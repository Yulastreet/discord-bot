"""Slash commands /cs * for Counter-Strike 2.

Covers:
- /cs link        : link a Steam or Faceit account to a Discord account
- /cs unlink      : remove a link
- /cs stats       : show a profile (steam and/or faceit)
- /cs setrank     : declare a Premier ELO (0-40000) -> applies the rank role
- /cs rankrole    : admin, enable/disable automatic rank role assignment
- /cs price       : Steam Market price of a skin
- /cs inventory   : CS2 inventory of a user + total EUR value
- /cs queue       : create a temporary voice channel for a Premier stack
- /cs map         : turn-by-turn map ban/pick based on voice channel members
- /cs loadout     : random loadout generator

Security:
- No API key is ever logged or returned to the user.
- Strict regex validation on input (steam_id, faceit nickname, skin name).
- Server errors -> generic message, details stay in the server-side logs.
"""
from __future__ import annotations

import asyncio
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services import cs2_api as csapi
from services.i18n import ti
from database import (
    cs_profile_get, cs_profile_upsert, cs_profile_unlink,
    cs_rank_config_get, cs_rank_config_upsert,
    cs_queue_lobby_add, cs_queue_lobby_delete, cs_queue_lobbies_list,
    cs_cache_get, cs_cache_set,
)


CS2_MAP_POOL = ["Mirage", "Inferno", "Nuke", "Anubis", "Ancient", "Dust2", "Train"]

# Slot codes -> i18n keys are in _LOADOUT_SLOT_KEYS below.
WEAPON_POOL = {
    "pistol": ["USP-S", "Glock-18", "P250", "Tec-9", "Five-SeveN", "Desert Eagle", "CZ75-Auto"],
    "rifle":  ["AK-47", "M4A4", "M4A1-S", "AUG", "SG 553", "FAMAS", "Galil AR"],
    "sniper": ["AWP", "SSG 08", "SCAR-20", "G3SG1"],
    "smg":    ["MP9", "MAC-10", "MP7", "P90", "UMP-45", "PP-Bizon", "MP5-SD"],
    "heavy":  ["Nova", "XM1014", "MAG-7", "Sawed-Off", "M249", "Negev"],
}
_LOADOUT_SLOT_KEYS = {
    "pistol": "games.cs.loadout.slot_pistol",
    "rifle":  "games.cs.loadout.slot_rifle",
    "sniper": "games.cs.loadout.slot_sniper",
    "smg":    "games.cs.loadout.slot_smg",
    "heavy":  "games.cs.loadout.slot_heavy",
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
# Steam wear names: never translate, they are part of market_hash_name.
WEAR_LEVELS = ["Battle-Scarred", "Well-Worn", "Field-Tested", "Minimal Wear", "Factory New"]
WEAR_LEVELS_ORDER = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]

# Flat list of ALL weapons for the /cs price autocomplete.
# Knives and gloves carry the '★' prefix, which must be kept in
# market_hash_name. StatTrak can be added by the user.
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
    # Knives (with the star)
    "★ Karambit", "★ M9 Bayonet", "★ Butterfly Knife", "★ Talon Knife",
    "★ Stiletto Knife", "★ Skeleton Knife", "★ Bowie Knife",
    "★ Falchion Knife", "★ Huntsman Knife", "★ Flip Knife", "★ Gut Knife",
    "★ Shadow Daggers", "★ Bayonet", "★ Ursus Knife", "★ Navaja Knife",
    "★ Classic Knife", "★ Paracord Knife", "★ Survival Knife", "★ Nomad Knife",
    # Gloves (with the star)
    "★ Sport Gloves", "★ Driver Gloves", "★ Specialist Gloves",
    "★ Hand Wraps", "★ Bloodhound Gloves", "★ Moto Gloves",
    "★ Hydra Gloves", "★ Broken Fang Gloves",
]


def _build_market_hash_name(weapon: str, skin: str, wear: Optional[str], stattrak: bool) -> str:
    """Build the Steam market_hash_name following these conventions:
      - Normal      : 'AK-47 | Redline (Field-Tested)'
      - StatTrak    : 'StatTrak™ AK-47 | Redline (Field-Tested)'
      - Knife       : '★ Karambit | Doppler (Factory New)'
      - Knife StT   : '★ StatTrak™ Karambit | Doppler (Factory New)'
      - Gloves      : '★ Sport Gloves | Pandora\'s Box (Field-Tested)'  (no StatTrak)
    """
    skin = (skin or "").strip()
    is_special = weapon.startswith("★")
    is_gloves  = is_special and ("Gloves" in weapon or "Hand Wraps" in weapon)
    parts = []
    if is_special:
        weapon_clean = weapon.replace("★", "").strip()
        if stattrak and not is_gloves:
            parts.append(f"★ StatTrak™ {weapon_clean}")
        else:
            parts.append(f"★ {weapon_clean}")
    else:
        if stattrak:
            parts.append(f"StatTrak™ {weapon}")
        else:
            parts.append(weapon)
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


async def _build_steam_link(interaction: discord.Interaction, raw: str) -> Optional[str]:
    steam_id = await csapi.steam_resolve(raw)
    if not steam_id:
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.err.steam_id_invalid_title"),
                ti(interaction, "games.cs.err.steam_id_invalid_link")),
            ephemeral=True,
        )
        return None
    summary = await csapi.steam_player_summary(steam_id)
    if not summary:
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.err.steam_profile_not_found_title"),
                ti(interaction, "games.cs.err.steam_profile_not_found_desc")),
            ephemeral=True,
        )
        return None
    cs_profile_upsert(interaction.user.id, steam_id=steam_id)
    persona = summary.get("personaname") or steam_id
    await interaction.followup.send(
        embed=_info_embed(
            ti(interaction, "games.cs.link.steam_success_title"),
            ti(interaction, "games.cs.link.steam_success_desc",
               persona=persona, steam_id=steam_id),
            color=0x2ECC71),
        ephemeral=True,
    )
    return steam_id


