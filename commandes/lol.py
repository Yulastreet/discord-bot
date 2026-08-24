"""Slash commands /lol * for League of Legends.

Covers:
- /lol link        : link a Riot ID (Name#TAG) + region
- /lol unlink      : remove the link
- /lol stats       : show Solo/Duo + Flex rank + top masteries (with emblem)
- /lol rank        : focus on the Solo/Duo rank with auto-assigned rank role
- /lol rankrole    : admin, enable/disable automatic rank role assignment

Security:
- No API key is ever logged or sent to the user.
- Strict regex validation on the Riot ID + region.
"""
from __future__ import annotations

import asyncio
import json as _json
import os
import re
from typing import Optional

import discord
from discord import app_commands

from services import riot_api as riot
from services.i18n import t, ti, locale_of
from database import (
    lol_profile_get, lol_profile_upsert, lol_profile_unlink,
    lol_rank_config_get, lol_rank_config_upsert,
    lol_scout_session_create,
    lol_scout_session_stop, lol_scout_sessions_list,
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


async def _attach_emblem(embed: discord.Embed, tier: str):
    """Attach the local emblem file + set_thumbnail attachment when available.
    Otherwise fall back to the remote URL."""
    if tier == "UNRANKED":
        embed.set_thumbnail(url=riot.tier_emblem_url(tier))
        return None
    path = await riot.tier_emblem_file_path(tier)
    if path:
        fname = f"emblem_{tier.lower()}.png"
        embed.set_thumbnail(url=f"attachment://{fname}")
        return discord.File(path, filename=fname)
    # Fallback: remote URL (Discord may pixelate it)
    embed.set_thumbnail(url=riot.tier_emblem_url(tier))
    return None


async def _build_stats_embed(member: discord.abc.User, prof: dict,
                             locale: str = "en") -> discord.Embed:
    platform = prof.get("platform") or "euw1"
    puuid = prof["puuid"]
    summ_id = prof.get("summoner_id")
    name = prof.get("game_name") or "?"
    tag  = prof.get("tag_line") or "?"
    level = prof.get("summoner_level")

    # No summoner_id yet: refresh (with auto-recovery on a stale puuid)
    if not summ_id:
        try:
            s = await riot.summoner_by_puuid(platform, puuid)
        except riot.RiotPuuidStaleError:
            new_prof = await _refresh_puuid_if_stale(member.id if hasattr(member, 'id') else 0, prof)
            if new_prof:
                prof = new_prof
                puuid = prof["puuid"]
                summ_id = prof.get("summoner_id")
                name = prof.get("game_name") or name
                tag = prof.get("tag_line") or tag
                level = prof.get("summoner_level") or level
            s = None
        if s:
            summ_id = s.get("id")
            level = s.get("summonerLevel")
            if hasattr(member, 'id') and member.id:
                lol_profile_upsert(
                    member.id, puuid=puuid, game_name=name, tag_line=tag,
                    platform=platform, summoner_id=summ_id, summoner_level=level,
                )

    # Try by-puuid first (modern, more reliable endpoint), fall back to by-summoner
    try:
        entries = await riot.league_entries_by_puuid(platform, puuid)
    except riot.RiotPuuidStaleError:
        new_prof = await _refresh_puuid_if_stale(member.id if hasattr(member, 'id') else 0, prof)
        if new_prof:
            prof = new_prof
            puuid = prof["puuid"]
            summ_id = prof.get("summoner_id")
            entries = await riot.league_entries_by_puuid(platform, puuid)
        else:
            entries = None
    if not entries and summ_id:
        entries = await riot.league_entries_by_summoner(platform, summ_id)
    entries = entries or []
    solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
    flex = next((e for e in entries if e.get("queueType") == "RANKED_FLEX_SR"), None)

    # Embed color from the best Solo rank, then Flex, then grey
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
        description=t("games.lol.stats.subtitle", locale,
                      region=riot.PLATFORM_LABEL.get(platform, platform.upper()),
                      level=level or "?"),
        color=color,
    )

    def _fmt_entry(e):
        if not e:
            return t("games.lol.unranked", locale)
        tier = e.get("tier", "UNRANKED")
        rank = e.get("rank", "")
        lp   = e.get("leaguePoints", 0)
        wins = e.get("wins", 0)
        losses = e.get("losses", 0)
        return t("games.lol.rank_line", locale,
                 rank=riot.rank_label(tier, rank), lp=lp,
                 wins=wins, losses=losses, wr=_wl_ratio(wins, losses))

    embed.add_field(name="🏆 Solo/Duo", value=_fmt_entry(solo), inline=True)
    embed.add_field(name="⚔️ Flex 5v5", value=_fmt_entry(flex), inline=True)

    # Top 3 masteries
    try:
        masteries = await riot.mastery_top(platform, puuid, count=3)
    except riot.RiotPuuidStaleError:
        masteries = None
    if masteries:
        lines = []
        for m in masteries:
            cname = await riot.champion_name(m.get("championId", 0))
            pts   = int(m.get("championPoints", 0))
            mlvl  = m.get("championLevel", 0)
            lines.append(t("games.lol.mastery_line", locale, champion=cname,
                           level=mlvl, points=f"{pts:,}".replace(",", " ")))
        embed.add_field(name=t("games.lol.stats.top_masteries", locale),
                        value="\n".join(lines), inline=False)

    embed.set_footer(text=t("games.lol.stats.footer", locale, riot_id=f"{name}#{tag}"))
    file = await _attach_emblem(embed, primary_tier)
    return embed, file


# ===== Rank role helpers =====
def _build_default_role_map():
    """Tier (UPPERCASE) -> role_id None (filled in from the dashboard later)."""
    return {t: None for t in (
        "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM",
        "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
    )}


async def _apply_rank_role(member: discord.Member, tier: str) -> Optional[str]:
    """Apply the matching role when the guild has rank roles enabled.
    Returns the applied role name, or None."""
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

    # Remove the other rank roles before applying the new one
    other_ids = {int(rid) for rid in role_map.values() if rid and rid != target_role_id}
    to_remove = [r for r in member.roles if r.id in other_ids]
    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason="LoL rank role update")
        if new_role not in member.roles:
            await member.add_roles(new_role, reason="LoL rank role auto")
    except discord.Forbidden:
        print(f"[lol/rankrole] forbidden: bot lacks manage_roles or the role is too high (guild {member.guild.id})")
        return None
    except Exception as e:
        print(f"[lol/rankrole] err: {type(e).__name__}: {e}")
        return None
    return new_role.name


_VIEW_ITEMS = "items"
_VIEW_RUNES = "runes"
_VIEW_SKILLS = "skills"

_SKILL_LETTER = {1: "Q", 2: "W", 3: "E", 4: "R"}


def _build_subtitle(build: dict, cname: str, role_label: str,
                    source: str, source_url: str, locale: str = "en") -> str:
    """Shared build header line (+ optional winrate line)."""
    out = t("games.lol.build.subtitle", locale, champion=cname, role=role_label,
            source=source, source_url=source_url)
    if build.get("wr") is not None:
        out += t("games.lol.build.wr_line", locale,
                 wr=f"{build['wr']:.1f}",
                 matches=f"{build['matches']:,}".replace(",", " "))
    return out


async def _render_items_view(build: dict, cname: str, champion_id: int,
                              role_label: str, source: str, source_url: str,
                              locale: str = "en"):
    """Items view: embed with items per phase + composite PNG (items + spells)."""
    embed = discord.Embed(
        title=t("games.lol.build.items_title", locale, build=build.get("name", "Build")),
        description=_build_subtitle(build, cname, role_label, source, source_url, locale),
        color=0xF1C40F,
    )
    for phase in (build.get("items_by_phase") or [])[:6]:
        ptype = phase.get("type") or "?"
        ids = phase.get("items") or []
        if not ids:
            continue
        names = []
        for iid in ids[:8]:
            try:
                names.append(await riot.item_name(int(iid)))
            except Exception:
                names.append(f"#{iid}")
        embed.add_field(
            name=t("games.lol.build.items_phase", locale, phase=ptype),
            value=" → ".join(f"`{n}`" for n in names) or "—",
            inline=False,
        )

    # Composite image: items + spells
    all_items = []
    for phase in (build.get("items_by_phase") or []):
        for iid in (phase.get("items") or []):
            try:
                all_items.append(int(iid))
            except Exception:
                pass
    spells = build.get("summoner_spells") or []
    img_bytes = await riot.compose_build_image(all_items[:12], [int(s) for s in spells[:2]])
    file = None
    if img_bytes:
        from io import BytesIO
        file = discord.File(BytesIO(img_bytes), filename="build.png")
        embed.set_image(url="attachment://build.png")
    else:
        icon = await riot.champion_icon_url(champion_id)
        if icon:
            embed.set_thumbnail(url=icon)
    embed.set_footer(text=t("games.lol.build.items_footer", locale, source=source))
    return embed, file


