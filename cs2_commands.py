"""Slash commands /cs * pour Counter-Strike 2.

Couvre :
- /cs link        : associer un compte Steam ou Faceit a son Discord
- /cs unlink      : retirer un lien
- /cs stats       : afficher un profil (steam et/ou faceit)
- /cs setrank     : declarer son Premier ELO (0-40000) -> applique rank role
- /cs rankrole    : admin, active/desactive l'attribution auto des rank roles
- /cs price       : prix Steam Market d'un skin
- /cs inventory   : inventaire CS2 d'un user + valeur totale EUR
- /cs queue       : cree un voice channel temporaire pour stack Premier
- /cs map         : ban/pick maps tour-par-tour selon membres du voice
- /cs loadout     : generateur de loadout aleatoire

Securite :
- Aucune cle API n'est loggee ou renvoyee a l'utilisateur.
- Validation regex stricte sur input (steam_id, faceit nickname, skin name).
- Erreurs serveur -> message generique, details dans logs cote serveur.
"""
from __future__ import annotations

import asyncio
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import cs2_api as csapi
from database import (
    cs_profile_get, cs_profile_upsert, cs_profile_unlink,
    cs_rank_config_get, cs_rank_config_upsert,
    cs_queue_lobby_add, cs_queue_lobby_delete, cs_queue_lobbies_list,
    cs_cache_get, cs_cache_set,
)


# ============================================================================
# Helpers presentation
# ============================================================================

CS2_MAP_POOL = ["Mirage", "Inferno", "Nuke", "Anubis", "Ancient", "Dust2", "Train"]

WEAPON_POOL = {
    "Pistolet":  ["USP-S", "Glock-18", "P250", "Tec-9", "Five-SeveN", "Desert Eagle", "CZ75-Auto"],
    "Fusil":     ["AK-47", "M4A4", "M4A1-S", "AUG", "SG 553", "FAMAS", "Galil AR"],
    "Sniper":    ["AWP", "SSG 08", "SCAR-20", "G3SG1"],
    "SMG":       ["MP9", "MAC-10", "MP7", "P90", "UMP-45", "PP-Bizon", "MP5-SD"],
    "Lourd":     ["Nova", "XM1014", "MAG-7", "Sawed-Off", "M249", "Negev"],
}
SKIN_POOL_GENERIC = [
    "Redline", "Asiimov", "Hyper Beast", "Neon Rider", "Phantom Disruptor",
    "Dragon Lore", "Howl", "Fire Serpent", "Vulcan", "Bloodsport", "Wild Lotus",
    "Printstream", "Cyrex", "Gungnir", "Hyper Beast", "Fade", "Wildfire",
    "Empress", "Aquamarine Revenge", "Slingshot", "Frontside Misty",
]
KNIVES = ["Karambit", "M9 Bayonet", "Butterfly Knife", "Talon Knife", "Stiletto Knife",
          "Skeleton Knife", "Bowie Knife", "Falchion Knife", "Huntsman Knife"]
KNIFE_FINISHES = ["Doppler", "Marble Fade", "Fade", "Slaughter", "Tiger Tooth",
                  "Crimson Web", "Lore", "Gamma Doppler", "Damascus Steel",
                  "Autotronic", "Black Laminate"]
GLOVES = ["Sport Gloves", "Driver Gloves", "Specialist Gloves", "Hand Wraps",
          "Bloodhound Gloves", "Moto Gloves", "Hydra Gloves", "Broken Fang Gloves"]
GLOVES_FINISHES = ["Pandora's Box", "Vice", "King Snake", "Crimson Kimono",
                   "Snakebite", "Slaughter", "Imperial Plaid", "Amphibious"]
WEAR_LEVELS = ["Battle-Scarred", "Well-Worn", "Field-Tested", "Minimal Wear", "Factory New"]
WEAR_LEVELS_ORDER = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]
WEAR_LABEL_FR = {
    "Factory New":    "Neuve",
    "Minimal Wear":   "Peu usée",
    "Field-Tested":   "Testée",
    "Well-Worn":      "Bien usée",
    "Battle-Scarred": "Vétérante",
}

# Liste plate de TOUTES les armes pour autocomplete /cs price.
# Couteaux et gants portent le prefixe '★' qui doit etre conserve dans
# market_hash_name. StatTrak peut etre rajoute par l'utilisateur.
PRICE_WEAPONS = [
    # Pistols
    "USP-S", "Glock-18", "P250", "Tec-9", "Five-SeveN", "Desert Eagle",
    "CZ75-Auto", "Dual Berettas", "P2000", "R8 Revolver",
    # SMGs
    "MP9", "MAC-10", "MP7", "P90", "UMP-45", "PP-Bizon", "MP5-SD",
    # Rifles
    "AK-47", "M4A4", "M4A1-S", "AUG", "SG 553", "FAMAS", "Galil AR",
    # Snipers
    "AWP", "SSG 08", "SCAR-20", "G3SG1",
    # Heavy
    "Nova", "XM1014", "MAG-7", "Sawed-Off", "M249", "Negev",
    # Knives (avec etoile)
    "★ Karambit", "★ M9 Bayonet", "★ Butterfly Knife", "★ Talon Knife",
    "★ Stiletto Knife", "★ Skeleton Knife", "★ Bowie Knife",
    "★ Falchion Knife", "★ Huntsman Knife", "★ Flip Knife", "★ Gut Knife",
    "★ Shadow Daggers", "★ Bayonet", "★ Ursus Knife", "★ Navaja Knife",
    "★ Classic Knife", "★ Paracord Knife", "★ Survival Knife", "★ Nomad Knife",
    # Gloves (avec etoile)
    "★ Sport Gloves", "★ Driver Gloves", "★ Specialist Gloves",
    "★ Hand Wraps", "★ Bloodhound Gloves", "★ Moto Gloves",
    "★ Hydra Gloves", "★ Broken Fang Gloves",
]