async def _build_faceit_link(interaction: discord.Interaction, nickname: str) -> Optional[str]:
    if not (2 <= len(nickname) <= 30) or not all(ch.isalnum() or ch in "_-." for ch in nickname):
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.err.faceit_nick_invalid_title"),
                ti(interaction, "games.cs.err.faceit_nick_invalid_desc")),
            ephemeral=True,
        )
        return None
    fc = await csapi.faceit_player_by_nickname(nickname)
    if not fc or not fc.get("player_id"):
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.err.faceit_not_found_title"),
                ti(interaction, "games.cs.err.faceit_not_found_desc", nickname=nickname)),
            ephemeral=True,
        )
        return None
    pid  = fc["player_id"]
    nick = fc.get("nickname") or nickname
    cs_profile_upsert(interaction.user.id, faceit_id=pid, faceit_nick=nick)
    skill = (fc.get("games", {}) or {}).get("cs2", {}).get("skill_level") or "—"
    await interaction.followup.send(
        embed=_info_embed(
            ti(interaction, "games.cs.link.faceit_success_title"),
            ti(interaction, "games.cs.link.faceit_success_desc", nickname=nick, level=skill),
            color=0x2ECC71),
        ephemeral=True,
    )
    return pid


async def _build_steam_stats_embed(interaction: discord.Interaction, member, steam_id: str) -> discord.Embed:
    summary = await csapi.steam_player_summary(steam_id)
    stats   = await csapi.steam_cs2_stats(steam_id)
    owned   = await csapi.steam_owned_cs2(steam_id)
    persona = (summary or {}).get("personaname") or ti(interaction, "games.cs.stats.unknown_player")
    avatar  = (summary or {}).get("avatarfull")

    if stats and stats.get("_private"):
        e = discord.Embed(
            title=ti(interaction, "games.cs.stats.steam_title", persona=persona),
            description=ti(interaction, "games.cs.stats.profile_private"),
            color=0xE67E22,
        )
        if avatar:
            e.set_thumbnail(url=avatar)
        return e
    if stats is None:
        e = discord.Embed(
            title=ti(interaction, "games.cs.stats.steam_title", persona=persona),
            description=ti(interaction, "games.cs.stats.steam_unavailable"),
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
    matches_w = stats.get("total_matches_won", 0) or 0
    bombs_p  = stats.get("total_planted_bombs", 0) or 0
    bombs_d  = stats.get("total_defused_bombs", 0) or 0
    money    = stats.get("total_money_earned", 0) or 0
    hours    = (owned or {}).get("playtime_forever", 0) // 60 if owned else 0

    kd      = (kills / deaths) if deaths else float(kills)
    hs_pct  = (hs / kills) if kills else 0
    acc_pct = (hits / shots) if shots else 0
    wr_pct  = (matches_w / matches) if matches else 0

    color = 0x1A9FFF
    embed = discord.Embed(
        title=ti(interaction, "games.cs.stats.steam_profile_title", persona=persona),
        url=f"https://steamcommunity.com/profiles/{steam_id}",
        color=color,
    )
    if avatar:
        embed.set_thumbnail(url=avatar)

    embed.add_field(
        name=ti(interaction, "games.cs.stats.performance"),
        value=ti(interaction, "games.cs.stats.performance_value",
                 kills=fmt_int(kills), deaths=fmt_int(deaths),
                 kd=f"{kd:.2f}", mvps=fmt_int(mvps)),
        inline=True,
    )
    embed.add_field(
        name=ti(interaction, "games.cs.stats.accuracy"),
        value=ti(interaction, "games.cs.stats.accuracy_value",
                 hs=fmt_int(hs), hs_pct=f"{hs_pct*100:.1f}", hs_bar=bar(hs_pct),
                 acc_pct=f"{acc_pct*100:.1f}", acc_bar=bar(acc_pct)),
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=False)

    embed.add_field(
        name=ti(interaction, "games.cs.stats.matches"),
        value=ti(interaction, "games.cs.stats.matches_value",
                 played=fmt_int(matches), won=fmt_int(matches_w),
                 wr=f"{wr_pct*100:.1f}", wr_bar=bar(wr_pct)),
        inline=True,
    )
    embed.add_field(
        name=ti(interaction, "games.cs.stats.rounds"),
        value=ti(interaction, "games.cs.stats.rounds_value",
                 rounds=fmt_int(rounds), wins=fmt_int(wins),
                 planted=fmt_int(bombs_p), defused=fmt_int(bombs_d)),
        inline=True,
    )
    embed.add_field(name="​", value="​", inline=False)

    embed.add_field(
        name=ti(interaction, "games.cs.stats.economy"),
        value=ti(interaction, "games.cs.stats.economy_value",
                 money=fmt_int(money),
                 hours_2w=(owned or {}).get("playtime_2weeks", 0) // 60,
                 hours_total=hours),
        inline=False,
    )
    embed.set_footer(text=ti(interaction, "games.cs.stats.steam_footer", steam_id=steam_id))
    return embed


async def _build_faceit_stats_embed(interaction: discord.Interaction, member,
                                    faceit_id: str, faceit_nick: str) -> discord.Embed:
    fc = await csapi.faceit_player_stats(faceit_id, "cs2")
    if not fc:
        return discord.Embed(
            title=ti(interaction, "games.cs.stats.faceit_title", nickname=faceit_nick),
            description=ti(interaction, "games.cs.stats.faceit_unavailable"),
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
        title=ti(interaction, "games.cs.stats.faceit_profile_title", nickname=faceit_nick),
        url=f"https://www.faceit.com/en/players/{faceit_nick}",
        color=0xFF5500,
    )
    embed.add_field(
        name=ti(interaction, "games.cs.stats.lifetime"),
        value=ti(interaction, "games.cs.stats.lifetime_value",
                 matches=fmt_int(matches), wins=fmt_int(wins),
                 wr=f"{wr*100:.1f}", wr_bar=bar(wr)),
        inline=True,
    )
    embed.add_field(
        name=ti(interaction, "games.cs.stats.combat"),
        value=ti(interaction, "games.cs.stats.combat_value",
                 kd=f"{kdr:.2f}", hs_pct=f"{hs_pct*100:.1f}", hs_bar=bar(hs_pct),
                 best_streak=longest_streak, current_streak=cur_streak),
        inline=True,
    )
    # Per-map stats (top 5 by matches played)
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
        embed.add_field(name=ti(interaction, "games.cs.stats.top_maps"),
                        value="\n".join(lines), inline=False)

    embed.set_footer(text=ti(interaction, "games.cs.stats.faceit_footer"))
    return embed


def quote_plus_safe(s: str) -> str:
    from urllib.parse import quote_plus as _qp
    return _qp(s)


async def _build_price_embed(interaction: discord.Interaction, name: str) -> discord.Embed:
    """Price embed for ONE exact market_hash_name. Shows Steam + Skinport + CSFloat."""
    # Steam
    ck_steam = f"price:{name.lower()}"
    cached_steam = cs_cache_get(ck_steam, max_age_sec=600)
    steam_data = cached_steam or await csapi.steam_market_price(name, currency=3)
    if not cached_steam and steam_data:
        cs_cache_set(ck_steam, steam_data)

    # Skinport (through the csgotrader bundle)
    ck_sp = f"skinport:{name.lower()}"
    cached_sp = cs_cache_get(ck_sp, max_age_sec=1800)
    sp_data = cached_sp or await csapi.skinport_lowest_price(name)
    if not cached_sp and sp_data:
        cs_cache_set(ck_sp, sp_data)

    # CSFloat (auth API key)
    ck_cf = f"csfloat:{name.lower()}"
    cached_cf = cs_cache_get(ck_cf, max_age_sec=600)
    cf_data = cached_cf or await csapi.csfloat_lowest_price(name)
    if not cached_cf and cf_data:
        cs_cache_set(ck_cf, cf_data)

    encoded = quote_plus_safe(name)
    if not steam_data and not sp_data and not cf_data:
        return _err_embed(
            ti(interaction, "games.cs.price.not_found_title"),
            ti(interaction, "games.cs.price.not_found_desc", name=name),
        )

    embed = discord.Embed(
        title=f"💸 {name}",
        url=f"https://steamcommunity.com/market/listings/730/{encoded}",
        color=0xF1C40F,
    )
    # Steam field
    if steam_data:
        steam_value = ti(interaction, "games.cs.price.steam_value",
                         lowest=steam_data.get("lowest_price") or "n/a",
                         median=steam_data.get("median_price") or "n/a",
                         volume=steam_data.get("volume") or 0)
    else:
        steam_value = ti(interaction, "games.cs.price.no_listing")
    embed.add_field(name=ti(interaction, "games.cs.price.steam_field"),
                    value=steam_value, inline=True)

    # Skinport field
    if sp_data:
        eur = sp_data.get("price_eur") or 0
        sugg = sp_data.get("suggested_price") or 0
        qty  = sp_data.get("quantity") or 0
        extra = []
        if sugg:
            extra.append(ti(interaction, "games.cs.price.skinport_suggested", price=f"{sugg:.2f}"))
        if qty:
            extra.append(ti(interaction, "games.cs.price.listings", count=qty))
        sp_value = ti(interaction, "games.cs.price.skinport_value",
                      lowest=f"{eur:.2f}",
                      extra=("\n".join(extra) + "\n" if extra else ""),
                      encoded=encoded)
    else:
        sp_value = ti(interaction, "games.cs.price.no_listing")
    embed.add_field(name=ti(interaction, "games.cs.price.skinport_field"),
                    value=sp_value, inline=True)

    # CSFloat field
    if cf_data:
        eur = cf_data.get("price_eur") or 0
        usd = cf_data.get("price_usd") or 0
        qty = cf_data.get("listings_count") or 0
        cf_value = ti(interaction, "games.cs.price.csfloat_value",
                      eur=f"{eur:.2f}", usd=f"{usd:.2f}",
                      extra=(ti(interaction, "games.cs.price.listings", count=qty) + "\n") if qty else "",
                      encoded=encoded)
    else:
        cf_value = ti(interaction, "games.cs.price.no_listing")
    embed.add_field(name=ti(interaction, "games.cs.price.csfloat_field"),
                    value=cf_value, inline=True)

    embed.set_footer(text=ti(interaction, "games.cs.price.footer"))
    return embed


async def _build_price_embed_all_wears(interaction: discord.Interaction, weapon: str,
                                       skin: str, stattrak: bool) -> discord.Embed:
    """Query Steam + Skinport + CSFloat for every wear level, aligned table."""
    rows = []  # (wear, steam, sp, cf)
    any_found = False
    for wear in WEAR_LEVELS_ORDER:
        name = _build_market_hash_name(weapon, skin, wear, stattrak)

        # Steam
        ck_steam = f"price:{name.lower()}"
        cached_steam = cs_cache_get(ck_steam, max_age_sec=600)
        steam_data = cached_steam or await csapi.steam_market_price(name, currency=3)
        if not cached_steam and steam_data:
            cs_cache_set(ck_steam, steam_data)
        if not cached_steam:
            await asyncio.sleep(0.4)
        steam_price = (steam_data or {}).get("lowest_price") if steam_data else None

        # Skinport
        ck_sp = f"skinport:{name.lower()}"
        cached_sp = cs_cache_get(ck_sp, max_age_sec=1800)
        sp_data = cached_sp or await csapi.skinport_lowest_price(name)
        if not cached_sp and sp_data:
            cs_cache_set(ck_sp, sp_data)
        if not cached_sp:
            await asyncio.sleep(0.3)

        # CSFloat
        ck_cf = f"csfloat:{name.lower()}"
        cached_cf = cs_cache_get(ck_cf, max_age_sec=600)
        cf_data = cached_cf or await csapi.csfloat_lowest_price(name)
        if not cached_cf and cf_data:
            cs_cache_set(ck_cf, cf_data)
        if not cached_cf:
            await asyncio.sleep(0.3)

        steam_str = steam_price if steam_price else "—"
        sp_str = f"{sp_data['price_eur']:.2f}€" if (sp_data and sp_data.get("price_eur")) else "—"
        cf_str = f"{cf_data['price_eur']:.2f}€" if (cf_data and cf_data.get("price_eur")) else "—"
        if steam_price or sp_data or cf_data:
            any_found = True
        rows.append((wear, steam_str, sp_str, cf_str))

    base_name = _build_market_hash_name(weapon, skin, None, stattrak)
    if not any_found:
        return _err_embed(
            ti(interaction, "games.cs.price.not_found_title"),
            ti(interaction, "games.cs.price.not_found_all_desc", name=base_name),
        )

    wear_col = ti(interaction, "games.cs.price.table_wear")
    header = f"{wear_col:<14} │ {'Steam':<9} │ {'Skinport':<9} │ {'CSFloat':<9}"
    sep    = f"{'─'*14}─┼─{'─'*9}─┼─{'─'*9}─┼─{'─'*9}"
    lines = [header, sep]
    for wear, s_str, sp_str, cf_str in rows:
        # Wear names come from Steam, they stay untranslated.
        lines.append(f"{wear:<14} │ {s_str:<9} │ {sp_str:<9} │ {cf_str:<9}")

    embed = discord.Embed(
        title=f"💸 {base_name}",
        description=ti(interaction, "games.cs.price.all_wears_desc", table="\n".join(lines)),
        color=0xF1C40F,
    )
    embed.set_footer(text=ti(interaction, "games.cs.price.all_wears_footer"))
    return embed


async def _weapon_autocomplete(interaction: discord.Interaction, current: str):
    cur = (current or "").lower()
    matches = [w for w in PRICE_WEAPONS if cur in w.lower()][:25]
    return [app_commands.Choice(name=w, value=w) for w in matches]


async def _skin_autocomplete(interaction: discord.Interaction, current: str):
    # Read the weapon already typed in to narrow the search
    try:
        weapon = (interaction.namespace.weapon or "").strip()
    except Exception:
        weapon = ""
    cur = (current or "").strip()
    if not weapon:
        return [app_commands.Choice(
            name=ti(interaction, "games.cs.price.autocomplete_pick_weapon"),
            value=cur or " ")]
    weapon_for_query = weapon.replace("★", "").strip()
    query = f"{weapon_for_query} {cur}".strip()
    cache_key = f"skin_ac:{query.lower()}"
    cached = cs_cache_get(cache_key, max_age_sec=3600)
    if cached:
        skins = cached
    else:
        results = await csapi.steam_market_search(query, count=40)
        if not results:
            return [app_commands.Choice(
                name=ti(interaction, "games.cs.price.autocomplete_no_result"),
                value=cur or " ")]
        skins_set = set()
        for hash_name in results:
            # Skip items with no " | " (e.g. keys, agents)
            if " | " not in hash_name:
                continue
            # Keep only items that really carry the searched weapon in the prefix
            prefix, after_pipe = hash_name.split(" | ", 1)
            if weapon_for_query.lower() not in prefix.lower():
                continue
            # Skin = the part between " | " and " (Wear)"
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
        return [app_commands.Choice(
            name=ti(interaction, "games.cs.price.autocomplete_no_skin"),
            value=cur or " ")]
    return [app_commands.Choice(name=s[:100], value=s[:100]) for s in skins[:25]]


# Item types ignored for the valuation (not marketable, or not relevant to the total).
# These strings are Steam API values returned with `l=french`, do not translate them.
_IGNORE_TYPES = {"Conteneur de munitions", "Graffiti", "Pass-temps"}


async def _build_inventory_embed(interaction: discord.Interaction,
                                 member: discord.abc.User, steam_id: str) -> discord.Embed:
    items = await csapi.steam_inventory(steam_id)
    if items is None:
        return _err_embed(
            ti(interaction, "games.cs.inventory.unavailable_title"),
            ti(interaction, "games.cs.inventory.unavailable_desc",
               name=member.display_name, steam_id=steam_id),
        )
    if not items:
        return _info_embed(
            ti(interaction, "games.cs.inventory.title", name=member.display_name),
            ti(interaction, "games.cs.inventory.empty"),
            color=0x95A5A6,
        )

    # Aggregation: count ALL CS2 items (including non-marketable cooldown ones)
    # but track the marketable flag separately for the valuation.
    counts: dict[str, int] = {}
    marketable_counts: dict[str, int] = {}
    nonmarket_counts:  dict[str, int] = {}
    for it in items:
        if it.get("type") in _IGNORE_TYPES:
            continue
        nm = it["name"]
        counts[nm] = counts.get(nm, 0) + 1
        if it.get("marketable"):
            marketable_counts[nm] = marketable_counts.get(nm, 0) + 1
        else:
            nonmarket_counts[nm] = nonmarket_counts.get(nm, 0) + 1

    if not counts:
        return _info_embed(
            ti(interaction, "games.cs.inventory.title", name=member.display_name),
            ti(interaction, "games.cs.inventory.no_cs2_item"),
            color=0x95A5A6,
        )

    total_eur = 0.0
    valued    = []

    # Phase 1: CSFloat as the primary source (real marketplace price, auth through
    # CSFLOAT_API_KEY). Capped to respect the ~100/min rate limit with auth.
    CSFLOAT_LOOKUP_CAP = 120
    names_list = list(counts.keys())
    cf_targets = names_list[:CSFLOAT_LOOKUP_CAP]
    cf_overflow = names_list[CSFLOAT_LOOKUP_CAP:]
    csfloat_misses = []
    for name in cf_targets:
        ck = f"csfloat:{name.lower()}"
        cached = cs_cache_get(ck, max_age_sec=600)
        cf = cached or await csapi.csfloat_lowest_price(name)
        if not cached and cf:
            cs_cache_set(ck, cf)
        if cf and cf.get("price_eur"):
            eur = float(cf["price_eur"])
            qty = counts[name]
            total_eur += eur * qty
            valued.append((name, qty, eur, "csfloat"))
        else:
            csfloat_misses.append(name)
        if not cached:
            await asyncio.sleep(0.6)

    # Phase 2: Skinport (instant local bundle) for CSFloat misses + overflow
    skinport_misses = []
    for name in csfloat_misses + cf_overflow:
        sp = await csapi.skinport_lowest_price(name)
        if sp and sp.get("price_eur"):
            eur = float(sp["price_eur"])
            qty = counts[name]
            total_eur += eur * qty
            valued.append((name, qty, eur, "skinport"))
        else:
            skinport_misses.append(name)

    # Phase 3: Steam Market as the last resort (capped for the rate limit)
    STEAM_LOOKUP_CAP = 40
    steam_targets = skinport_misses[:STEAM_LOOKUP_CAP]
    for name in steam_targets:
        ck = f"price:{name.lower()}"
        cached = cs_cache_get(ck, max_age_sec=900)
        price_data = cached or await csapi.steam_market_price(name, currency=3)
        if not cached and price_data:
            cs_cache_set(ck, price_data)
        eur = csapi._parse_price_eur((price_data or {}).get("lowest_price"))
        qty = counts[name]
        if eur is not None:
            total_eur += eur * qty
            valued.append((name, qty, eur, "steam"))
        if not cached:
            await asyncio.sleep(0.4)

    not_priced = len(counts) - len(valued)
    valued.sort(key=lambda r: -(r[2] * r[1]))

    top_lines = []
    _SRC_TAG = {"csfloat": "🟪", "skinport": "🟧", "steam": "🟦"}
    for name, qty, eur, src in valued[:10]:
        sub = eur * qty
        tag = _SRC_TAG.get(src, "•")
        cd_tag = " 🔒" if nonmarket_counts.get(name) else ""
        top_lines.append(f"{tag} `x{qty}` **{name}**{cd_tag} — {sub:.2f}€ (`{eur:.2f}€/u`)")

    extra_note = ""
    overflow_steam = max(0, len(skinport_misses) - STEAM_LOOKUP_CAP)
    if overflow_steam:
        extra_note = ti(interaction, "games.cs.inventory.partial_note", count=overflow_steam)

    total_items     = sum(counts.values())
    total_market    = sum(marketable_counts.values())
    total_nonmarket = sum(nonmarket_counts.values())

    embed = discord.Embed(
        title=ti(interaction, "games.cs.inventory.title", name=member.display_name),
        description=ti(
            interaction, "games.cs.inventory.summary",
            total=total_items,
            unique=len(counts),
            sellable=total_market,
            cooldown=(ti(interaction, "games.cs.inventory.cooldown_part", count=total_nonmarket)
                      if total_nonmarket else ""),
            priced=len(valued),
            missing=(ti(interaction, "games.cs.inventory.missing_part", count=not_priced)
                     if not_priced else ""),
            value=f"{total_eur:.2f}",
            note=extra_note,
        ),
        url=f"https://steamcommunity.com/profiles/{steam_id}/inventory/#730",
        color=0x9B59B6,
    )
    if top_lines:
        embed.add_field(name=ti(interaction, "games.cs.inventory.top_items"),
                        value="\n".join(top_lines), inline=False)
    embed.set_footer(text=ti(interaction, "games.cs.inventory.footer"))
    return embed


async def _create_queue_lobby(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    guild = interaction.guild
    if guild is None:
        return None
    # Category: same as the channel the command was run in, root otherwise
    category = interaction.channel.category if interaction.channel and hasattr(interaction.channel, "category") else None
    name = ti(interaction, "games.cs.queue.channel_name", name=interaction.user.display_name)
    try:
        vc = await guild.create_voice_channel(
            name=name[:100],
            user_limit=5,
            category=category,
            reason=f"CS2 queue created by {interaction.user} ({interaction.user.id})",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.err.missing_permission_title"),
                ti(interaction, "games.cs.err.missing_permission_desc")),
            ephemeral=True,
        )
        return None
    except Exception as e:
        print(f"[cs2/queue] create err: {type(e).__name__}")
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.err.generic_title"),
                ti(interaction, "games.cs.err.voice_create_failed")),
            ephemeral=True,
        )
        return None
    cs_queue_lobby_add(vc.id, guild.id, interaction.user.id)
    return vc


