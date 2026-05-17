"""Slash commands /lol * for League of Legends.

Couvre :
- /lol link        : lier un Riot ID (Name#TAG) + region
- /lol unlink      : retirer le lien
- /lol stats       : afficher rank Solo/Duo + Flex + top masteries (avec emblem)
- /lol rank        : focus sur rank Solo/Duo avec auto-assign rank role
- /lol rankrole    : admin, active/desactive l'attribution auto des rank roles

Securite :
- Aucune cle API loggee ou envoyee a l'utilisateur.
- Validation regex stricte sur Riot ID + region.
"""
from __future__ import annotations

import json as _json
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services import riot_api as riot
from database import (
    lol_profile_get, lol_profile_upsert, lol_profile_unlink,
    lol_rank_config_get, lol_rank_config_upsert,
)


_RIOT_ID_RE = re.compile(r"^(.{3,16})#([A-Za-z0-9]{2,5})$")
_VALID_PLATFORMS = list(riot.PLATFORM_TO_REGIONAL.keys())


def _err_embed(title: str, msg: str) -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=msg, color=0xE74C3C)


def _info_embed(title: str, msg: str, color: int = 0x3498DB) -> discord.Embed:
    return discord.Embed(title=title, description=msg, color=color)


def _wl_ratio(wins: int, losses: int) -> str:
    total = wins + losses
    if total == 0:
        return "0%"
    return f"{(wins / total * 100):.1f}%"


def _format_queue_label(qt: str) -> str:
    return {
        "RANKED_SOLO_5x5": "Solo/Duo",
        "RANKED_FLEX_SR":  "Flex 5v5",
        "RANKED_FLEX_TT":  "Flex 3v3",
    }.get(qt, qt.replace("_", " ").title())


async def _build_stats_embed(member: discord.abc.User, prof: dict) -> discord.Embed:
    platform = prof.get("platform") or "euw1"
    puuid = prof["puuid"]
    summ_id = prof.get("summoner_id")
    name = prof.get("game_name") or "?"
    tag  = prof.get("tag_line") or "?"
    level = prof.get("summoner_level")

    # Si pas de summoner_id, refresh
    if not summ_id:
        s = await riot.summoner_by_puuid(platform, puuid)
        if s:
            summ_id = s.get("id")
            level = s.get("summonerLevel")
            lol_profile_upsert(
                member.id, puuid=puuid, game_name=name, tag_line=tag,
                platform=platform, summoner_id=summ_id, summoner_level=level,
            )

    # Tente by-puuid d'abord (endpoint moderne, plus fiable), fallback by-summoner
    entries = await riot.league_entries_by_puuid(platform, puuid)
    if not entries and summ_id:
        entries = await riot.league_entries_by_summoner(platform, summ_id)
    entries = entries or []
    solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
    flex = next((e for e in entries if e.get("queueType") == "RANKED_FLEX_SR"), None)

    # Couleur embed selon meilleur rank Solo, fallback Flex, fallback gris
    color = 0x747F8D
    primary_tier = "UNRANKED"
    if solo:
        primary_tier = solo.get("tier", "UNRANKED")
        color = riot.TIER_COLOR.get(primary_tier, color)
    elif flex:
        primary_tier = flex.get("tier", "UNRANKED")
        color = riot.TIER_COLOR.get(primary_tier, color)

    embed = discord.Embed(
        title=f"🎮 {name}#{tag}",
        description=f"**{riot.PLATFORM_LABEL.get(platform, platform.upper())}** · Niveau **{level or '?'}**",
        color=color,
    )
    # Emblem en image (plus grand qu'en thumbnail) si user est classe
    if primary_tier != "UNRANKED":
        embed.set_image(url=riot.tier_emblem_url(primary_tier))
    else:
        embed.set_thumbnail(url=riot.tier_emblem_url(primary_tier))

    def _fmt_entry(e):
        if not e:
            return "_Non classé._"
        tier = e.get("tier", "UNRANKED")
        rank = e.get("rank", "")
        lp   = e.get("leaguePoints", 0)
        wins = e.get("wins", 0)
        losses = e.get("losses", 0)
        return (
            f"**{riot.rank_label_fr(tier, rank)}** · `{lp} LP`\n"
            f"`{wins}V` `{losses}D` · WR {_wl_ratio(wins, losses)}"
        )

    embed.add_field(name="🏆 Solo/Duo", value=_fmt_entry(solo), inline=True)
    embed.add_field(name="⚔️ Flex 5v5", value=_fmt_entry(flex), inline=True)

    # Top 3 masteries
    masteries = await riot.mastery_top(platform, puuid, count=3)
    if masteries:
        lines = []
        for m in masteries:
            cname = await riot.champion_name(m.get("championId", 0))
            pts   = int(m.get("championPoints", 0))
            mlvl  = m.get("championLevel", 0)
            lines.append(f"**{cname}** · Niv. `{mlvl}` · `{pts:,}` pts".replace(",", " "))
        embed.add_field(name="✨ Top maîtrises", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Riot ID : {name}#{tag} · Données via Riot Games API")
    return embed


# ===== Rank role helpers =====
def _build_default_role_map():
    """Tier (UPPERCASE) -> role_id None (a remplir via dashboard plus tard)."""
    return {t: None for t in (
        "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM",
        "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
    )}


async def _apply_rank_role(member: discord.Member, tier: str) -> Optional[str]:
    """Si la guild a une config rank role active, applique le bon role.
    Retourne le nom du role applique ou None."""
    if not isinstance(member, discord.Member):
        return None
    cfg = lol_rank_config_get(member.guild.id)
    if not cfg.get("enabled"):
        return None
    raw = cfg.get("role_map")
    if not raw:
        return None
    try:
        role_map = _json.loads(raw)
    except Exception:
        return None
    target_role_id = role_map.get((tier or "").upper())
    if not target_role_id:
        return None
    new_role = member.guild.get_role(int(target_role_id))
    if not new_role:
        return None

    # Retire les autres rank roles avant d'appliquer le nouveau
    other_ids = {int(rid) for rid in role_map.values() if rid and rid != target_role_id}
    to_remove = [r for r in member.roles if r.id in other_ids]
    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason="LoL rank role update")
        if new_role not in member.roles:
            await member.add_roles(new_role, reason="LoL rank role auto")
    except discord.Forbidden:
        print(f"[lol/rankrole] forbidden : bot manque manage_roles ou role trop haut (guild {member.guild.id})")
        return None
    except Exception as e:
        print(f"[lol/rankrole] err : {type(e).__name__}: {e}")
        return None
    return new_role.name