async def _render_runes_view(build: dict, cname: str, champion_id: int,
                              role_label: str, source: str, source_url: str,
                              locale: str = "en"):
    """Runes view: embed with the full rune page (keystone + primary 3 +
    secondary 2 + 3 stat shards)."""
    embed = discord.Embed(
        title=t("games.lol.build.runes_title", locale, build=build.get("name", "Build")),
        description=_build_subtitle(build, cname, role_label, source, source_url, locale),
        color=0xF1C40F,
    )
    perk_ids = build.get("perk_ids") or []
    primary_tree_id = build.get("primary_style")
    sub_tree_id     = build.get("sub_style")

    if not perk_ids:
        embed.add_field(name="—", value=t("games.lol.build.no_runes", locale), inline=False)
        icon = await riot.champion_icon_url(champion_id)
        if icon:
            embed.set_thumbnail(url=icon)
        return embed, None

    # Typical layout: keystone + 3 primary + 2 secondary + 3 shards = 9 ids
    keystone     = perk_ids[0] if len(perk_ids) > 0 else None
    primary_3    = perk_ids[1:4]
    secondary_2  = perk_ids[4:6]
    shards       = perk_ids[6:9]

    tree_primary_name   = await riot.rune_name(primary_tree_id) if primary_tree_id else "?"
    tree_secondary_name = await riot.rune_name(sub_tree_id) if sub_tree_id else "?"

    primary_lines = []
    if keystone:
        primary_lines.append(t("games.lol.build.keystone_line", locale,
                               rune=await riot.rune_name(keystone)))
    for rid in primary_3:
        primary_lines.append(t("games.lol.build.rune_line", locale,
                               rune=await riot.rune_name(rid)))
    embed.add_field(
        name=t("games.lol.build.primary_tree", locale, tree=tree_primary_name),
        value="\n".join(primary_lines) or "—",
        inline=True,
    )

    secondary_lines = []
    for rid in secondary_2:
        secondary_lines.append(t("games.lol.build.rune_line", locale,
                                 rune=await riot.rune_name(rid)))
    embed.add_field(
        name=t("games.lol.build.secondary_tree", locale, tree=tree_secondary_name),
        value="\n".join(secondary_lines) or "—",
        inline=True,
    )

    shard_lines = []
    shard_slot_labels = ["Offense", "Flex", "Defense"]
    for i, rid in enumerate(shards):
        label = shard_slot_labels[i] if i < 3 else ""
        rune_nm = await riot.rune_name(rid)
        shard_lines.append(
            t("games.lol.build.shard_line", locale, slot=label, rune=rune_nm)
            if label else f"**{rune_nm}**"
        )
    embed.add_field(
        name=t("games.lol.build.stat_shards", locale),
        value="\n".join(shard_lines) or "—",
        inline=False,
    )

    # Thumbnail = icon keystone
    if keystone:
        icon = await riot.rune_icon_url(keystone)
        if icon:
            embed.set_thumbnail(url=icon)
    embed.set_footer(text=t("games.lol.build.runes_footer", locale, source=source))
    return embed, None


async def _render_skills_view(build: dict, cname: str, champion_id: int,
                              role_label: str, source: str, source_url: str,
                              locale: str = "en"):
    """Skills + summoners view: skill order + summoner spells."""
    embed = discord.Embed(
        title=t("games.lol.build.skills_title", locale, build=build.get("name", "Build")),
        description=_build_subtitle(build, cname, role_label, source, source_url, locale),
        color=0xF1C40F,
    )

    # Skill order (1=Q, 2=W, 3=E, 4=R)
    so = build.get("skill_order") or []
    if so:
        # Visual table 1..18
        line_letters = []
        line_levels  = []
        for lvl, s in enumerate(so[:18], 1):
            line_letters.append(_SKILL_LETTER.get(int(s), "?"))
            line_levels.append(f"{lvl:2d}")
        embed.add_field(
            name=t("games.lol.build.skill_order", locale),
            value=t("games.lol.build.skill_order_value", locale,
                    levels=" ".join(line_levels),
                    skills=" ".join(f"{l:>2}" for l in line_letters)),
            inline=False,
        )

    # Max-out priority
    smo = build.get("skill_max_order") or []
    if smo:
        prio = " > ".join(_SKILL_LETTER.get(int(s), "?") for s in smo)
        embed.add_field(name=t("games.lol.build.max_priority", locale),
                        value=f"`{prio}`", inline=False)

    # Summoner spells
    spells = build.get("summoner_spells") or []
    if spells:
        names = [riot.SUMMONER_SPELL_NAMES.get(int(s), f"Spell #{s}") for s in spells[:2]]
        embed.add_field(name=t("games.lol.build.summoner_spells", locale),
                        value=" + ".join(f"**{n}**" for n in names),
                        inline=False)

    icon = await riot.champion_icon_url(champion_id)
    if icon:
        embed.set_thumbnail(url=icon)
    embed.set_footer(text=t("games.lol.build.skills_footer", locale, source=source))
    return embed, None


async def _render_build(build: dict, view_kind: str, cname: str, champion_id: int,
                        role_label: str, source: str, source_url: str,
                        locale: str = "en"):
    """Dispatch to the selected view."""
    if view_kind == _VIEW_RUNES:
        return await _render_runes_view(build, cname, champion_id, role_label,
                                        source, source_url, locale)
    if view_kind == _VIEW_SKILLS:
        return await _render_skills_view(build, cname, champion_id, role_label,
                                         source, source_url, locale)
    return await _render_items_view(build, cname, champion_id, role_label,
                                    source, source_url, locale)


# Backwards-compat (used elsewhere)
async def _render_build_embed(build, cname, champion_id, role_label, source, source_url,
                              locale="en"):
    return await _render_items_view(build, cname, champion_id, role_label,
                                    source, source_url, locale)


class LolBuildView(discord.ui.View):
    """View with 2 rows of buttons:
      - Row 0: build selection (1-5 buttons, Mobalytics types)
      - Row 1: view selection (Items / Runes / Skills+Spells)
    A click edits the same message."""

    def __init__(self, author_id: int, builds: list[dict], cname: str,
                 cslug: str, champion_id: int, role_label: str,
                 source: str, source_url: str,
                 selected_build: int = 0, selected_view: str = _VIEW_ITEMS,
                 locale: str = "en"):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.locale = locale
        self.builds = builds
        self.cname = cname
        self.cslug = cslug
        self.champion_id = champion_id
        self.role_label = role_label
        self.source = source
        self.source_url = source_url
        self.selected_build = selected_build
        self.selected_view = selected_view
        self._rebuild_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Block every user other than the author."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                ti(interaction, "games.lol.build.not_your_menu"),
                ephemeral=True,
            )
            return False
        return True

    def _rebuild_buttons(self):
        self.clear_items()
        # Row 0: builds (max 5)
        for idx, b in enumerate(self.builds[:5]):
            label = b.get("name") or t("games.lol.build.default_name", self.locale, n=idx + 1)
            wr = b.get("wr")
            if wr is not None:
                label = f"{label} {wr:.1f}%"
            btn = discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.primary if idx == self.selected_build else discord.ButtonStyle.secondary,
                custom_id=f"lolbuild:{idx}",
                row=0,
            )
            btn.callback = self._make_build_cb(idx)
            self.add_item(btn)
        # Row 1: views
        for kind, label, emoji in (
            (_VIEW_ITEMS,  t("games.lol.build.btn_items", self.locale),  "🧱"),
            (_VIEW_RUNES,  t("games.lol.build.btn_runes", self.locale),  "🌿"),
            (_VIEW_SKILLS, t("games.lol.build.btn_skills", self.locale), "⚔️"),
        ):
            btn = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.success if kind == self.selected_view else discord.ButtonStyle.secondary,
                custom_id=f"lolview:{kind}",
                row=1,
            )
            btn.callback = self._make_view_cb(kind)
            self.add_item(btn)

    def _make_build_cb(self, idx: int):
        async def cb(interaction: discord.Interaction):
            self.selected_build = idx
            await self._refresh(interaction)
        return cb

    def _make_view_cb(self, kind: str):
        async def cb(interaction: discord.Interaction):
            self.selected_view = kind
            await self._refresh(interaction)
        return cb

    async def _refresh(self, interaction: discord.Interaction):
        build = self.builds[self.selected_build]
        embed, file = await _render_build(
            build, self.selected_view, self.cname, self.champion_id,
            self.role_label, self.source,
            build.get("source_url") or self.source_url,
            self.locale,
        )
        self._rebuild_buttons()
        if file:
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        else:
            await interaction.response.edit_message(embed=embed, attachments=[], view=self)