async def on_voice_state_update(member, before, after, bot):
    """Hook to wire into bot.py: auto-deletes queue voice channels that become
    empty. `member` is explicitly excluded from the count because the discord.py
    cache is not always updated before this handler runs."""
    if not before.channel or before.channel == after.channel:
        return
    if member.bot:
        return
    ch = before.channel
    if not ch.guild:
        return
    lobbies = {l["channel_id"]: l for l in cs_queue_lobbies_list(ch.guild.id)}
    if str(ch.id) not in lobbies:
        return
    remaining = [m for m in ch.members if not m.bot and m.id != member.id]
    if remaining:
        return
    try:
        await ch.delete(reason="CS2 queue empty, auto-cleanup")
        print(f"[cs2/queue] deleted empty lobby channel_id={ch.id}")
    except Exception as e:
        print(f"[cs2/queue] auto-delete err channel_id={ch.id}: {type(e).__name__}: {e}")
        return
    cs_queue_lobby_delete(ch.id)


async def queue_cleanup_sweep(bot):
    """Background task: walks every persisted queue and deletes the ones that
    are really empty. Safety net when on_voice_state_update misses an event
    (bot restart, Discord glitch)."""
    try:
        lobbies = cs_queue_lobbies_list()
    except Exception as e:
        print(f"[cs2/queue/sweep] list err: {type(e).__name__}")
        return
    for lobby in lobbies:
        try:
            ch = bot.get_channel(int(lobby["channel_id"]))
            if ch is None:
                # Channel deleted on the Discord side, clean up the DB
                cs_queue_lobby_delete(lobby["channel_id"])
                continue
            non_bot = [m for m in ch.members if not m.bot]
            if non_bot:
                continue
            try:
                await ch.delete(reason="CS2 queue empty (sweep)")
                print(f"[cs2/queue/sweep] deleted empty lobby channel_id={ch.id}")
            except Exception as e:
                print(f"[cs2/queue/sweep] delete err channel_id={ch.id}: {type(e).__name__}")
                continue
            cs_queue_lobby_delete(ch.id)
        except Exception as e:
            print(f"[cs2/queue/sweep] iter err: {type(e).__name__}")