def _build_market_hash_name(arme: str, skin: str, wear: Optional[str], stattrak: bool) -> str:
    """Construit le market_hash_name Steam selon les conventions :
      - Normal      : 'AK-47 | Redline (Field-Tested)'
      - StatTrak    : 'StatTrak™ AK-47 | Redline (Field-Tested)'
      - Couteau     : '★ Karambit | Doppler (Factory New)'
      - Couteau StT : '★ StatTrak™ Karambit | Doppler (Factory New)'
      - Gants       : '★ Sport Gloves | Pandora\'s Box (Field-Tested)'  (pas de StatTrak)
    """
    skin = (skin or "").strip()
    is_special = arme.startswith("★")
    is_gloves  = is_special and ("Gloves" in arme or "Hand Wraps" in arme)
    parts = []
    if is_special:
        weapon_clean = arme.replace("★", "").strip()
        if stattrak and not is_gloves:
            parts.append(f"★ StatTrak™ {weapon_clean}")
        else:
            parts.append(f"★ {weapon_clean}")
    else:
        if stattrak:
            parts.append(f"StatTrak™ {arme}")
        else:
            parts.append(arme)
    name = f"{parts[0]} | {skin}"
    if wear:
        name += f" ({wear})"
    return name


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except Exception:
        return str(n)


def bar(pct: float, width: int = 14, filled="▰", empty="▱") -> str:
    """Unicode progress bar."""
    pct = max(0.0, min(1.0, pct or 0))
    fill = int(pct * width)
    return filled * fill + empty * (width - fill)


def _err_embed(title: str, msg: str) -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=msg, color=0xE74C3C)


def _info_embed(title: str, msg: str, color: int = 0x3498DB) -> discord.Embed:
    return discord.Embed(title=title, description=msg, color=color)


# ============================================================================
# /cs link
# ============================================================================

async def _build_steam_link(interaction: discord.Interaction, raw: str) -> Optional[str]:
    steam_id = await csapi.steam_resolve(raw)
    if not steam_id:
        await interaction.followup.send(
            embed=_err_embed("ID Steam invalide ou introuvable",
                "Formats acceptés :\n"
                "• URL custom : `https://steamcommunity.com/id/yuminiteru/`\n"
                "• URL numérique : `https://steamcommunity.com/profiles/76561198xxxxxxxxx`\n"
                "• Pseudo custom seul : `yuminiteru`\n"
                "• SteamID64 seul : `76561198xxxxxxxxx` (17 chiffres)\n\n"
                "Astuce : tu peux copier-coller l'URL de ton profil Steam directement."),
            ephemeral=True,
        )
        return None
    summary = await csapi.steam_player_summary(steam_id)
    if not summary:
        await interaction.followup.send(
            embed=_err_embed("Profil Steam introuvable",
                "Steam n'a renvoyé aucun profil pour cet ID. Vérifie que l'ID est bon et que le profil n'est pas supprimé."),
            ephemeral=True,
        )
        return None
    cs_profile_upsert(interaction.user.id, steam_id=steam_id)
    persona = summary.get("personaname") or steam_id
    await interaction.followup.send(
        embed=_info_embed("✅ Steam lié",
            f"Compte Steam **{persona}** lié à ton Discord.\n"
            f"`steamID64` : `{steam_id}`\n\n"
            f"Tu peux maintenant utiliser `/cs stats` et `/cs inventory`.",
            color=0x2ECC71),
        ephemeral=True,
    )
    return steam_id


async def _build_faceit_link(interaction: discord.Interaction, nickname: str) -> Optional[str]:
    if not (2 <= len(nickname) <= 30) or not all(ch.isalnum() or ch in "_-." for ch in nickname):
        await interaction.followup.send(
            embed=_err_embed("Pseudo Faceit invalide",
                "Le pseudo doit faire 2-30 caractères alphanumériques (underscore et tiret OK)."),
            ephemeral=True,
        )
        return None
    fc = await csapi.faceit_player_by_nickname(nickname)
    if not fc or not fc.get("player_id"):
        await interaction.followup.send(
            embed=_err_embed("Faceit user introuvable",
                f"Aucun joueur Faceit trouvé pour le pseudo `{nickname}`. "
                "Vérifie l'orthographe (sensible à la casse)."),
            ephemeral=True,
        )
        return None
    pid  = fc["player_id"]
    nick = fc.get("nickname") or nickname
    cs_profile_upsert(interaction.user.id, faceit_id=pid, faceit_nick=nick)
    skill = (fc.get("games", {}) or {}).get("cs2", {}).get("skill_level") or "—"
    await interaction.followup.send(
        embed=_info_embed("✅ Faceit lié",
            f"Compte Faceit **{nick}** lié à ton Discord.\nNiveau Faceit CS2 : **{skill}**",
            color=0x2ECC71),
        ephemeral=True,
    )
    return pid


# ============================================================================
# Embeds /cs stats
# ============================================================================