# Autocomplete helpers (shared by every sub-command below)
async def _champion_autocomplete(interaction: discord.Interaction, current: str):
    await riot._dd_refresh()
    champs = list(riot._DD_CACHE["champions"].values())
    cur = (current or "").strip().lower()
    if cur:
        matches = [c for c in champs if cur in c["name"].lower() or cur in c["slug"].lower()]
    else:
        matches = champs
    matches.sort(key=lambda c: c["name"])
    return [app_commands.Choice(name=c["name"], value=c["name"]) for c in matches[:25]]


_SKIN_CACHE: dict = {}  # slug -> list of names (Meraki already caches for 6h)


async def _skin_autocomplete(interaction: discord.Interaction, current: str):
    try:
        champ_input = (interaction.namespace.champion or "").strip()
    except Exception:
        champ_input = ""
    if not champ_input:
        return []
    await riot._dd_refresh()
    champs = riot._DD_CACHE["champions"]
    cname_lower = champ_input.lower()
    slug = None
    for cid, info in champs.items():
        if info["name"].lower() == cname_lower or info["slug"].lower() == cname_lower:
            slug = info["slug"]
            break
    if not slug:
        return []
    cached_names = _SKIN_CACHE.get(slug)
    if cached_names is None:
        meraki = await riot.meraki_champion(slug)
        skins = (meraki or {}).get("skins") or []
        cached_names = [s.get("name") for s in skins if s.get("name")]
        _SKIN_CACHE[slug] = cached_names
    cur = (current or "").strip().lower()
    if cur:
        matches = [n for n in cached_names if cur in n.lower()]
    else:
        matches = cached_names
    return [app_commands.Choice(name=n[:100], value=n) for n in matches[:25]]


_REGION_CHOICES = [
    app_commands.Choice(name="EUW (Europe West)", value="euw1"),
    app_commands.Choice(name="EUNE (Europe Nordic & East)", value="eun1"),
    app_commands.Choice(name="NA (North America)", value="na1"),
    app_commands.Choice(name="KR (Korea)", value="kr"),
    app_commands.Choice(name="BR (Brazil)", value="br1"),
    app_commands.Choice(name="LAN (Latin America North)", value="la1"),
    app_commands.Choice(name="LAS (Latin America South)", value="la2"),
    app_commands.Choice(name="JP (Japan)", value="jp1"),
    app_commands.Choice(name="OCE (Oceania)", value="oc1"),
    app_commands.Choice(name="TR (Turkey)", value="tr1"),
]


class _StubUser:
    """Fake user for /lol stats|rank when riot_id is passed without a member."""
    def __init__(self, name: str, user_id: int = 0):
        self.display_name = name
        self.name = name
        self.id = user_id
        self.display_avatar = None


async def _refresh_puuid_if_stale(user_id, prof: dict) -> Optional[dict]:
    """Re-run the Account API call with the stored game_name + tag_line to get
    a puuid encrypted with the CURRENT API key. Updates the DB.
    Returns the new profile, or None on failure."""
    platform = prof.get("platform") or "euw1"
    gname = prof.get("game_name")
    tag   = prof.get("tag_line")
    if not gname or not tag:
        return None
    account = await riot.account_by_riot_id(platform, gname, tag)
    if not account or not account.get("puuid"):
        return None
    new_puuid = account["puuid"]
    summ = await riot.summoner_by_puuid(platform, new_puuid)
    lol_profile_upsert(
        user_id,
        puuid=new_puuid,
        game_name=account.get("gameName") or gname,
        tag_line=account.get("tagLine") or tag,
        platform=platform,
        summoner_id=(summ or {}).get("id"),
        summoner_level=(summ or {}).get("summonerLevel"),
    )
    print(f"[lol] puuid refresh user={user_id} platform={platform} new_prefix={new_puuid[:10]}")
    return lol_profile_get(user_id)


async def _resolve_target_or_riot(interaction: discord.Interaction,
                                   member: Optional[discord.Member],
                                   riot_id: Optional[str],
                                   region: Optional[app_commands.Choice[str]]):
    """Return (target, profile_dict), or (None, None) on error.
    When riot_id is given: resolve through the Riot API + a stub user.
    Otherwise: the member DB profile (or the caller's when no member is given)."""
    if riot_id:
        platform = (region.value if region else "euw1").lower()
        m = _RIOT_ID_RE.match((riot_id or "").strip())
        if not m:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.invalid_format_title"),
                    ti(interaction, "games.lol.err.invalid_format_desc")),
                ephemeral=True,
            )
            return None, None
        game_name, tag_line = m.group(1), m.group(2)
        account = await riot.account_by_riot_id(platform, game_name, tag_line)
        if not account or not account.get("puuid"):
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.riot_id_not_found_title"),
                    ti(interaction, "games.lol.err.riot_id_not_found_desc",
                       riot_id=f"{game_name}#{tag_line}",
                       region=riot.PLATFORM_LABEL.get(platform, platform))),
                ephemeral=True,
            )
            return None, None
        puuid = account["puuid"]
        summ = await riot.summoner_by_puuid(platform, puuid)
        prof = {
            "puuid":          puuid,
            "summoner_id":    (summ or {}).get("id"),
            "game_name":      account.get("gameName") or game_name,
            "tag_line":       account.get("tagLine") or tag_line,
            "platform":       platform,
            "summoner_level": (summ or {}).get("summonerLevel"),
        }
        stub = _StubUser(f"{prof['game_name']}#{prof['tag_line']}")
        return stub, prof

    # Fallback: member or caller
    target = member or interaction.user
    prof = lol_profile_get(target.id)
    if not prof:
        await interaction.followup.send(
            embed=_err_embed(ti(interaction, "games.lol.err.no_account_title"),
                ti(interaction, "games.lol.err.no_account_desc", name=target.display_name)),
            ephemeral=True,
        )
        return None, None
    return target, prof