class MapBanView(discord.ui.View):
    def __init__(self, origin: discord.Interaction, voters: list, maps: list[str], event: asyncio.Event):
        super().__init__(timeout=240)
        self.origin = origin
        self.voters = voters
        self.maps   = list(maps)
        self.event  = event
        self.idx    = 0      # voter turn
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
                # Filter: only the current voter
                if interaction.user.id != self.current_voter.id:
                    try:
                        await interaction.response.send_message(
                            ti(interaction, "games.cs.map.not_your_turn",
                               name=self.current_voter.display_name),
                            ephemeral=True,
                        )
                    except Exception:
                        pass
                    return
                self.bans.append((self.current_voter, picked))
                self.maps.remove(picked)
                self.idx += 1
                if len(self.maps) <= 1:
                    # Done: 1 map left
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
            title=ti(self.origin, "games.cs.map.title"),
            description=ti(self.origin, "games.cs.map.description",
                           mention=self.current_voter.mention,
                           voters=", ".join(v.display_name for v in self.voters)),
            color=0xE67E22,
        )
        if self.bans:
            lines = [ti(self.origin, "games.cs.map.ban_line", map=m, name=v.display_name)
                     for v, m in self.bans]
            embed.add_field(name=ti(self.origin, "games.cs.map.bans"),
                            value="\n".join(lines), inline=False)
        embed.add_field(name=ti(self.origin, "games.cs.map.remaining"),
                        value=" · ".join(f"`{m}`" for m in self.maps), inline=False)
        embed.set_footer(text=ti(self.origin, "games.cs.map.footer"))
        return embed