async def _build_steam_stats_embed(member, steam_id: str) -> discord.Embed:
    summary = await csapi.steam_player_summary(steam_id)
    stats   = await csapi.steam_cs2_stats(steam_id)
    owned   = await csapi.steam_owned_cs2(steam_id)
    persona = (summary or {}).get("personaname", "Inconnu")
    avatar  = (summary or {}).get("avatarfull")

    if stats and stats.get("_private"):
        e = discord.Embed(
            title=f"🎯 Stats Steam — {persona}",
            description=("⚠️ **Profil ou jeu CS2 en privé.**\nActive le partage des stats CS2 : "
                         "Steam → Profile → Edit Profile → **Privacy Settings** → "
                         "*Game details* + *My profile* → **Public**."),
            color=0xE67E22,
        )
        if avatar:
            e.set_thumbnail(url=avatar)
        return e
    if stats is None:
        e = discord.Embed(
            title=f"🎯 Stats Steam — {persona}",
            description="⚠️ Impossible de récupérer les stats (Steam API indispo). Réessaie plus tard.",
            color=0xE67E22,
        )
        if avatar:
            e.set_thumbnail(url=avatar)
        return e

    kills    = stats.get("total_kills", 0) or 0
    deaths   = stats.get("total_deaths", 0) or 0
    hs       = stats.get("total_kills_headshot", 0) or 0
    shots    = stats.get("total_shots_fired", 0) or 0
    hits     = stats.get("total_shots_hit", 0) or 0
    mvps     = stats.get("total_mvps", 0) or 0
    wins     = stats.get("total_wins", 0) or 0
    rounds   = stats.get("total_rounds_played", 0) or 0
    matches  = stats.get("total_matches_played", 0) or 0
    matchs_w = stats.get("total_matches_won", 0) or 0
    bombs_p  = stats.get("total_planted_bombs", 0) or 0
    bombs_d  = stats.get("total_defused_bombs", 0) or 0
    money    = stats.get("total_money_earned", 0) or 0
    hours    = (owned or {}).get("playtime_forever", 0) // 60 if owned else 0

    kd      = (kills / deaths) if deaths else float(kills)
    hs_pct  = (hs / kills) if kills else 0
    acc_pct = (hits / shots) if shots else 0
    wr_pct  = (matchs_w / matches) if matches else 0

    color = 0x1A9FFF
    embed = discord.Embed(
        title=f"🎯 Profil Steam · {persona}",
        url=f"https://steamcommunity.com/profiles/{steam_id}",
        color=color,
    )
    if avatar:
        embed.set_thumbnail(url=avatar)

    embed.add_field(
        name="⚔️ Performance",
        value=(
            f"**Kills** : `{fmt_int(kills)}`\n"
            f"**Deaths** : `{fmt_int(deaths)}`\n"
            f"**K/D** : `{kd:.2f}`\n"
            f"**MVPs** : `{fmt_int(mvps)}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🎯 Précision",
        value=(
            f"**Headshots** : `{fmt_int(hs)}`\n"
            f"**HS%** : `{hs_pct*100:.1f}%`\n"
            f"{bar(hs_pct)}\n"
            f"**Précision** : `{acc_pct*100:.1f}%`\n"
            f"{bar(acc_pct)}"
        ),
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=False)

    embed.add_field(
        name="🏆 Matchs",
        value=(
            f"**Joués** : `{fmt_int(matches)}`\n"
            f"**Gagnés** : `{fmt_int(matchs_w)}`\n"
            f"**Win Rate** : `{wr_pct*100:.1f}%`\n"
            f"{bar(wr_pct)}"
        ),
        inline=True,
    )
    embed.add_field(
        name="💣 Rounds & Bombes",
        value=(
            f"**Rounds** : `{fmt_int(rounds)}`\n"
            f"**Wins (rounds)** : `{fmt_int(wins)}`\n"
            f"**Bombes plantées** : `{fmt_int(bombs_p)}`\n"
            f"**Désamorcées** : `{fmt_int(bombs_d)}`"
        ),
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=False)

    embed.add_field(
        name="💰 Économie & temps",
        value=(
            f"**Argent gagné** : `{fmt_int(money)} $`\n"
            f"**Heures CS2 (2 sem.)** : `{(owned or {}).get('playtime_2weeks', 0) // 60} h`\n"
            f"**Heures CS2 totales** : `{hours} h`"
        ),
        inline=False,
    )
    embed.set_footer(text=f"steamID64 : {steam_id} · données Steam Web API")
    return embed


async def _build_faceit_stats_embed(member, faceit_id: str, faceit_nick: str) -> discord.Embed:
    fc      = await csapi.faceit_player_stats(faceit_id, "cs2")
    if not fc:
        return discord.Embed(
            title=f"🎯 Faceit — {faceit_nick}",
            description="⚠️ Stats Faceit indispos pour ce joueur.",
            color=0xE67E22,
        )
    lifetime = fc.get("lifetime", {}) or {}
    matches  = int(lifetime.get("Matches", 0) or 0)
    wins     = int(lifetime.get("Wins", 0) or 0)
    wr       = float(lifetime.get("Win Rate %", 0) or 0) / 100
    kdr      = float(lifetime.get("Average K/D Ratio", 0) or 0)
    hs_pct   = float(lifetime.get("Average Headshots %", 0) or 0) / 100
    longest_streak = lifetime.get("Longest Win Streak", "?")
    cur_streak     = lifetime.get("Current Win Streak", "?")

    embed = discord.Embed(
        title=f"🎯 Profil Faceit · {faceit_nick}",
        url=f"https://www.faceit.com/fr/players/{faceit_nick}",
        color=0xFF5500,
    )
    embed.add_field(
        name="📊 Lifetime",
        value=(
            f"**Matchs** : `{fmt_int(matches)}`\n"
            f"**Wins** : `{fmt_int(wins)}`\n"
            f"**Win Rate** : `{wr*100:.1f}%`\n"
            f"{bar(wr)}"
        ),
        inline=True,
    )
    embed.add_field(
        name="⚔️ Combat",
        value=(
            f"**K/D ratio** : `{kdr:.2f}`\n"
            f"**HS%** : `{hs_pct*100:.1f}%`\n"
            f"{bar(hs_pct)}\n"
            f"**Win streak (max)** : `{longest_streak}`\n"
            f"**Win streak (cur.)** : `{cur_streak}`"
        ),
        inline=True,
    )
    # Stats par map (top 5 par matchs joues)
    segments = fc.get("segments") or []
    maps_rows = []
    for seg in segments:
        if (seg.get("type") or "").lower() != "map":
            continue
        s = seg.get("stats", {})
        maps_rows.append({
            "map":     seg.get("label"),
            "matches": int(s.get("Matches", 0) or 0),
            "wr":      float(s.get("Win Rate %", 0) or 0),
            "kd":      float(s.get("Average K/D Ratio", 0) or 0),
        })
    if maps_rows:
        maps_rows.sort(key=lambda r: -r["matches"])
        lines = []
        for r in maps_rows[:5]:
            lines.append(f"`{r['map']:<10}` · {r['matches']:>3}m · WR `{r['wr']:.0f}%` · K/D `{r['kd']:.2f}`")
        embed.add_field(name="🗺️ Top maps", value="\n".join(lines), inline=False)

    embed.set_footer(text="Données Faceit Open API")
    return embed


# ============================================================================
# /cs price
# ============================================================================

async def _build_price_embed(name: str) -> discord.Embed:
    """Embed prix pour UN market_hash_name precis."""
    cache_key = f"price:{name.lower()}"
    cached = cs_cache_get(cache_key, max_age_sec=600)
    data = cached or await csapi.steam_market_price(name, currency=3)
    if not cached and data:
        cs_cache_set(cache_key, data)
    if not data:
        return _err_embed(
            "Skin introuvable",
            "Aucun résultat sur Steam Market pour ce skin/usure.\n"
            f"Recherche tentée : `{name}`\n\n"
            "Cause probable : combinaison arme/skin/usure inexistante (certains skins n'existent pas en toutes les usures).",
        )
    encoded = name.replace(" ", "%20").replace("|", "%7C")
    embed = discord.Embed(
        title=f"💸 {name}",
        description=(
            f"**Prix le plus bas** : `{data.get('lowest_price') or 'n/a'}`\n"
            f"**Prix médian (24 h)** : `{data.get('median_price') or 'n/a'}`\n"
            f"**Volume échangé** : `{data.get('volume') or 0}`"
        ),
        url=f"https://steamcommunity.com/market/listings/730/{encoded}",
        color=0xF1C40F,
    )
    embed.set_footer(text="Source : Steam Community Market")
    return embed


async def _build_price_embed_all_wears(arme: str, skin: str, stattrak: bool) -> discord.Embed:
    """Lance 5 lookups (une par wear) et affiche tableau condense."""
    rows = []
    for wear in WEAR_LEVELS_ORDER:
        name = _build_market_hash_name(arme, skin, wear, stattrak)
        cache_key = f"price:{name.lower()}"
        cached = cs_cache_get(cache_key, max_age_sec=600)
        data = cached or await csapi.steam_market_price(name, currency=3)
        if not cached and data:
            cs_cache_set(cache_key, data)
        if not cached:
            await asyncio.sleep(0.4)   # rate-limit Steam Market
        low = (data or {}).get("lowest_price") if data else None
        rows.append((wear, low))

    found = [r for r in rows if r[1]]
    base_name = _build_market_hash_name(arme, skin, None, stattrak)
    if not found:
        return _err_embed(
            "Skin introuvable",
            f"Aucun prix Steam Market pour **{base_name}** sur toutes les usures.\n\n"
            "Vérifie l'orthographe du skin (sensible à la casse, ex: `Redline`, `Hyper Beast`, `Asiimov`).\n"
            "Certains skins anciens n'existent pas en toutes usures.",
        )

    lines = []
    for wear, price in rows:
        label = WEAR_LABEL_FR.get(wear, wear)
        val   = f"`{price}`" if price else "`—`"
        lines.append(f"**{label:<10}** {val}")

    embed = discord.Embed(
        title=f"💸 {base_name}",
        description="**Prix Steam Market par niveau d'usure :**\n\n" + "\n".join(lines),
        color=0xF1C40F,
    )
    embed.set_footer(text="Source : Steam Community Market · Prix les plus bas")
    return embed


# ─── Autocomplete pour /cs price arme ────────────────────────────────────────
async def _arme_autocomplete(interaction: discord.Interaction, current: str):
    cur = (current or "").lower()
    matches = [w for w in PRICE_WEAPONS if cur in w.lower()][:25]
    return [app_commands.Choice(name=w, value=w) for w in matches]


# ─── Autocomplete pour /cs price skin ────────────────────────────────────────
async def _skin_autocomplete(interaction: discord.Interaction, current: str):
    # On lit l'arme deja saisie pour cibler la recherche
    try:
        arme = (interaction.namespace.arme or "").strip()
    except Exception:
        arme = ""
    cur = (current or "").strip()
    if not arme:
        return [app_commands.Choice(name="⚠️ Choisis d'abord une arme", value=cur or " ")]
    weapon_for_query = arme.replace("★", "").strip()
    query = f"{weapon_for_query} {cur}".strip()
    cache_key = f"skin_ac:{query.lower()}"
    cached = cs_cache_get(cache_key, max_age_sec=3600)
    if cached:
        skins = cached
    else:
        results = await csapi.steam_market_search(query, count=40)
        if not results:
            return [app_commands.Choice(name="(aucun résultat — vérifie l'orthographe)", value=cur or " ")]
        skins_set = set()
        for hash_name in results:
            # Ignore les items qui ne contiennent pas " | " (ex: cles, agents)
            if " | " not in hash_name:
                continue
            # Filtre uniquement les items qui ont vraiment l'arme cherchee dans le prefixe
            prefix, after_pipe = hash_name.split(" | ", 1)
            if weapon_for_query.lower() not in prefix.lower():
                continue
            # Skin = partie entre " | " et " (Wear)"
            if " (" in after_pipe:
                skin = after_pipe.rsplit(" (", 1)[0].strip()
            else:
                skin = after_pipe.strip()
            if skin:
                skins_set.add(skin)
        skins = sorted(skins_set)[:25]
        if skins:
            cs_cache_set(cache_key, skins)
    if not skins:
        return [app_commands.Choice(name="(aucun skin trouvé)", value=cur or " ")]
    return [app_commands.Choice(name=s[:100], value=s[:100]) for s in skins[:25]]


# ============================================================================
# /cs inventory
# ============================================================================

# Type d'item ignores pour la valeur (ne sont pas marketable ou pas relevant pour le total)
_IGNORE_TYPES = {"Conteneur de munitions", "Graffiti", "Pass-temps"}


async def _build_inventory_embed(member: discord.abc.User, steam_id: str) -> discord.Embed:
    items = await csapi.steam_inventory(steam_id)
    if items is None:
        return _err_embed(
            "Inventaire inaccessible",
            f"L'inventaire CS2 de **{member.display_name}** n'est pas accessible publiquement.\n\n"
            "**Va sur** https://steamcommunity.com/my/edit/settings **et passe en `Public` les 3 réglages suivants :**\n"
            "1️⃣ **Mon profil**\n"
            "2️⃣ **Détails du jeu** *(souvent oublié — obligatoire pour CS2 !)*\n"
            "3️⃣ **Inventaire**\n\n"
            "Autres causes possibles : Steam en rate-limit (retry 2-5 min) ou compte VAC/trade-ban.\n\n"
            f"_Test direct : https://steamcommunity.com/profiles/{steam_id}/inventory/#730_",
        )
    if not items:
        return _info_embed(
            f"📦 Inventaire CS2 · {member.display_name}",
            "_Inventaire vide ou aucun objet CS2._",
            color=0x95A5A6,
        )

    # Aggregation : count par market_hash_name
    counts: dict[str, int] = {}
    for it in items:
        if not it.get("marketable"):
            continue
        if it.get("type") in _IGNORE_TYPES:
            continue
        counts[it["name"]] = counts.get(it["name"], 0) + 1

    if not counts:
        return _info_embed(
            f"📦 Inventaire CS2 · {member.display_name}",
            "_Aucun objet marketable trouvé._",
            color=0x95A5A6,
        )

    total_eur = 0.0
    valued    = []
    # Limite a 60 lookups pour respecter le rate-limit Steam Market
    names = list(counts.keys())[:60]
    for name in names:
        ck = f"price:{name.lower()}"
        cached = cs_cache_get(ck, max_age_sec=900)
        price_data = cached or await csapi.steam_market_price(name, currency=3)
        if not cached and price_data:
            cs_cache_set(ck, price_data)
        eur = csapi._parse_price_eur((price_data or {}).get("lowest_price"))
        qty = counts[name]
        if eur is not None:
            total_eur += eur * qty
            valued.append((name, qty, eur))
        # Tres petit sleep pour eviter rate-limit Steam Market (~2s par requete max conseille)
        await asyncio.sleep(0.4)

    # Top 8 items par valeur totale (qty * unit)
    valued.sort(key=lambda r: -(r[2] * r[1]))
    top_lines = []
    for name, qty, eur in valued[:8]:
        sub = eur * qty
        top_lines.append(f"`x{qty}` **{name}** — {sub:.2f}€ (`{eur:.2f}€/u`)")

    embed = discord.Embed(
        title=f"📦 Inventaire CS2 · {member.display_name}",
        description=(
            f"**Items marketables** : `{sum(counts.values())}`\n"
            f"**Items uniques** : `{len(counts)}`\n"
            f"**Valeur totale estimée** : **{total_eur:.2f} €**\n"
            + (f"_Limité aux 60 items uniques les plus présents._\n" if len(counts) > 60 else "")
        ),
        url=f"https://steamcommunity.com/profiles/{steam_id}/inventory/#730",
        color=0x9B59B6,
    )
    if top_lines:
        embed.add_field(name="💎 Top items", value="\n".join(top_lines), inline=False)
    embed.set_footer(text="Prix basés sur la dernière offre Steam Market (lowest_price)")
    return embed


# ============================================================================
# /cs queue : voice channel temporaire 5 slots
# ============================================================================

async def _create_queue_lobby(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    guild = interaction.guild
    if guild is None:
        return None
    # Categorie : meme que le salon ou la commande est faite, sinon racine
    category = interaction.channel.category if interaction.channel and hasattr(interaction.channel, "category") else None
    name = f"🎮 Premier Queue · {interaction.user.display_name}"
    try:
        vc = await guild.create_voice_channel(
            name=name[:100],
            user_limit=5,
            category=category,
            reason=f"CS2 queue created by {interaction.user} ({interaction.user.id})",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            embed=_err_embed("Permission manquante",
                "Le bot doit avoir la permission **Gérer les salons** pour créer un voice channel."),
            ephemeral=True,
        )
        return None
    except Exception as e:
        print(f"[cs2/queue] create err: {type(e).__name__}")
        await interaction.followup.send(
            embed=_err_embed("Erreur",
                "Impossible de créer le voice channel. Réessaie plus tard."),
            ephemeral=True,
        )
        return None
    cs_queue_lobby_add(vc.id, guild.id, interaction.user.id)
    return vc


async def on_voice_state_update(member, before, after, bot):
    """Hook a brancher dans bot.py : supprime auto les voice channels de queue
    qui se vident."""
    if not before.channel:
        return
    if member.bot:
        # Si c'est le bot lui-meme qui change de canal, on ignore
        return
    ch = before.channel
    lobbies = {l["channel_id"]: l for l in cs_queue_lobbies_list(ch.guild.id)}
    if str(ch.id) not in lobbies:
        return
    # Verifie si vide (en excluant les bots)
    remaining = [m for m in ch.members if not m.bot]
    if remaining:
        return
    try:
        await ch.delete(reason="CS2 queue empty, auto-cleanup")
    except Exception as e:
        print(f"[cs2/queue] auto-delete err: {type(e).__name__}")
    cs_queue_lobby_delete(ch.id)


# ============================================================================
# /cs map : ban/pick tour-par-tour selon membres voice channel
# ============================================================================

class MapBanView(discord.ui.View):
    def __init__(self, voters: list, maps: list[str], event: asyncio.Event):
        super().__init__(timeout=240)
        self.voters = voters
        self.maps   = list(maps)
        self.event  = event
        self.idx    = 0      # voter tour
        self.bans   = []     # (member, map_banned)
        self._refresh_buttons()

    @property
    def current_voter(self):
        return self.voters[self.idx % len(self.voters)]

    def _refresh_buttons(self):
        self.clear_items()
        for i, mp in enumerate(self.maps):
            btn = discord.ui.Button(label=f"🗺️ {mp}",
                                    style=discord.ButtonStyle.danger,
                                    row=i // 4,
                                    custom_id=f"mapban_{mp}_{self.idx}")
            async def cb(interaction: discord.Interaction, picked=mp):
                # Filter : seul le voter courant
                if interaction.user.id != self.current_voter.id:
                    try:
                        await interaction.response.send_message(
                            f"❌ C'est au tour de **{self.current_voter.display_name}** de ban.",
                            ephemeral=True,
                        )
                    except Exception:
                        pass
                    return
                self.bans.append((self.current_voter, picked))
                self.maps.remove(picked)
                self.idx += 1
                if len(self.maps) <= 1:
                    # Termine : 1 map restante
                    self.event.set()
                    try:
                        await interaction.response.defer()
                    except Exception:
                        pass
                    return
                self._refresh_buttons()
                try:
                    await interaction.response.edit_message(
                        embed=self._make_embed(), view=self,
                    )
                except Exception:
                    pass
            btn.callback = cb
            self.add_item(btn)

    def _make_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🗺️ Ban/Pick — Counter-Strike 2",
            description=(
                f"**À ton tour, {self.current_voter.mention} !**\n"
                "Clique sur la map que tu veux **bannir**.\n\n"
                f"**Voteurs** : {', '.join(v.display_name for v in self.voters)}"
            ),
            color=0xE67E22,
        )
        if self.bans:
            lines = [f"❌ **{m}** ban par *{v.display_name}*" for v, m in self.bans]
            embed.add_field(name="Bans", value="\n".join(lines), inline=False)
        embed.add_field(name="Maps restantes", value=" · ".join(f"`{m}`" for m in self.maps), inline=False)
        embed.set_footer(text="240 s pour finir le ban-pick. La dernière map restante sera jouée.")
        return embed


async def _run_map_ban(interaction: discord.Interaction):
    user = interaction.user
    vstate = user.voice if hasattr(user, "voice") else None
    if not vstate or not vstate.channel:
        await interaction.followup.send(
            embed=_err_embed("Pas en vocal",
                "Tu dois être dans un voice channel pour lancer un ban/pick. "
                "Les voteurs sont les membres de ton canal vocal."),
            ephemeral=True,
        )
        return
    voters = [m for m in vstate.channel.members if not m.bot]
    if len(voters) < 2:
        await interaction.followup.send(
            embed=_err_embed("Pas assez de voteurs",
                "Au moins 2 personnes doivent être dans le voice channel pour faire un ban/pick."),
            ephemeral=True,
        )
        return

    event = asyncio.Event()
    view  = MapBanView(voters, CS2_MAP_POOL, event)
    msg   = await interaction.followup.send(embed=view._make_embed(), view=view)
    try:
        await asyncio.wait_for(event.wait(), timeout=240)
    except asyncio.TimeoutError:
        try:
            await msg.edit(embed=_err_embed("Temps écoulé",
                "Le ban-pick a expiré. Relance la commande."), view=None)
        except Exception:
            pass
        return
    final_map = view.maps[0] if view.maps else "?"
    embed = discord.Embed(
        title=f"🏆 Map sélectionnée : **{final_map}**",
        description="Bon GG ! 🎮\n\n**Récap des bans :**\n" +
                    "\n".join(f"❌ {m} ban par *{v.display_name}*" for v, m in view.bans),
        color=0x2ECC71,
    )
    try:
        await msg.edit(embed=embed, view=None)
    except Exception:
        pass


# ============================================================================
# /cs loadout
# ============================================================================

def _build_loadout_embed() -> discord.Embed:
    loadout = {}
    for slot, weapons in WEAPON_POOL.items():
        weapon = random.choice(weapons)
        skin   = random.choice(SKIN_POOL_GENERIC)
        wear   = random.choice(WEAR_LEVELS)
        loadout[slot] = f"**{weapon}** | _{skin}_ ({wear})"
    knife = f"**★ {random.choice(KNIVES)}** | _{random.choice(KNIFE_FINISHES)}_ ({random.choice(WEAR_LEVELS)})"
    gloves = f"**★ {random.choice(GLOVES)}** | _{random.choice(GLOVES_FINISHES)}_"

    embed = discord.Embed(
        title="🎒 Ton loadout aléatoire",
        description="Configuration fantaisie pour CS2 — pour de vrai, à toi de l'acheter 😉",
        color=0x16A085,
    )
    for slot, val in loadout.items():
        embed.add_field(name=slot, value=val, inline=False)
    embed.add_field(name="🔪 Couteau", value=knife, inline=False)
    embed.add_field(name="🧤 Gants",   value=gloves, inline=False)
    embed.set_footer(text="Skins fictifs — purement aléatoire")
    return embed


# ============================================================================
# Rank role helper (appele depuis /cs setrank + admin toggle)
# ============================================================================

_TIER_TO_ROLE_FIELD = {
    "grey": "role_grey",
    "lightblue": "role_lightblue",
    "blue": "role_blue",
    "purple": "role_purple",
    "pink": "role_pink",
    "red": "role_red",
    "gold": "role_gold",
}


async def _apply_rank_role(member: discord.Member, elo: int) -> Optional[str]:
    """Applique le role correspondant au palier elo. Retire les autres rank
    roles. Retourne label tier ou None si feature off / config incomplete."""
    if not member.guild:
        return None
    cfg = cs_rank_config_get(member.guild.id)
    if not cfg.get("enabled"):
        return None
    code, label, _color = csapi.premier_tier(elo)
    if not code:
        return None
    target_field    = _TIER_TO_ROLE_FIELD[code]
    target_role_id  = cfg.get(target_field)
    # Retire tous les autres rank roles de l'user
    all_role_ids = [cfg.get(f) for f in _TIER_TO_ROLE_FIELD.values() if cfg.get(f)]
    to_remove = [r for r in member.roles if str(r.id) in all_role_ids and (not target_role_id or str(r.id) != str(target_role_id))]
    if to_remove:
        try:
            await member.remove_roles(*to_remove, reason="CS2 rank role update")
        except Exception as e:
            print(f"[cs2/rank] remove_roles err: {type(e).__name__}")
    if target_role_id:
        role = member.guild.get_role(int(target_role_id))
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason=f"CS2 rank {code} (elo {elo})")
            except Exception as e:
                print(f"[cs2/rank] add_role err: {type(e).__name__}")
    return label