def setup_lol_commands(bot):
    lol_group = app_commands.Group(name="lol", description="League of Legends : link, stats, rank")

    # ---------- /lol link ----------
    @lol_group.command(name="link", description="Lier ton Riot ID (Pseudo#TAG)")
    @app_commands.describe(
        riot_id="Ton Riot ID au format Pseudo#TAG (ex: Tookyn#EUW)",
        region="Region serveur (EUW par defaut)",
    )
    @app_commands.choices(region=[
        app_commands.Choice(name="EUW (Europe West)", value="euw1"),
        app_commands.Choice(name="EUNE (Europe Nord/Est)", value="eun1"),
        app_commands.Choice(name="NA (Amerique Nord)", value="na1"),
        app_commands.Choice(name="KR (Corée)", value="kr"),
        app_commands.Choice(name="BR (Brésil)", value="br1"),
        app_commands.Choice(name="LAN (Latine Nord)", value="la1"),
        app_commands.Choice(name="LAS (Latine Sud)", value="la2"),
        app_commands.Choice(name="JP (Japon)", value="jp1"),
        app_commands.Choice(name="OCE (Océanie)", value="oc1"),
        app_commands.Choice(name="TR (Turquie)", value="tr1"),
    ])
    async def lol_link(interaction: discord.Interaction, riot_id: str,
                       region: Optional[app_commands.Choice[str]] = None):
        await interaction.response.defer(ephemeral=True)
        platform = (region.value if region else "euw1").lower()
        m = _RIOT_ID_RE.match((riot_id or "").strip())
        if not m:
            await interaction.followup.send(
                embed=_err_embed("Format invalide",
                    "Format attendu : `Pseudo#TAG` (ex: `Tookyn#EUW`).\n"
                    "Le tag a 2 à 5 caractères, sans espace."),
                ephemeral=True,
            )
            return
        game_name, tag_line = m.group(1), m.group(2)

        account = await riot.account_by_riot_id(platform, game_name, tag_line)
        if not account or not account.get("puuid"):
            await interaction.followup.send(
                embed=_err_embed("Riot ID introuvable",
                    f"`{game_name}#{tag_line}` n'a pas été trouvé sur la région **{riot.PLATFORM_LABEL.get(platform, platform)}**.\n\n"
                    "Vérifie l'orthographe et la région."),
                ephemeral=True,
            )
            return
        puuid = account["puuid"]

        summ = await riot.summoner_by_puuid(platform, puuid)
        summ_id = (summ or {}).get("id")
        summ_level = (summ or {}).get("summonerLevel")

        lol_profile_upsert(
            interaction.user.id,
            puuid=puuid,
            game_name=account.get("gameName") or game_name,
            tag_line=account.get("tagLine") or tag_line,
            platform=platform,
            summoner_id=summ_id,
            summoner_level=summ_level,
        )
        await interaction.followup.send(
            embed=_info_embed(
                "✅ Compte LoL lié",
                f"**{account.get('gameName') or game_name}#{account.get('tagLine') or tag_line}** sur **{riot.PLATFORM_LABEL.get(platform, platform)}**.\n"
                f"Niveau invocateur : **{summ_level or '?'}**.\n\n"
                "Tape `/lol stats` pour voir ton profil complet.",
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /lol unlink ----------
    @lol_group.command(name="unlink", description="Retirer le lien de ton compte LoL")
    async def lol_unlink(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n = lol_profile_unlink(interaction.user.id)
        if n == 0:
            await interaction.followup.send(
                embed=_err_embed("Pas de compte lié",
                    "Tu n'as aucun compte LoL lié à supprimer."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=_info_embed("✅ Compte LoL délié",
                "Le lien a été supprimé. Tu peux refaire `/lol link` quand tu veux.",
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /lol stats ----------
    @lol_group.command(name="stats", description="Affiche les stats LoL d'un joueur")
    @app_commands.describe(membre="Membre Discord à inspecter (optionnel)")
    async def lol_stats(interaction: discord.Interaction,
                        membre: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = membre or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed("Aucun compte lié",
                    f"**{target.display_name}** n'a pas de compte LoL lié.\n"
                    "Utilise `/lol link Pseudo#TAG` pour en lier un."),
                ephemeral=True,
            )
            return
        embed = await _build_stats_embed(target, prof)
        await interaction.followup.send(embed=embed)

    # ---------- /lol rank ----------
    @lol_group.command(name="rank", description="Affiche ton rank Solo/Duo et applique le role si configuré")
    @app_commands.describe(membre="Membre Discord (optionnel)")
    async def lol_rank(interaction: discord.Interaction,
                       membre: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = membre or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed("Aucun compte lié",
                    f"**{target.display_name}** n'a pas de compte LoL lié.\n"
                    "Utilise `/lol link` d'abord."),
                ephemeral=True,
            )
            return

        platform = prof.get("platform") or "euw1"
        puuid    = prof["puuid"]
        summ_id  = prof.get("summoner_id")
        if not summ_id:
            s = await riot.summoner_by_puuid(platform, puuid)
            summ_id = (s or {}).get("id")

        entries = await riot.league_entries_by_puuid(platform, puuid)
        if not entries and summ_id:
            entries = await riot.league_entries_by_summoner(platform, summ_id)
        entries = entries or []
        solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)

        tier = (solo or {}).get("tier", "UNRANKED")
        rank = (solo or {}).get("rank", "")
        lp   = (solo or {}).get("leaguePoints", 0)
        wins = (solo or {}).get("wins", 0)
        losses = (solo or {}).get("losses", 0)
        color = riot.TIER_COLOR.get(tier, 0x747F8D)

        embed = discord.Embed(
            title=f"🏆 Rank — {target.display_name}",
            description=(
                f"**{prof.get('game_name')}#{prof.get('tag_line')}** "
                f"({riot.PLATFORM_LABEL.get(platform, platform.upper())})"
            ),
            color=color,
        )
        # set_image (grand) si classe, sinon thumbnail (petit)
        if tier != "UNRANKED":
            embed.set_image(url=riot.tier_emblem_url(tier))
        else:
            embed.set_thumbnail(url=riot.tier_emblem_url(tier))
        if solo:
            embed.add_field(
                name="Solo/Duo",
                value=(f"**{riot.rank_label_fr(tier, rank)}** · `{lp} LP`\n"
                       f"`{wins}V` `{losses}D` · WR {_wl_ratio(wins, losses)}"),
                inline=False,
            )
        else:
            embed.add_field(name="Solo/Duo", value="_Non classé._", inline=False)

        # Auto rank role
        applied = None
        if isinstance(target, discord.Member):
            applied = await _apply_rank_role(target, tier)
        if applied:
            embed.set_footer(text=f"✅ Role «{applied}» appliqué automatiquement.")

        await interaction.followup.send(embed=embed)

    # ---------- /lol rankrole ----------
    @lol_group.command(name="rankrole",
                       description="Admin : active/desactive l'attribution auto des rank roles LoL")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="Activer",    value="on"),
        app_commands.Choice(name="Désactiver", value="off"),
    ])
    async def lol_rankrole(interaction: discord.Interaction,
                           action: app_commands.Choice[str]):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed("Pas dispo en DM", "Lance cette commande dans un serveur."),
                ephemeral=True,
            )
            return
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                embed=_err_embed("Permission refusée",
                    "Tu as besoin de la permission **Gérer les rôles**."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)

        # Initialise la role_map vide si pas encore set
        cur = lol_rank_config_get(interaction.guild.id)
        role_map_arg = None
        if not cur.get("role_map"):
            role_map_arg = _build_default_role_map()

        lol_rank_config_upsert(
            interaction.guild.id,
            enabled=(1 if action.value == "on" else 0),
            role_map=role_map_arg,
        )
        await interaction.followup.send(
            embed=_info_embed(
                "🏆 Rank role LoL",
                f"L'attribution auto des rank roles est maintenant "
                f"**{'activée' if action.value == 'on' else 'désactivée'}** sur ce serveur.\n"
                f"Configure les rôles par palier dans le dashboard "
                f"(page League of Legends, à venir dans batch 2).",
                color=0x2ECC71 if action.value == "on" else 0xE67E22,
            ),
            ephemeral=True,
        )

    bot.tree.add_command(lol_group)