async def _run_map_ban(interaction: discord.Interaction):
    user = interaction.user
    vstate = user.voice if hasattr(user, "voice") else None
    if not vstate or not vstate.channel:
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.map.not_in_voice_title"),
                ti(interaction, "games.cs.map.not_in_voice_desc")),
            ephemeral=True,
        )
        return
    voters = [m for m in vstate.channel.members if not m.bot]
    if len(voters) < 2:
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.map.not_enough_voters_title"),
                ti(interaction, "games.cs.map.not_enough_voters_desc")),
            ephemeral=True,
        )
        return

    event = asyncio.Event()
    view  = MapBanView(interaction, voters, CS2_MAP_POOL, event)
    msg   = await interaction.followup.send(embed=view._make_embed(), view=view)
    try:
        await asyncio.wait_for(event.wait(), timeout=240)
    except asyncio.TimeoutError:
        try:
            await msg.edit(embed=_err_embed(
                ti(interaction, "games.cs.map.timeout_title"),
                ti(interaction, "games.cs.map.timeout_desc")), view=None)
        except Exception:
            pass
        return
    final_map = view.maps[0] if view.maps else "?"
    recap = "\n".join(
        ti(interaction, "games.cs.map.final_ban_line", map=m, name=v.display_name)
        for v, m in view.bans
    )
    embed = discord.Embed(
        title=ti(interaction, "games.cs.map.final_title", map=final_map),
        description=ti(interaction, "games.cs.map.final_desc", recap=recap),
        color=0x2ECC71,
    )
    try:
        await msg.edit(embed=embed, view=None)
    except Exception:
        pass