# ============================================================================
# Setup : groupe /cs et commandes
# ============================================================================

def setup_cs2_commands(bot: commands.Bot):
    cs_group = app_commands.Group(name="cs", description="Commandes Counter-Strike 2")

    # ---------- /cs link ----------
    @cs_group.command(name="link", description="Lie ton compte Steam ou Faceit à ton Discord")
    @app_commands.describe(
        plateforme="Choisis la plateforme à lier",
        identifiant="Steam : URL https://steamcommunity.com/id/<pseudo>/ ou /profiles/<id>. Faceit : pseudo.",
    )
    @app_commands.choices(plateforme=[
        app_commands.Choice(name="Steam",  value="steam"),
        app_commands.Choice(name="Faceit", value="faceit"),
    ])
    async def cs_link(interaction: discord.Interaction,
                      plateforme: app_commands.Choice[str], identifiant: str):
        await interaction.response.defer(ephemeral=True)
        if plateforme.value == "steam":
            await _build_steam_link(interaction, identifiant)
        else:
            await _build_faceit_link(interaction, identifiant)

    # ---------- /cs unlink ----------
    @cs_group.command(name="unlink", description="Retire un lien Steam, Faceit, ou les deux")
    @app_commands.choices(plateforme=[
        app_commands.Choice(name="Steam",  value="steam"),
        app_commands.Choice(name="Faceit", value="faceit"),
        app_commands.Choice(name="Tout",   value="all"),
    ])
    async def cs_unlink(interaction: discord.Interaction,
                        plateforme: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        cs_profile_unlink(interaction.user.id, plateforme.value)
        await interaction.followup.send(
            embed=_info_embed("✅ Lien retiré",
                f"Lien **{plateforme.name}** supprimé.",
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /cs stats ----------
    @cs_group.command(name="stats", description="Affiche les stats CS2 d'un joueur (toi par défaut)")
    @app_commands.describe(membre="Membre Discord à inspecter (optionnel)")
    async def cs_stats(interaction: discord.Interaction,
                       membre: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = membre or interaction.user
        prof = cs_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed(
                    "Aucun compte lié",
                    f"**{target.display_name}** n'a pas de compte CS2 lié.\n"
                    "Fais `/cs link` pour lier ton compte."),
                ephemeral=True,
            )
            return

        has_steam  = bool(prof.get("steam_id"))
        has_faceit = bool(prof.get("faceit_id"))
        if has_steam and has_faceit:
            view = _StatsProfileSelectView(target, prof)
            await interaction.followup.send(
                embed=_info_embed(
                    f"🎯 Stats — {target.display_name}",
                    "Tu as les deux profils liés, choisis lequel afficher :",
                    color=0x3498DB),
                view=view,
            )
            return
        if has_steam:
            embed = await _build_steam_stats_embed(target, prof["steam_id"])
            await interaction.followup.send(embed=embed)
            return
        if has_faceit:
            embed = await _build_faceit_stats_embed(target, prof["faceit_id"], prof.get("faceit_nick") or "?")
            await interaction.followup.send(embed=embed)
            return
        await interaction.followup.send(
            embed=_err_embed("Aucun profil utilisable",
                "Le profil DB est vide. Refais `/cs link`."),
            ephemeral=True,
        )

    # ---------- /cs setrank ----------
    @cs_group.command(name="setrank", description="Déclare ton Premier ELO CS2 (0–40000)")
    @app_commands.describe(elo="Ton rating Premier (visible en jeu)")
    async def cs_setrank(interaction: discord.Interaction, elo: app_commands.Range[int, 0, 40000]):
        await interaction.response.defer(ephemeral=True)
        cs_profile_upsert(interaction.user.id, premier_elo=elo)
        code, label, color = csapi.premier_tier(elo)
        applied_label = None
        if isinstance(interaction.user, discord.Member):
            applied_label = await _apply_rank_role(interaction.user, elo)
        msg = f"Rating Premier enregistré : **{elo}** — palier **{label}**."
        if applied_label:
            msg += f"\n✅ Role **{applied_label}** appliqué."
        await interaction.followup.send(
            embed=_info_embed("🎖️ Rank mis à jour", msg, color=color or 0x3498DB),
            ephemeral=True,
        )

    # ---------- /cs rankrole on|off ----------
    @cs_group.command(name="rankrole", description="Admin : active/désactive l'attribution auto des rank roles")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="Activer",     value="on"),
        app_commands.Choice(name="Désactiver",  value="off"),
    ])
    async def cs_rankrole(interaction: discord.Interaction,
                          action: app_commands.Choice[str]):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed("Pas dispo en DM", "Lance cette commande dans un serveur."),
                ephemeral=True,
            )
            return
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                embed=_err_embed("Permission refusée", "Tu as besoin de la permission **Gérer les rôles**."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        cs_rank_config_upsert(interaction.guild.id, enabled=(action.value == "on"))
        await interaction.followup.send(
            embed=_info_embed(
                "🎖️ Rank role config",
                f"L'attribution auto des rank roles est maintenant **{'activée' if action.value=='on' else 'désactivée'}** "
                f"sur ce serveur.\nConfigure les rôles par palier dans le dashboard (page Counter-Strike 2).",
                color=0x2ECC71 if action.value == "on" else 0xE67E22,
            ),
            ephemeral=True,
        )

    # ---------- /cs price ----------
    @cs_group.command(name="price", description="Prix Steam Market d'un skin (autocomplete arme + nom libre)")
    @app_commands.describe(
        arme="Arme (ou couteau ★, ou gants ★). Tape pour autocompléter.",
        skin="Nom du skin (ex: Redline, Asiimov, Hyper Beast)",
        usure="Apparence (optionnel — laisse vide pour voir les 5 niveaux)",
        stattrak="Variante StatTrak™ (compteur de kills) — défaut: non",
    )
    @app_commands.autocomplete(arme=_arme_autocomplete, skin=_skin_autocomplete)
    @app_commands.choices(usure=[
        app_commands.Choice(name="Neuve (Factory New)",          value="Factory New"),
        app_commands.Choice(name="Peu usée (Minimal Wear)",      value="Minimal Wear"),
        app_commands.Choice(name="Testée (Field-Tested)",        value="Field-Tested"),
        app_commands.Choice(name="Bien usée (Well-Worn)",        value="Well-Worn"),
        app_commands.Choice(name="Vétérante (Battle-Scarred)",   value="Battle-Scarred"),
    ])
    async def cs_price(interaction: discord.Interaction, arme: str, skin: str,
                       usure: Optional[app_commands.Choice[str]] = None,
                       stattrak: bool = False):
        if arme not in PRICE_WEAPONS:
            # Tolere si user a tapé sans autocomplete mais c'est dans la liste
            arme_match = next((w for w in PRICE_WEAPONS if w.lower() == arme.lower()), None)
            if not arme_match:
                await interaction.response.send_message(
                    embed=_err_embed(
                        "Arme inconnue",
                        f"`{arme}` n'est pas dans la liste reconnue.\n"
                        "Tape dans le champ pour voir les suggestions (AK-47, AWP, ★ Karambit, etc.)."),
                    ephemeral=True,
                )
                return
            arme = arme_match
        skin = (skin or "").strip()
        if not (2 <= len(skin) <= 60) or any(c in skin for c in "<>"):
            await interaction.response.send_message(
                embed=_err_embed("Skin invalide", "Le nom du skin doit faire 2-60 caractères."),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        if usure:
            name = _build_market_hash_name(arme, skin, usure.value, stattrak)
            embed = await _build_price_embed(name)
        else:
            embed = await _build_price_embed_all_wears(arme, skin, stattrak)
        await interaction.followup.send(embed=embed)

    # ---------- /cs inventory ----------
    @cs_group.command(name="inventory", description="Inventaire CS2 d'un membre ou d'un SteamID")
    @app_commands.describe(
        membre="Membre Discord (doit avoir lié son compte Steam)",
        steamid="Ou un SteamID64 / URL Steam directement",
    )
    async def cs_inventory(interaction: discord.Interaction,
                           membre: Optional[discord.Member] = None,
                           steamid: Optional[str] = None):
        await interaction.response.defer()
        if membre and steamid:
            await interaction.followup.send(
                embed=_err_embed("Param ambigu",
                    "Choisis **soit** `membre`, **soit** `steamid`, pas les deux."),
                ephemeral=True,
            )
            return
        steam_id = None
        display_name = None
        if steamid:
            steam_id = await csapi.steam_resolve(steamid)
            if not steam_id:
                await interaction.followup.send(
                    embed=_err_embed("ID Steam invalide ou introuvable",
                        "Formats acceptés : URL `https://steamcommunity.com/id/<pseudo>/`, "
                        "URL `/profiles/<id>`, pseudo seul, ou SteamID64 (17 chiffres)."),
                    ephemeral=True,
                )
                return
            summary = await csapi.steam_player_summary(steam_id)
            display_name = (summary or {}).get("personaname") or steam_id
            # Cree un faux objet user pour la presentation
            class _Stub:
                pass
            stub = _Stub()
            stub.display_name = display_name
            embed = await _build_inventory_embed(stub, steam_id)
            await interaction.followup.send(embed=embed)
            return
        target = membre or interaction.user
        prof   = cs_profile_get(target.id)
        if not prof or not prof.get("steam_id"):
            await interaction.followup.send(
                embed=_err_embed("Pas de Steam lié",
                    f"**{target.display_name}** n'a pas lié de compte Steam. "
                    "Fais `/cs link` plateforme `Steam`."),
                ephemeral=True,
            )
            return
        embed = await _build_inventory_embed(target, prof["steam_id"])
        await interaction.followup.send(embed=embed)

    # ---------- /cs queue ----------
    @cs_group.command(name="queue", description="Crée un voice channel temporaire pour Premier (5 slots)")
    async def cs_queue(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed("Pas dispo en DM", "Utilise cette commande dans un serveur."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        vc = await _create_queue_lobby(interaction)
        if vc is None:
            return
        await interaction.followup.send(
            embed=_info_embed(
                "🎮 Queue Premier créée",
                f"Voice channel **{vc.mention}** créé (5 slots).\n"
                "Il sera supprimé automatiquement quand il sera vide.",
                color=0x2ECC71,
            ),
            ephemeral=True,
        )

    # ---------- /cs map ----------
    @cs_group.command(name="map", description="Ban/pick tour-par-tour entre les membres du voice channel")
    async def cs_map(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed("Pas dispo en DM", "Utilise cette commande dans un serveur."),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _run_map_ban(interaction)

    # ---------- /cs loadout ----------
    @cs_group.command(name="loadout", description="Génère un loadout CS2 aléatoire")
    async def cs_loadout(interaction: discord.Interaction):
        await interaction.response.send_message(embed=_build_loadout_embed())

    bot.tree.add_command(cs_group)


# ============================================================================
# View interne : selection profil Steam/Faceit quand les 2 sont liés
# ============================================================================

class _StatsProfileSelectView(discord.ui.View):
    def __init__(self, target: discord.abc.User, prof: dict):
        super().__init__(timeout=60)
        self.target = target
        self.prof   = prof

        async def cb_steam(interaction: discord.Interaction):
            await interaction.response.defer()
            embed = await _build_steam_stats_embed(self.target, self.prof["steam_id"])
            try:
                await interaction.edit_original_response(embed=embed, view=None)
            except Exception:
                pass

        async def cb_faceit(interaction: discord.Interaction):
            await interaction.response.defer()
            embed = await _build_faceit_stats_embed(self.target,
                                                    self.prof["faceit_id"],
                                                    self.prof.get("faceit_nick") or "?")
            try:
                await interaction.edit_original_response(embed=embed, view=None)
            except Exception:
                pass

        b1 = discord.ui.Button(label="Steam",  style=discord.ButtonStyle.primary,
                               emoji="🎮", custom_id="cs_stats_steam")
        b1.callback = cb_steam
        b2 = discord.ui.Button(label="Faceit", style=discord.ButtonStyle.danger,
                               emoji="🔥", custom_id="cs_stats_faceit")
        b2.callback = cb_faceit
        self.add_item(b1)
        self.add_item(b2)