def setup_lol_commands(bot):
    lol_group = app_commands.Group(name="lol", description="League of Legends: link, stats, rank")

    # ---------- /lol link ----------
    @lol_group.command(name="link", description="Link your Riot ID (Name#TAG)")
    @app_commands.describe(
        riot_id="Your Riot ID in the Name#TAG format (e.g. Tookyn#EUW)",
        region="Server region (EUW by default)",
    )
    @app_commands.choices(region=_REGION_CHOICES)
    async def lol_link(interaction: discord.Interaction, riot_id: str,
                       region: Optional[app_commands.Choice[str]] = None):
        await interaction.response.defer(ephemeral=True)
        platform = (region.value if region else "euw1").lower()
        m = _RIOT_ID_RE.match((riot_id or "").strip())
        if not m:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.invalid_format_title"),
                    ti(interaction, "games.lol.err.invalid_format_link_desc")),
                ephemeral=True,
            )
            return
        game_name, tag_line = m.group(1), m.group(2)

        account = await riot.account_by_riot_id(platform, game_name, tag_line)
        if not account or not account.get("puuid"):
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.riot_id_not_found_title"),
                    ti(interaction, "games.lol.err.riot_id_not_found_link_desc",
                       riot_id=f"{game_name}#{tag_line}",
                       region=riot.PLATFORM_LABEL.get(platform, platform))),
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
                ti(interaction, "games.lol.link.success_title"),
                ti(interaction, "games.lol.link.success_desc",
                   riot_id=f"{account.get('gameName') or game_name}#{account.get('tagLine') or tag_line}",
                   region=riot.PLATFORM_LABEL.get(platform, platform),
                   level=summ_level or "?"),
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /lol unlink ----------
    @lol_group.command(name="unlink", description="Remove the link to your LoL account")
    async def lol_unlink(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n = lol_profile_unlink(interaction.user.id)
        if n == 0:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.unlink.none_title"),
                    ti(interaction, "games.lol.unlink.none_desc")),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=_info_embed(ti(interaction, "games.lol.unlink.success_title"),
                ti(interaction, "games.lol.unlink.success_desc"),
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /lol stats ----------
    @lol_group.command(name="stats", description="Show a player's LoL stats (Discord member or Riot ID)")
    @app_commands.describe(
        member="Discord member to inspect (optional)",
        riot_id="Or a Riot ID directly (Name#TAG)",
        region="Region when you use riot_id (EUW by default)",
    )
    @app_commands.choices(region=_REGION_CHOICES)
    async def lol_stats(interaction: discord.Interaction,
                        member: Optional[discord.Member] = None,
                        riot_id: Optional[str] = None,
                        region: Optional[app_commands.Choice[str]] = None):
        await interaction.response.defer()
        if member and riot_id:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.ambiguous_params_title"),
                    ti(interaction, "games.lol.err.ambiguous_params_desc")),
                ephemeral=True,
            )
            return

        target, prof = await _resolve_target_or_riot(interaction, member, riot_id, region)
        if prof is None:
            return
        embed, file = await _build_stats_embed(target, prof, locale_of(interaction))
        if file:
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(embed=embed)

    # ---------- /lol rank ----------
    @lol_group.command(name="rank", description="Show your Solo/Duo rank (Discord member or Riot ID)")
    @app_commands.describe(
        member="Discord member (optional)",
        riot_id="Or a Riot ID directly (Name#TAG)",
        region="Region when you use riot_id (EUW by default)",
    )
    @app_commands.choices(region=_REGION_CHOICES)
    async def lol_rank(interaction: discord.Interaction,
                       member: Optional[discord.Member] = None,
                       riot_id: Optional[str] = None,
                       region: Optional[app_commands.Choice[str]] = None):
        await interaction.response.defer()
        if member and riot_id:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.ambiguous_params_title"),
                    ti(interaction, "games.lol.err.ambiguous_params_desc")),
                ephemeral=True,
            )
            return
        target, prof = await _resolve_target_or_riot(interaction, member, riot_id, region)
        if prof is None:
            return

        platform = prof.get("platform") or "euw1"
        puuid    = prof["puuid"]
        summ_id  = prof.get("summoner_id")
        if not summ_id:
            try:
                s = await riot.summoner_by_puuid(platform, puuid)
            except riot.RiotPuuidStaleError:
                new_prof = await _refresh_puuid_if_stale(target.id if hasattr(target, 'id') else 0, prof)
                if new_prof:
                    prof = new_prof
                    puuid = prof["puuid"]
                    summ_id = prof.get("summoner_id")
                s = None
            if s:
                summ_id = (s or {}).get("id")

        try:
            entries = await riot.league_entries_by_puuid(platform, puuid)
        except riot.RiotPuuidStaleError:
            new_prof = await _refresh_puuid_if_stale(target.id if hasattr(target, 'id') else 0, prof)
            if new_prof:
                prof = new_prof
                puuid = prof["puuid"]
                summ_id = prof.get("summoner_id")
                entries = await riot.league_entries_by_puuid(platform, puuid)
            else:
                entries = None
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
            title=ti(interaction, "games.lol.rank.title", name=target.display_name),
            description=ti(interaction, "games.lol.rank.subtitle",
                           riot_id=f"{prof.get('game_name')}#{prof.get('tag_line')}",
                           region=riot.PLATFORM_LABEL.get(platform, platform.upper())),
            color=color,
        )
        rank_file = await _attach_emblem(embed, tier)
        if solo:
            embed.add_field(
                name="Solo/Duo",
                value=ti(interaction, "games.lol.rank_line",
                         rank=riot.rank_label(tier, rank), lp=lp,
                         wins=wins, losses=losses, wr=_wl_ratio(wins, losses)),
                inline=False,
            )
        else:
            embed.add_field(name="Solo/Duo",
                            value=ti(interaction, "games.lol.unranked"), inline=False)

        # Auto rank role
        applied = None
        if isinstance(target, discord.Member):
            applied = await _apply_rank_role(target, tier)
        if applied:
            embed.set_footer(text=ti(interaction, "games.lol.rank.role_footer", role=applied))

        if rank_file:
            await interaction.followup.send(embed=embed, file=rank_file)
        else:
            await interaction.followup.send(embed=embed)

    # ---------- /lol rankrole ----------
    @lol_group.command(name="rankrole",
                       description="Admin: enable/disable automatic LoL rank role assignment")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="Enable",  value="on"),
        app_commands.Choice(name="Disable", value="off"),
    ])
    async def lol_rankrole(interaction: discord.Interaction,
                           action: app_commands.Choice[str]):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed(ti(interaction, "games.lol.err.dm_not_supported_title"),
                                 ti(interaction, "games.lol.err.dm_not_supported_desc")),
                ephemeral=True,
            )
            return
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                embed=_err_embed(ti(interaction, "games.lol.err.permission_denied_title"),
                    ti(interaction, "games.lol.err.permission_denied_desc")),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)

        # Initialise an empty role_map when not set yet
        cur = lol_rank_config_get(interaction.guild.id)
        role_map_arg = None
        if not cur.get("role_map"):
            role_map_arg = _build_default_role_map()

        lol_rank_config_upsert(
            interaction.guild.id,
            enabled=(1 if action.value == "on" else 0),
            role_map=role_map_arg,
        )
        state_key = "games.lol.rankrole.state_on" if action.value == "on" else "games.lol.rankrole.state_off"
        await interaction.followup.send(
            embed=_info_embed(
                ti(interaction, "games.lol.rankrole.title"),
                ti(interaction, "games.lol.rankrole.desc",
                   state=ti(interaction, state_key)),
                color=0x2ECC71 if action.value == "on" else 0xE67E22,
            ),
            ephemeral=True,
        )

    # ---------- /lol history ----------
    @lol_group.command(name="history", description="Your last N Solo/Duo ranked games (champion, KDA, W/L)")
    @app_commands.describe(
        n="Number of games (1-10, default 5)",
        member="Discord member (optional)",
    )
    async def lol_history(interaction: discord.Interaction,
                           n: Optional[app_commands.Range[int, 1, 10]] = 5,
                           member: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = member or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.no_account_title"),
                    ti(interaction, "games.lol.err.no_account_link_first_desc",
                       name=target.display_name)),
                ephemeral=True,
            )
            return

        platform = prof.get("platform") or "euw1"
        puuid = prof["puuid"]
        ids = await riot.match_ids_by_puuid(platform, puuid, count=int(n or 5), queue=420)
        if not ids:
            await interaction.followup.send(
                embed=_info_embed(ti(interaction, "games.lol.history.no_match_title"),
                    ti(interaction, "games.lol.history.no_match_desc",
                       name=target.display_name),
                    color=0x95A5A6),
            )
            return

        lines = []
        wins = 0
        for mid in ids:
            m = await riot.match_details(platform, mid)
            if not m:
                continue
            info = (m.get("info") or {})
            duration = int(info.get("gameDuration", 0))
            participants = info.get("participants") or []
            me = next((p for p in participants if p.get("puuid") == puuid), None)
            if not me:
                continue
            won = bool(me.get("win"))
            if won:
                wins += 1
            cid = me.get("championId", 0)
            cname = await riot.champion_name(cid)
            k = me.get("kills", 0); d = me.get("deaths", 0); a = me.get("assists", 0)
            kda = (k + a) / max(1, d)
            cs = me.get("totalMinionsKilled", 0) + me.get("neutralMinionsKilled", 0)
            mins = duration // 60
            secs = duration % 60
            tag = "🟢" if won else "🔴"
            lines.append(ti(interaction, "games.lol.history.line", tag=tag,
                            champion=cname, k=k, d=d, a=a, kda=f"{kda:.2f}",
                            cs=cs, time=f"{mins:02d}:{secs:02d}"))

        if not lines:
            await interaction.followup.send(
                embed=_info_embed(ti(interaction, "games.lol.history.no_data_title"),
                    ti(interaction, "games.lol.history.no_data_desc"),
                    color=0xE67E22),
            )
            return

        wr = (wins / len(lines)) * 100
        embed = discord.Embed(
            title=ti(interaction, "games.lol.history.title", name=target.display_name),
            description=ti(interaction, "games.lol.history.summary",
                           riot_id=f"{prof.get('game_name')}#{prof.get('tag_line')}",
                           region=riot.PLATFORM_LABEL.get(platform, platform.upper()),
                           wins=wins, losses=len(lines) - wins, wr=f"{wr:.1f}",
                           lines="\n".join(lines)),
            color=0x2ECC71 if wr >= 55 else (0xE67E22 if wr < 45 else 0x3498DB),
        )
        embed.set_footer(text=ti(interaction, "games.lol.history.footer"))
        await interaction.followup.send(embed=embed)

    # ---------- /lol live ----------
    @lol_group.command(name="live", description="Show a member's ongoing game (if they are in one)")
    @app_commands.describe(member="Discord member (optional)")
    async def lol_live(interaction: discord.Interaction,
                       member: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = member or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.no_account_title"),
                    ti(interaction, "games.lol.err.no_account_simple_desc",
                       name=target.display_name)),
                ephemeral=True,
            )
            return

        platform = prof.get("platform") or "euw1"
        puuid = prof["puuid"]
        game = await riot.active_game_by_puuid(platform, puuid)
        if not game:
            await interaction.followup.send(
                embed=_info_embed(ti(interaction, "games.lol.live.not_in_game_title"),
                    ti(interaction, "games.lol.live.not_in_game_desc",
                       name=target.display_name),
                    color=0x95A5A6),
            )
            return

        queue_id = game.get("gameQueueConfigId")
        q_label = riot.queue_label(queue_id)
        length = int(game.get("gameLength", 0))
        mins, secs = length // 60, length % 60
        map_id = game.get("mapId")

        participants = game.get("participants") or []
        me = next((p for p in participants if p.get("puuid") == puuid), None)
        my_team = (me or {}).get("teamId", 100)

        blue, red = [], []
        for p in participants:
            cname = await riot.champion_name(p.get("championId", 0))
            line = ti(interaction, "games.lol.live.player_line",
                      champion=cname, riot_id=p.get("riotId", "?"))
            if p.get("teamId") == 100:
                blue.append(line)
            else:
                red.append(line)

        you_label = ti(interaction, "games.lol.live.you")
        embed = discord.Embed(
            title=ti(interaction, "games.lol.live.title", name=target.display_name),
            description=ti(interaction, "games.lol.live.subtitle",
                           riot_id=f"{prof.get('game_name')}#{prof.get('tag_line')}",
                           region=riot.PLATFORM_LABEL.get(platform, platform.upper()),
                           queue=q_label, map_id=map_id,
                           time=f"{mins:02d}:{secs:02d}"),
            color=0xE74C3C,
        )
        if blue:
            embed.add_field(name=ti(interaction, "games.lol.live.blue_team",
                                    you=you_label if my_team == 100 else "").strip(),
                            value="\n".join(blue), inline=True)
        if red:
            embed.add_field(name=ti(interaction, "games.lol.live.red_team",
                                    you=you_label if my_team == 200 else "").strip(),
                            value="\n".join(red), inline=True)
        await interaction.followup.send(embed=embed)

    # ---------- /lol mastery ----------
    @lol_group.command(name="mastery", description="Top masteries, or the details of one champion")
    @app_commands.describe(
        champion="Champion name (optional, top 10 otherwise)",
        member="Discord member (optional)",
    )
    @app_commands.autocomplete(champion=_champion_autocomplete)
    async def lol_mastery(interaction: discord.Interaction,
                          champion: Optional[str] = None,
                          member: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = member or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.no_account_title"),
                    ti(interaction, "games.lol.err.no_account_simple_desc",
                       name=target.display_name)),
                ephemeral=True,
            )
            return

        platform = prof.get("platform") or "euw1"
        puuid = prof["puuid"]

        if champion:
            # Look up the champion id by name (case-insensitive)
            await riot._dd_refresh()  # noqa: ensure cache loaded
            champs = riot._DD_CACHE["champions"]
            target_id = None
            wanted = champion.strip().lower()
            for cid, info in champs.items():
                if info["name"].lower() == wanted or info["slug"].lower() == wanted:
                    target_id = cid
                    break
            if not target_id:
                await interaction.followup.send(
                    embed=_err_embed(ti(interaction, "games.lol.err.champion_not_found_title"),
                        ti(interaction, "games.lol.err.champion_not_found_desc",
                           champion=champion)),
                    ephemeral=True,
                )
                return
            m = await riot.mastery_by_champion(platform, puuid, target_id)
            if not m:
                await interaction.followup.send(
                    embed=_info_embed(ti(interaction, "games.lol.mastery.none_title"),
                        ti(interaction, "games.lol.mastery.none_champion_desc",
                           name=target.display_name,
                           champion=champs[target_id]["name"]),
                        color=0x95A5A6),
                )
                return
            embed = discord.Embed(
                title=ti(interaction, "games.lol.mastery.champion_title",
                         champion=champs[target_id]["name"]),
                description=ti(interaction, "games.lol.mastery.champion_desc",
                               name=target.display_name,
                               riot_id=f"{prof.get('game_name')}#{prof.get('tag_line')}",
                               level=m.get("championLevel", 0),
                               points=f"{int(m.get('championPoints', 0)):,}".replace(",", " ")),
                color=0x9B59B6,
            )
            icon = await riot.champion_icon_url(target_id)
            if icon:
                embed.set_thumbnail(url=icon)
            await interaction.followup.send(embed=embed)
            return

        # Top 10 by default
        all_m = await riot.mastery_all(platform, puuid)
        if not all_m:
            await interaction.followup.send(
                embed=_info_embed(ti(interaction, "games.lol.mastery.none_title"),
                    ti(interaction, "games.lol.mastery.none_desc"),
                    color=0x95A5A6),
            )
            return
        top = all_m[:10]
        lines = []
        for m in top:
            cid = m.get("championId", 0)
            cname = await riot.champion_name(cid)
            pts = int(m.get("championPoints", 0))
            lvl = m.get("championLevel", 0)
            lines.append(ti(interaction, "games.lol.mastery_line", champion=cname,
                            level=lvl, points=f"{pts:,}".replace(",", " ")))
        embed = discord.Embed(
            title=ti(interaction, "games.lol.mastery.top_title", name=target.display_name),
            description=ti(interaction, "games.lol.mastery.top_desc",
                           riot_id=f"{prof.get('game_name')}#{prof.get('tag_line')}",
                           lines="\n".join(lines)),
            color=0x9B59B6,
        )
        # Thumbnail = icon of the #1 champion
        if top:
            icon = await riot.champion_icon_url(top[0].get("championId", 0))
            if icon:
                embed.set_thumbnail(url=icon)
        await interaction.followup.send(embed=embed)

    # ---------- /lol queue ----------
    @lol_group.command(name="queue",
                       description="Create a temporary voice channel to stack ranked (5 slots)")
    async def lol_queue(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed(ti(interaction, "games.lol.err.dm_not_supported_title"),
                                 ti(interaction, "games.lol.err.dm_not_supported_desc")),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        from database import cs_queue_lobby_add
        guild = interaction.guild
        category = interaction.channel.category if interaction.channel and hasattr(interaction.channel, "category") else None
        name = ti(interaction, "games.lol.queue.channel_name",
                  name=interaction.user.display_name)
        try:
            vc = await guild.create_voice_channel(
                name=name[:100],
                user_limit=5,
                category=category,
                reason=f"LoL queue created by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.missing_permission_title"),
                    ti(interaction, "games.lol.err.missing_permission_desc")),
                ephemeral=True,
            )
            return
        except Exception as e:
            print(f"[lol/queue] create err: {type(e).__name__}")
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.generic_title"),
                    ti(interaction, "games.lol.err.voice_create_failed")),
                ephemeral=True,
            )
            return

        # Reuse the cs_queue_lobbies table (same auto-cleanup mechanism)
        cs_queue_lobby_add(vc.id, guild.id, interaction.user.id)
        await interaction.followup.send(
            embed=_info_embed(
                ti(interaction, "games.lol.queue.created_title"),
                ti(interaction, "games.lol.queue.created_desc", channel=vc.mention),
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /lol skin ----------
    @lol_group.command(name="skin",
                       description="Splash art + RP price of a skin (or the skin list of a champion)")
    @app_commands.describe(
        champion="Champion name (e.g. Jinx, Lee Sin)",
        skin="Skin name (optional, lists every skin otherwise)",
    )
    @app_commands.autocomplete(champion=_champion_autocomplete, skin=_skin_autocomplete)
    async def lol_skin(interaction: discord.Interaction, champion: str,
                       skin: Optional[str] = None):
        await interaction.response.defer()
        await riot._dd_refresh()
        champs = riot._DD_CACHE["champions"]
        wanted = champion.strip().lower()
        target_id = None
        for cid, info in champs.items():
            if info["name"].lower() == wanted or info["slug"].lower() == wanted:
                target_id = cid
                break
        if not target_id:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.champion_not_found_title"),
                    ti(interaction, "games.lol.err.champion_not_found_skin",
                       champion=champion)),
                ephemeral=True,
            )
            return

        cname = champs[target_id]["name"]
        cslug = champs[target_id]["slug"]
        meraki = await riot.meraki_champion(cslug)
        all_skins = (meraki or {}).get("skins") or []

        # "list" mode: no skin requested
        if not skin:
            if not all_skins:
                await interaction.followup.send(
                    embed=_info_embed(ti(interaction, "games.lol.skin.no_data_title"),
                        ti(interaction, "games.lol.skin.no_data_desc", champion=cname),
                        color=0x95A5A6),
                )
                return
            lines = []
            for sk in all_skins[:40]:  # cap
                nm = sk.get("name") or "?"
                cost = sk.get("cost")
                cost_str = "Classic" if cost in (0, "0", None) and nm.lower() == cname.lower() else (
                    f"{cost} RP" if isinstance(cost, int) and cost > 0 else (str(cost) if cost else "—")
                )
                lines.append(ti(interaction, "games.lol.skin.list_line", name=nm, cost=cost_str))
            embed = discord.Embed(
                title=ti(interaction, "games.lol.skin.list_title", champion=cname),
                description="\n".join(lines),
                color=0x9B59B6,
            )
            icon = await riot.champion_icon_url(target_id)
            if icon:
                embed.set_thumbnail(url=icon)
            embed.set_footer(text=ti(interaction, "games.lol.skin.list_footer",
                                     count=len(all_skins), champion=cname))
            await interaction.followup.send(embed=embed)
            return

        # "detail" mode: one specific skin
        wanted_skin = skin.strip().lower()
        found = next((sk for sk in all_skins if (sk.get("name") or "").lower() == wanted_skin), None)
        if not found:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.skin.not_found_title"),
                    ti(interaction, "games.lol.skin.not_found_desc",
                       skin=skin, champion=cname)),
                ephemeral=True,
            )
            return

        nm = found.get("name") or "?"
        cost = found.get("cost")
        cost_str = "Classic" if cost in (0, "0", None) and nm.lower() == cname.lower() else (
            f"{cost} RP" if isinstance(cost, int) and cost > 0 else (str(cost) if cost else "—")
        )
        skin_num = found.get("id", 0) % 1000  # Meraki id = championId * 1000 + skinNum
        splash_url = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{cslug}_{skin_num}.jpg"

        embed = discord.Embed(
            title=f"🎨 {nm}",
            description=ti(interaction, "games.lol.skin.detail_desc",
                           champion=cname, cost=cost_str),
            color=0x9B59B6,
        )
        embed.set_image(url=splash_url)
        release = found.get("release") or {}
        if release.get("date"):
            embed.add_field(name=ti(interaction, "games.lol.skin.release"),
                            value=str(release["date"])[:10], inline=True)
        rarity = found.get("rarity")
        if rarity and rarity.lower() != "norarity":
            embed.add_field(name=ti(interaction, "games.lol.skin.rarity"),
                            value=rarity, inline=True)
        embed.set_footer(text=ti(interaction, "games.lol.skin.footer"))
        await interaction.followup.send(embed=embed)

    # ---------- /lol build ----------
    @lol_group.command(name="build",
                       description="Build (items, runes, spells) for a champion + role")
    @app_commands.describe(
        champion="Champion name",
        role="Position (top, jungle, mid, adc, support)",
    )
    @app_commands.choices(role=[
        app_commands.Choice(name="Top",     value="top"),
        app_commands.Choice(name="Jungle",  value="jungle"),
        app_commands.Choice(name="Mid",     value="mid"),
        app_commands.Choice(name="ADC",     value="adc"),
        app_commands.Choice(name="Support", value="support"),
    ])
    @app_commands.autocomplete(champion=_champion_autocomplete)
    async def lol_build(interaction: discord.Interaction, champion: str,
                        role: app_commands.Choice[str]):
        await interaction.response.defer()
        await riot._dd_refresh()
        champs = riot._DD_CACHE["champions"]
        wanted = champion.strip().lower()
        target_id = None
        for cid, info in champs.items():
            if info["name"].lower() == wanted or info["slug"].lower() == wanted:
                target_id = cid
                break
        if not target_id:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.err.champion_not_found_title"),
                    ti(interaction, "games.lol.err.champion_not_found_simple",
                       champion=champion)),
                ephemeral=True,
            )
            return

        cname = champs[target_id]["name"]
        cslug = champs[target_id]["slug"]
        slug_dash = cslug.lower().replace("'", "")
        opgg_url = f"https://www.op.gg/lol/champions/{slug_dash}/build/{role.value}"
        moba_url = f"https://mobalytics.gg/lol/champions/{slug_dash}/build?role={role.value}"
        ugg_url  = f"https://u.gg/lol/champions/{slug_dash}/build?role={role.value}"
        dpm_url  = f"https://dpm.lol/lol/champions/{slug_dash}/builds?role={role.value}"

        # Priority OP.GG -> Mobalytics -> Data Dragon (always available)
        builds = None
        source = None
        source_url = None

        # 1. OP.GG (often blocked on the VPS, but we try anyway)
        opgg = await riot.opgg_build(cslug, role.value)
        if opgg and opgg.get("data") and isinstance(opgg.get("data"), dict):
            # OP.GG NEXT_DATA pageProps structure varies, skip when not
            # directly usable
            pass

        # 2. Mobalytics
        if not builds:
            mb = await riot.mobalytics_builds_all(cslug, role.value)
            if mb:
                builds = mb
                source = "Mobalytics"
                source_url = mb[0].get("source_url")

        # 3. Data Dragon recommended (always-available fallback)
        if not builds:
            dd = await riot.ddragon_recommended(cslug)
            if dd:
                builds = dd
                source = "Data Dragon (Riot)"
                source_url = builds[0].get("source_url")

        if not builds:
            # No source available: fall back to links
            embed = discord.Embed(
                title=ti(interaction, "games.lol.build.unavailable_title",
                         champion=cname, role=role.name),
                description=ti(interaction, "games.lol.build.unavailable_desc",
                               opgg=opgg_url, ugg=ugg_url, dpm=dpm_url, moba=moba_url),
                color=0xF1C40F,
            )
            icon_url = await riot.champion_icon_url(target_id)
            if icon_url:
                embed.set_thumbnail(url=icon_url)
            await interaction.followup.send(embed=embed)
            return

        # One or more builds available: show a selector when there are several
        loc = locale_of(interaction)
        view = LolBuildView(
            interaction.user.id, builds, cname, cslug, target_id,
            role_label=role.name, source=source, source_url=source_url or builds[0]["source_url"],
            locale=loc,
        )
        embed, file = await _render_build_embed(builds[0], cname, target_id,
                                                role.name, source,
                                                builds[0].get("source_url") or source_url, loc)
        if file:
            await interaction.followup.send(embed=embed, view=view, file=file)
        else:
            await interaction.followup.send(embed=embed, view=view)

    # ---------- /lol scout (sub-group, owner-only) ----------
    scout_group = app_commands.Group(
        name="scout",
        description="[Owner] Shareable Clash scouting sessions",
        parent=lol_group,
    )

    def _is_owner(interaction):
        owner_id = (os.getenv("DISCORD_OWNER_ID") or "").strip()
        return bool(owner_id) and str(interaction.user.id) == owner_id

    @scout_group.command(name="create",
                          description="Create a new scouting session (generates a shareable link)")
    @app_commands.describe(region="Region of the players (EUW by default)")
    @app_commands.choices(region=_REGION_CHOICES)
    async def scout_create(interaction: discord.Interaction,
                           region: Optional[app_commands.Choice[str]] = None):
        if not _is_owner(interaction):
            await interaction.response.send_message(
                embed=_err_embed(ti(interaction, "games.lol.err.owner_only_title"),
                    ti(interaction, "games.lol.err.owner_only_desc")),
                ephemeral=True,
            )
            return
        platform = (region.value if region else "euw1").lower()
        modal = ClashScoutModal(platform=platform, owner_id=interaction.user.id,
                                locale=locale_of(interaction))
        await interaction.response.send_modal(modal)

    @scout_group.command(name="stop",
                          description="Stop an active session (by slug)")
    @app_commands.describe(slug="Slug of the session to stop")
    async def scout_stop(interaction: discord.Interaction, slug: str):
        if not _is_owner(interaction):
            await interaction.response.send_message(
                embed=_err_embed(ti(interaction, "games.lol.err.owner_only_title"),
                                 ti(interaction, "games.lol.err.owner_only_short")),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        n = lol_scout_session_stop(slug.strip(), owner_id=interaction.user.id)
        if n:
            await interaction.followup.send(
                embed=_info_embed(ti(interaction, "games.lol.scout.stopped_title"),
                    ti(interaction, "games.lol.scout.stopped_desc", slug=slug),
                    color=0xE67E22),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=_err_embed(ti(interaction, "games.lol.scout.not_found_title"),
                    ti(interaction, "games.lol.scout.not_found_desc")),
                ephemeral=True,
            )

    @scout_group.command(name="list",
                          description="List your recent scout sessions")
    async def scout_list(interaction: discord.Interaction):
        if not _is_owner(interaction):
            await interaction.response.send_message(
                embed=_err_embed(ti(interaction, "games.lol.err.owner_only_title"),
                                 ti(interaction, "games.lol.err.owner_only_short")),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        sessions = lol_scout_sessions_list(owner_id=interaction.user.id, limit=15)
        if not sessions:
            await interaction.followup.send(
                embed=_info_embed(ti(interaction, "games.lol.scout.none_title"),
                    ti(interaction, "games.lol.scout.none_desc"),
                    color=0x95A5A6),
                ephemeral=True,
            )
            return
        base_url = (os.getenv("DASHBOARD_BASE_URL")
                    or "https://dashboard.tookbot.click").rstrip("/")
        lines = []
        for s in sessions[:10]:
            ts = int(__import__('datetime').datetime.fromisoformat(s['created_at']).timestamp())
            if s["status"] == "active":
                lines.append(ti(interaction, "games.lol.scout.list_active",
                                slug=s["slug"], platform=s["platform"].upper(), ts=ts,
                                url=f"{base_url}/scout/{s['slug']}"))
            else:
                lines.append(ti(interaction, "games.lol.scout.list_stopped",
                                slug=s["slug"], platform=s["platform"].upper(), ts=ts))
        embed = _info_embed(ti(interaction, "games.lol.scout.list_title"),
                            "\n".join(lines), color=0x3498DB)
        await interaction.followup.send(embed=embed, ephemeral=True)

    bot.tree.add_command(lol_group)


class ClashScoutModal(discord.ui.Modal, title="🔍 Clash scout: enter 5 Riot IDs"):
    """Modal with 5 inputs (Top/Jungle/Mid/ADC/Support). Creates a scout
    session in the DB + returns a shareable dashboard link."""

    def __init__(self, platform: str = "euw1", owner_id: int = 0, locale: str = "en"):
        super().__init__(timeout=600, title=t("games.lol.scout.modal_title", locale))
        self.platform = platform
        self.owner_id = owner_id
        self.locale = locale
        self.top = discord.ui.TextInput(
            label=t("games.lol.scout.modal_top", locale),
            placeholder="e.g. Faker#KR1",
            required=True, max_length=30,
        )
        self.jungle = discord.ui.TextInput(
            label=t("games.lol.scout.modal_jungle", locale),
            placeholder="e.g. Canyon#KR1",
            required=True, max_length=30,
        )
        self.mid = discord.ui.TextInput(
            label=t("games.lol.scout.modal_mid", locale),
            placeholder="e.g. Showmaker#KR1",
            required=True, max_length=30,
        )
        self.adc = discord.ui.TextInput(
            label=t("games.lol.scout.modal_adc", locale),
            placeholder="e.g. Ruler#KR1",
            required=True, max_length=30,
        )
        self.support = discord.ui.TextInput(
            label=t("games.lol.scout.modal_support", locale),
            placeholder="e.g. Keria#KR1",
            required=True, max_length=30,
        )
        self.add_item(self.top)
        self.add_item(self.jungle)
        self.add_item(self.mid)
        self.add_item(self.adc)
        self.add_item(self.support)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        entries = [
            ("TOP",     "🛡️", self.top.value),
            ("JUNGLE",  "🌲", self.jungle.value),
            ("MID",     "⚡", self.mid.value),
            ("ADC",     "🏹", self.adc.value),
            ("SUPPORT", "🛡️", self.support.value),
        ]

        # Scout the 5 players in parallel (raw data, not a string)
        scouts = await asyncio.gather(
            *[_scout_player_data(self.platform, raw_id) for _, _, raw_id in entries],
            return_exceptions=True,
        )

        scout_data = []
        for (role, emoji, raw_id), result in zip(entries, scouts):
            if isinstance(result, Exception):
                scout_data.append({
                    "role": f"{emoji} {role}",
                    "riot_id": raw_id,
                    "error": ti(interaction, "games.lol.scout.lookup_error",
                                error=type(result).__name__),
                })
                continue
            entry = dict(result)
            entry["role"] = f"{emoji} {role}"
            entry["riot_id"] = raw_id
            scout_data.append(entry)

        # Generate a slug + save the session with the structure:
        #   scout_data = {"enemies": [...5...], "allies": [5 empty slots]}
        from web_app.routes.lol_scout import generate_scout_slug
        slug = generate_scout_slug()
        riot_ids_dict = {role: raw_id for role, _, raw_id in entries}
        empty_allies = [
            {"role": f"{emoji} {role}", "riot_id": "", "side": "ally"}
            for role, emoji, _ in entries
        ]
        # Mark the enemies with side="enemy" for the template rendering
        for entry in scout_data:
            entry["side"] = "enemy"
        scout_data_v2 = {"enemies": scout_data, "allies": empty_allies}
        lol_scout_session_create(
            slug=slug,
            owner_id=self.owner_id,
            platform=self.platform,
            riot_ids=riot_ids_dict,
            scout_data=scout_data_v2,
        )
        base_url = (os.getenv("DASHBOARD_BASE_URL")
                    or "https://dashboard.tookbot.click").rstrip("/")
        link = f"{base_url}/scout/{slug}"

        embed = discord.Embed(
            title=ti(interaction, "games.lol.scout.created_title"),
            description=ti(interaction, "games.lol.scout.created_desc",
                           region=riot.PLATFORM_LABEL.get(self.platform, self.platform.upper()),
                           slug=slug, url=link),
            color=0x2ECC71,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def _recent_champs_stats(platform: str, puuid: str, count: int = 20):
    """Fetch the last N Solo/Duo ranked games + aggregate them per champion.
    Returns (recent_picks, top_wr):
    - recent_picks: list of (champion_id, games), sorted by games desc
    - top_wr: list of (champion_id, wins, total, wr) with >= 3 games, sorted by WR desc.
    """
    try:
        ids = await riot.match_ids_by_puuid(platform, puuid, count=count, queue=420)
    except Exception:
        ids = None
    if not ids:
        return [], []
    # In parallel
    try:
        matches = await asyncio.gather(
            *[riot.match_details(platform, mid) for mid in ids],
            return_exceptions=True,
        )
    except Exception:
        matches = []
    stats: dict = {}
    for m in matches:
        if not isinstance(m, dict):
            continue
        info = m.get("info") or {}
        for p in (info.get("participants") or []):
            if p.get("puuid") != puuid:
                continue
            cid = p.get("championId")
            if not cid:
                continue
            d = stats.setdefault(int(cid), {"wins": 0, "total": 0})
            d["total"] += 1
            if p.get("win"):
                d["wins"] += 1
            break
    recent = sorted(stats.items(), key=lambda kv: -kv[1]["total"])
    recent_list = [(cid, s["total"]) for cid, s in recent[:5]]
    top_wr_list = sorted(
        [(cid, s["wins"], s["total"], s["wins"] / s["total"] * 100)
         for cid, s in stats.items() if s["total"] >= 3],
        key=lambda x: (-x[3], -x[2]),
    )[:3]
    return recent_list, top_wr_list


async def _scout_player_data(platform: str, raw_riot_id: str) -> dict:
    """Structured version of _scout_player. Returns a rich dict for the HTML
    rendering: champion icons, profile icon, rank tier, etc."""
    import unicodedata as _u
    raw_riot_id = _u.normalize("NFC", raw_riot_id or "")
    raw_riot_id = "".join(c for c in raw_riot_id if _u.category(c) != "Cf")
    cleaned = raw_riot_id.replace(" ", " ").strip()
    if "#" not in cleaned:
        return {"error": t("games.lol.scout.bad_riot_id")}
    parts = cleaned.rsplit("#", 1)
    game_name = parts[0].strip()
    tag_line = parts[1].strip()
    if not game_name or not tag_line:
        return {"error": t("games.lol.scout.bad_riot_id_parts",
                           riot_id=f"{game_name}#{tag_line}")}
    account = await riot.account_by_riot_id(platform, game_name, tag_line)
    if not account or not account.get("puuid"):
        return {"error": t("games.lol.scout.not_found",
                           riot_id=f"{game_name}#{tag_line}",
                           region=riot.PLATFORM_LABEL.get(platform, platform))}
    puuid = account["puuid"]
    summ = await riot.summoner_by_puuid(platform, puuid)
    level = (summ or {}).get("summonerLevel")
    profile_icon_id = (summ or {}).get("profileIconId")

    # Rank Solo/Duo
    try:
        entries = await riot.league_entries_by_puuid(platform, puuid)
    except Exception:
        entries = None
    entries = entries or []
    solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
    tier = (solo or {}).get("tier", "UNRANKED")
    rank = (solo or {}).get("rank", "")
    lp = (solo or {}).get("leaguePoints", 0)
    wins = (solo or {}).get("wins", 0)
    losses = (solo or {}).get("losses", 0)
    wr = (wins / (wins + losses) * 100) if (wins + losses) else 0
    if solo:
        rank_line = t("games.lol.scout.rank_line_html",
                      rank=riot.rank_label(tier, rank), lp=lp,
                      wins=wins, losses=losses, wr=f"{wr:.1f}")
    else:
        rank_line = t("games.lol.scout.unranked_html")

    # Resolve champion slug helper
    await riot._dd_refresh()
    champs_map = riot._DD_CACHE.get("champions", {})

    def _slug(cid):
        info = champs_map.get(int(cid))
        return info["slug"] if info else None

    # Mastery
    try:
        masteries = await riot.mastery_top(platform, puuid, count=3)
    except Exception:
        masteries = None
    mastery_list = []
    for m in (masteries or [])[:3]:
        cid = int(m.get("championId", 0))
        cname = await riot.champion_name(cid)
        pts = int(m.get("championPoints", 0))
        mastery_list.append({
            "id": cid,
            "slug": _slug(cid),
            "champ": cname,
            "pts": f"{pts // 1000}k",
            "pts_int": pts,
            "level": m.get("championLevel", 0),
        })

    # Recent picks + top WR
    recent, top_wr = await _recent_champs_stats(platform, puuid, count=20)
    recent_list = []
    for cid, games in recent[:5]:
        cname = await riot.champion_name(cid)
        recent_list.append({
            "id": int(cid),
            "slug": _slug(cid),
            "champ": cname,
            "games": games,
        })
    top_wr_list = []
    for cid, w, t, w_pct in top_wr[:3]:
        cname = await riot.champion_name(cid)
        top_wr_list.append({
            "id": int(cid),
            "slug": _slug(cid),
            "champ": cname,
            "wins": w,
            "total": t,
            "wr": w_pct,
        })

    return {
        "game_name":       account.get("gameName") or game_name,
        "tag_line":        account.get("tagLine") or tag_line,
        "level":           level,
        "profile_icon_id": profile_icon_id,
        "tier":            tier,
        "rank":            rank,
        "lp":              lp,
        "wins":            wins,
        "losses":          losses,
        "wr":              wr,
        "rank_line":       rank_line,
        "mastery":         mastery_list,
        "recent":          recent_list,
        "top_wr":          top_wr_list,
    }


async def _scout_player(platform: str, raw_riot_id: str) -> str:
    """Fetch the scout profile of one player from a Riot ID. Returns a string
    formatted for embed.add_field."""
    import unicodedata as _u
    # NFC normalisation + strip of Unicode formatting chars (Cf):
    # a Discord Modal wraps the text in U+2066 (LRI) and U+2069 (PDI),
    # which are invisible but break the Riot API.
    raw_riot_id = _u.normalize("NFC", raw_riot_id or "")
    raw_riot_id = "".join(c for c in raw_riot_id if _u.category(c) != "Cf")
    # Tolerant parsing: raw split on the last '#'
    cleaned = (raw_riot_id or "").replace(" ", " ").strip()  # nbsp -> space
    if "#" not in cleaned:
        print(f"[lol/scout] no # in input raw={raw_riot_id!r} cleaned={cleaned!r}")
        return t("games.lol.scout.bad_riot_id_msg")
    parts = cleaned.rsplit("#", 1)
    game_name = parts[0].strip()
    tag_line = parts[1].strip()
    if not game_name or not tag_line or len(game_name) > 32 or len(tag_line) > 8:
        print(f"[lol/scout] bad parts raw={raw_riot_id!r} name={game_name!r} tag={tag_line!r}")
        return t("games.lol.scout.bad_riot_id_limits", riot_id=f"{game_name}#{tag_line}")
    print(f"[lol/scout] lookup name={game_name!r} tag={tag_line!r} platform={platform}")

    account = await riot.account_by_riot_id(platform, game_name, tag_line)
    if not account or not account.get("puuid"):
        return t("games.lol.scout.not_found_msg", riot_id=f"{game_name}#{tag_line}",
                 region=riot.PLATFORM_LABEL.get(platform, platform))
    puuid = account["puuid"]

    summ = await riot.summoner_by_puuid(platform, puuid)
    level = (summ or {}).get("summonerLevel")

    # Rank Solo/Duo
    try:
        entries = await riot.league_entries_by_puuid(platform, puuid)
    except Exception:
        entries = None
    entries = entries or []
    solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
    if solo:
        lp = solo.get("leaguePoints", 0)
        wins = solo.get("wins", 0)
        losses = solo.get("losses", 0)
        wr = (wins / (wins + losses) * 100) if (wins + losses) else 0
        rank_line = t("games.lol.scout.rank_line",
                      rank=riot.rank_label(solo.get("tier", "UNRANKED"), solo.get("rank", "")),
                      lp=lp, wins=wins, losses=losses, wr=f"{wr:.1f}")
    else:
        rank_line = t("games.lol.scout.unranked")

    # Top 3 masteries (all-time, every champion)
    try:
        masteries = await riot.mastery_top(platform, puuid, count=3)
    except Exception:
        masteries = None
    mastery_line = t("games.lol.scout.no_mastery")
    if masteries:
        names = []
        for m in masteries[:3]:
            cid = m.get("championId", 0)
            cname = await riot.champion_name(cid)
            pts = int(m.get("championPoints", 0))
            names.append(f"{cname} ({pts // 1000}k)")
        mastery_line = " · ".join(names)

    # Match history: recent picks + top WR (last 20 Solo/Duo ranked games)
    recent, top_wr = await _recent_champs_stats(platform, puuid, count=20)
    if recent:
        picks_parts = []
        for cid, games in recent[:4]:
            cname = await riot.champion_name(cid)
            picks_parts.append(f"{cname}×{games}")
        recent_line = " · ".join(picks_parts)
    else:
        recent_line = t("games.lol.scout.no_recent_games")

    if top_wr:
        ban_parts = []
        for cid, wins, total, wr in top_wr[:3]:
            cname = await riot.champion_name(cid)
            ban_parts.append(t("games.lol.scout.ban_entry", champion=cname,
                               wr=f"{wr:.0f}", games=total))
        ban_line = " · ".join(ban_parts)
    else:
        ban_line = t("games.lol.scout.not_enough_games")

    return t("games.lol.scout.summary", level=level or "?", rank_line=rank_line,
             recent=recent_line, bans=ban_line, mastery=mastery_line)