def _build_loadout_embed(interaction: discord.Interaction) -> discord.Embed:
    loadout = {}
    for slot, weapons in WEAPON_POOL.items():
        weapon = random.choice(weapons)
        skin   = random.choice(SKIN_POOL_GENERIC)
        wear   = random.choice(WEAR_LEVELS)
        loadout[slot] = f"**{weapon}** | _{skin}_ ({wear})"
    knife = f"**★ {random.choice(KNIVES)}** | _{random.choice(KNIFE_FINISHES)}_ ({random.choice(WEAR_LEVELS)})"
    gloves = f"**★ {random.choice(GLOVES)}** | _{random.choice(GLOVES_FINISHES)}_"

    embed = discord.Embed(
        title=ti(interaction, "games.cs.loadout.title"),
        description=ti(interaction, "games.cs.loadout.description"),
        color=0x16A085,
    )
    for slot, val in loadout.items():
        embed.add_field(name=ti(interaction, _LOADOUT_SLOT_KEYS[slot]), value=val, inline=False)
    embed.add_field(name=ti(interaction, "games.cs.loadout.knife"), value=knife, inline=False)
    embed.add_field(name=ti(interaction, "games.cs.loadout.gloves"), value=gloves, inline=False)
    embed.set_footer(text=ti(interaction, "games.cs.loadout.footer"))
    return embed


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
    """Apply the role matching the elo tier. Removes the other rank roles.
    Returns the tier label, or None when the feature is off / config incomplete."""
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
    # Remove every other rank role from the user
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


def setup_cs2_commands(bot: commands.Bot):
    cs_group = app_commands.Group(name="cs", description="Counter-Strike 2 commands")

    # ---------- /cs link ----------
    @cs_group.command(name="link", description="Link your Steam or Faceit account to your Discord")
    @app_commands.describe(
        platform="Pick the platform to link",
        identifier="Steam: URL https://steamcommunity.com/id/<name>/ or /profiles/<id>. Faceit: nickname.",
    )
    @app_commands.choices(platform=[
        app_commands.Choice(name="Steam",  value="steam"),
        app_commands.Choice(name="Faceit", value="faceit"),
    ])
    async def cs_link(interaction: discord.Interaction,
                      platform: app_commands.Choice[str], identifier: str):
        await interaction.response.defer(ephemeral=True)
        if platform.value == "steam":
            await _build_steam_link(interaction, identifier)
        else:
            await _build_faceit_link(interaction, identifier)

    # ---------- /cs unlink ----------
    @cs_group.command(name="unlink", description="Remove a Steam link, a Faceit link, or both")
    @app_commands.choices(platform=[
        app_commands.Choice(name="Steam",  value="steam"),
        app_commands.Choice(name="Faceit", value="faceit"),
        app_commands.Choice(name="All",    value="all"),
    ])
    async def cs_unlink(interaction: discord.Interaction,
                        platform: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        cs_profile_unlink(interaction.user.id, platform.value)
        await interaction.followup.send(
            embed=_info_embed(
                ti(interaction, "games.cs.unlink.success_title"),
                ti(interaction, "games.cs.unlink.success_desc", platform=platform.name),
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /cs stats ----------
    @cs_group.command(name="stats", description="Show a player's CS2 stats (yours by default)")
    @app_commands.describe(
        member="Discord member to inspect (optional)",
        steamid="Or a SteamID64 / Steam URL directly",
    )
    async def cs_stats(interaction: discord.Interaction,
                       member: Optional[discord.Member] = None,
                       steamid: Optional[str] = None):
        await interaction.response.defer()
        if member and steamid:
            await interaction.followup.send(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.ambiguous_params_title"),
                    ti(interaction, "games.cs.err.ambiguous_params_desc")),
                ephemeral=True,
            )
            return
        if steamid:
            resolved = await csapi.steam_resolve(steamid)
            if not resolved:
                await interaction.followup.send(
                    embed=_err_embed(
                        ti(interaction, "games.cs.err.steam_id_invalid_title"),
                        ti(interaction, "games.cs.err.steam_id_invalid_short")),
                    ephemeral=True,
                )
                return
            summary = await csapi.steam_player_summary(resolved)
            display_name = (summary or {}).get("personaname") or resolved
            class _Stub:
                pass
            stub = _Stub()
            stub.display_name = display_name
            embed = await _build_steam_stats_embed(interaction, stub, resolved)
            await interaction.followup.send(embed=embed)
            return
        target = member or interaction.user
        prof = cs_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.no_account_title"),
                    ti(interaction, "games.cs.err.no_account_desc", name=target.display_name)),
                ephemeral=True,
            )
            return

        has_steam  = bool(prof.get("steam_id"))
        has_faceit = bool(prof.get("faceit_id"))
        if has_steam and has_faceit:
            view = _StatsProfileSelectView(target, prof)
            await interaction.followup.send(
                embed=_info_embed(
                    ti(interaction, "games.cs.stats.choose_profile_title", name=target.display_name),
                    ti(interaction, "games.cs.stats.choose_profile_desc"),
                    color=0x3498DB),
                view=view,
            )
            return
        if has_steam:
            embed = await _build_steam_stats_embed(interaction, target, prof["steam_id"])
            await interaction.followup.send(embed=embed)
            return
        if has_faceit:
            embed = await _build_faceit_stats_embed(interaction, target, prof["faceit_id"],
                                                    prof.get("faceit_nick") or "?")
            await interaction.followup.send(embed=embed)
            return
        await interaction.followup.send(
            embed=_err_embed(
                ti(interaction, "games.cs.err.no_usable_profile_title"),
                ti(interaction, "games.cs.err.no_usable_profile_desc")),
            ephemeral=True,
        )

    # ---------- /cs setrank ----------
    @cs_group.command(name="setrank", description="Declare your CS2 Premier rating (0-40000)")
    @app_commands.describe(elo="Your Premier rating (shown in game)")
    async def cs_setrank(interaction: discord.Interaction, elo: app_commands.Range[int, 0, 40000]):
        await interaction.response.defer(ephemeral=True)
        cs_profile_upsert(interaction.user.id, premier_elo=elo)
        code, label, color = csapi.premier_tier(elo)
        applied_label = None
        if isinstance(interaction.user, discord.Member):
            applied_label = await _apply_rank_role(interaction.user, elo)
        msg = ti(interaction, "games.cs.setrank.saved_desc", elo=elo, tier=label)
        if applied_label:
            msg += ti(interaction, "games.cs.setrank.role_applied", role=applied_label)
        await interaction.followup.send(
            embed=_info_embed(ti(interaction, "games.cs.setrank.saved_title"),
                              msg, color=color or 0x3498DB),
            ephemeral=True,
        )

    # ---------- /cs rankrole on|off ----------
    @cs_group.command(name="rankrole", description="Admin: enable/disable automatic rank role assignment")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="Enable",  value="on"),
        app_commands.Choice(name="Disable", value="off"),
    ])
    async def cs_rankrole(interaction: discord.Interaction,
                          action: app_commands.Choice[str]):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.dm_not_supported_title"),
                    ti(interaction, "games.cs.err.dm_not_supported_desc")),
                ephemeral=True,
            )
            return
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.permission_denied_title"),
                    ti(interaction, "games.cs.err.permission_denied_desc")),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        enabled = (action.value == "on")
        cs_rank_config_upsert(interaction.guild.id, enabled=enabled)
        state = ti(interaction, "games.cs.rankrole.state_on" if enabled
                   else "games.cs.rankrole.state_off")
        await interaction.followup.send(
            embed=_info_embed(
                ti(interaction, "games.cs.rankrole.title"),
                ti(interaction, "games.cs.rankrole.desc", state=state),
                color=0x2ECC71 if enabled else 0xE67E22,
            ),
            ephemeral=True,
        )

    # ---------- /cs price ----------
    @cs_group.command(name="price", description="Steam Market price of a skin (weapon autocomplete + free-form name)")
    @app_commands.describe(
        weapon="Weapon (or ★ knife, or ★ gloves). Type to autocomplete.",
        skin="Skin name (e.g. Redline, Asiimov, Hyper Beast)",
        wear="Wear level (optional - leave empty to see all 5 levels)",
        stattrak="StatTrak™ variant (kill counter) - default: no",
    )
    @app_commands.autocomplete(weapon=_weapon_autocomplete, skin=_skin_autocomplete)
    @app_commands.choices(wear=[
        app_commands.Choice(name="Factory New",    value="Factory New"),
        app_commands.Choice(name="Minimal Wear",   value="Minimal Wear"),
        app_commands.Choice(name="Field-Tested",   value="Field-Tested"),
        app_commands.Choice(name="Well-Worn",      value="Well-Worn"),
        app_commands.Choice(name="Battle-Scarred", value="Battle-Scarred"),
    ])
    async def cs_price(interaction: discord.Interaction, weapon: str, skin: str,
                       wear: Optional[app_commands.Choice[str]] = None,
                       stattrak: bool = False):
        if weapon not in PRICE_WEAPONS:
            # Tolerate a manual entry that still matches the list
            weapon_match = next((w for w in PRICE_WEAPONS if w.lower() == weapon.lower()), None)
            if not weapon_match:
                await interaction.response.send_message(
                    embed=_err_embed(
                        ti(interaction, "games.cs.err.unknown_weapon_title"),
                        ti(interaction, "games.cs.err.unknown_weapon_desc", weapon=weapon)),
                    ephemeral=True,
                )
                return
            weapon = weapon_match
        skin = (skin or "").strip()
        if not (2 <= len(skin) <= 60) or any(c in skin for c in "<>"):
            await interaction.response.send_message(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.invalid_skin_title"),
                    ti(interaction, "games.cs.err.invalid_skin_desc")),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        if wear:
            name = _build_market_hash_name(weapon, skin, wear.value, stattrak)
            embed = await _build_price_embed(interaction, name)
        else:
            embed = await _build_price_embed_all_wears(interaction, weapon, skin, stattrak)
        await interaction.followup.send(embed=embed)

    # ---------- /cs inventory ----------
    @cs_group.command(name="inventory", description="CS2 inventory of a member or of a SteamID")
    @app_commands.describe(
        member="Discord member (must have linked their Steam account)",
        steamid="Or a SteamID64 / Steam URL directly",
    )
    async def cs_inventory(interaction: discord.Interaction,
                           member: Optional[discord.Member] = None,
                           steamid: Optional[str] = None):
        await interaction.response.defer()
        if member and steamid:
            await interaction.followup.send(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.ambiguous_params_title"),
                    ti(interaction, "games.cs.err.ambiguous_params_desc")),
                ephemeral=True,
            )
            return
        steam_id = None
        display_name = None
        if steamid:
            steam_id = await csapi.steam_resolve(steamid)
            if not steam_id:
                await interaction.followup.send(
                    embed=_err_embed(
                        ti(interaction, "games.cs.err.steam_id_invalid_title"),
                        ti(interaction, "games.cs.err.steam_id_invalid_short")),
                    ephemeral=True,
                )
                return
            summary = await csapi.steam_player_summary(steam_id)
            display_name = (summary or {}).get("personaname") or steam_id
            # Build a fake user object for the presentation layer
            class _Stub:
                pass
            stub = _Stub()
            stub.display_name = display_name
            embed = await _build_inventory_embed(interaction, stub, steam_id)
            await interaction.followup.send(embed=embed)
            return
        target = member or interaction.user
        prof   = cs_profile_get(target.id)
        if not prof or not prof.get("steam_id"):
            await interaction.followup.send(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.no_steam_linked_title"),
                    ti(interaction, "games.cs.err.no_steam_linked_desc", name=target.display_name)),
                ephemeral=True,
            )
            return
        embed = await _build_inventory_embed(interaction, target, prof["steam_id"])
        await interaction.followup.send(embed=embed)

    # ---------- /cs queue ----------
    @cs_group.command(name="queue", description="Create a temporary voice channel for Premier (5 slots)")
    async def cs_queue(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.dm_not_supported_title"),
                    ti(interaction, "games.cs.err.dm_not_supported_desc")),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        vc = await _create_queue_lobby(interaction)
        if vc is None:
            return
        await interaction.followup.send(
            embed=_info_embed(
                ti(interaction, "games.cs.queue.created_title"),
                ti(interaction, "games.cs.queue.created_desc", channel=vc.mention),
                color=0x2ECC71,
            ),
            ephemeral=True,
        )

    # ---------- /cs map ----------
    @cs_group.command(name="map", description="Turn-by-turn ban/pick between the voice channel members")
    async def cs_map(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed(
                    ti(interaction, "games.cs.err.dm_not_supported_title"),
                    ti(interaction, "games.cs.err.dm_not_supported_desc")),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _run_map_ban(interaction)

    # ---------- /cs loadout ----------
    @cs_group.command(name="loadout", description="Generate a random CS2 loadout")
    async def cs_loadout(interaction: discord.Interaction):
        await interaction.response.send_message(embed=_build_loadout_embed(interaction))

    bot.tree.add_command(cs_group)


class _StatsProfileSelectView(discord.ui.View):
    def __init__(self, target: discord.abc.User, prof: dict):
        super().__init__(timeout=60)
        self.target = target
        self.prof   = prof

        async def cb_steam(interaction: discord.Interaction):
            await interaction.response.defer()
            embed = await _build_steam_stats_embed(interaction, self.target, self.prof["steam_id"])
            try:
                await interaction.edit_original_response(embed=embed, view=None)
            except Exception:
                pass

        async def cb_faceit(interaction: discord.Interaction):
            await interaction.response.defer()
            embed = await _build_faceit_stats_embed(interaction,
                                                    self.target,
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
