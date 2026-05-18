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


async def _attach_emblem(embed: discord.Embed, tier: str):
    """Si emblem dispo en local, attache le fichier + set_thumbnail attachment.
    Sinon fallback URL distante."""
    if tier == "UNRANKED":
        embed.set_thumbnail(url=riot.tier_emblem_url(tier))
        return None
    path = await riot.tier_emblem_file_path(tier)
    if path:
        fname = f"emblem_{tier.lower()}.png"
        embed.set_thumbnail(url=f"attachment://{fname}")
        return discord.File(path, filename=fname)
    # Fallback : URL distante (peut etre pixelisee par Discord)
    embed.set_thumbnail(url=riot.tier_emblem_url(tier))
    return None


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
    file = await _attach_emblem(embed, primary_tier)
    return embed, file


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


_VIEW_ITEMS = "items"
_VIEW_RUNES = "runes"
_VIEW_SKILLS = "skills"

_SKILL_LETTER = {1: "Q", 2: "W", 3: "E", 4: "R"}


async def _render_items_view(build: dict, cname: str, champion_id: int,
                              role_label: str, source: str, source_url: str):
    """Vue items : embed avec items par phase + composite PNG (items + spells)."""
    embed = discord.Embed(
        title=f"🧱 Items — {build.get('name', 'Build')}",
        description=(f"**{cname}** {role_label} · Source : [{source}]({source_url})"
                     + (f"\n🏆 WR {build['wr']:.1f}% sur {build['matches']:,} parties".replace(",", " ")
                        if build.get("wr") is not None else "")),
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
            name=f"📦 {ptype}",
            value=" → ".join(f"`{n}`" for n in names) or "—",
            inline=False,
        )

    # Composite image : items + spells
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
    embed.set_footer(text=f"Items + sorts d'invocateur. {source}.")
    return embed, file


async def _render_runes_view(build: dict, cname: str, champion_id: int,
                              role_label: str, source: str, source_url: str):
    """Vue runes : embed avec page de runes complete (keystone + primary 3 +
    secondary 2 + stat shards 3)."""
    embed = discord.Embed(
        title=f"🌿 Runes — {build.get('name', 'Build')}",
        description=(f"**{cname}** {role_label} · Source : [{source}]({source_url})"
                     + (f"\n🏆 WR {build['wr']:.1f}% sur {build['matches']:,} parties".replace(",", " ")
                        if build.get("wr") is not None else "")),
        color=0xF1C40F,
    )
    perk_ids = build.get("perk_ids") or []
    primary_tree_id = build.get("primary_style")
    sub_tree_id     = build.get("sub_style")

    if not perk_ids:
        embed.add_field(name="—", value="Pas de données de runes.", inline=False)
        icon = await riot.champion_icon_url(champion_id)
        if icon:
            embed.set_thumbnail(url=icon)
        return embed, None

    # Layout typique : keystone + 3 primary + 2 secondary + 3 shards = 9 ids
    keystone     = perk_ids[0] if len(perk_ids) > 0 else None
    primary_3    = perk_ids[1:4]
    secondary_2  = perk_ids[4:6]
    shards       = perk_ids[6:9]

    tree_primary_name   = await riot.rune_name(primary_tree_id) if primary_tree_id else "?"
    tree_secondary_name = await riot.rune_name(sub_tree_id) if sub_tree_id else "?"

    primary_lines = []
    if keystone:
        primary_lines.append(f"⭐ **{await riot.rune_name(keystone)}** (Keystone)")
    for rid in primary_3:
        primary_lines.append(f"• {await riot.rune_name(rid)}")
    embed.add_field(
        name=f"🟦 {tree_primary_name} (principale)",
        value="\n".join(primary_lines) or "—",
        inline=True,
    )

    secondary_lines = []
    for rid in secondary_2:
        secondary_lines.append(f"• {await riot.rune_name(rid)}")
    embed.add_field(
        name=f"🟣 {tree_secondary_name} (secondaire)",
        value="\n".join(secondary_lines) or "—",
        inline=True,
    )

    shard_lines = []
    shard_slot_labels = ["Offense", "Flex", "Defense"]
    for i, rid in enumerate(shards):
        label = shard_slot_labels[i] if i < 3 else ""
        shard_lines.append(f"{label} : **{await riot.rune_name(rid)}**" if label
                            else f"**{await riot.rune_name(rid)}**")
    embed.add_field(
        name="💠 Stat Shards",
        value="\n".join(shard_lines) or "—",
        inline=False,
    )

    # Thumbnail = icon keystone
    if keystone:
        icon = await riot.rune_icon_url(keystone)
        if icon:
            embed.set_thumbnail(url=icon)
    embed.set_footer(text=f"Page de runes complète. {source}.")
    return embed, None


async def _render_skills_view(build: dict, cname: str, champion_id: int,
                              role_label: str, source: str, source_url: str):
    """Vue skills + summoners : ordre des skills + sorts d'invocateur."""
    embed = discord.Embed(
        title=f"⚔️ Skills + Sorts — {build.get('name', 'Build')}",
        description=(f"**{cname}** {role_label} · Source : [{source}]({source_url})"
                     + (f"\n🏆 WR {build['wr']:.1f}% sur {build['matches']:,} parties".replace(",", " ")
                        if build.get("wr") is not None else "")),
        color=0xF1C40F,
    )

    # Ordre des skills (1=Q, 2=W, 3=E, 4=R)
    so = build.get("skill_order") or []
    if so:
        # Tableau visuel 1..18
        line_letters = []
        line_levels  = []
        for lvl, s in enumerate(so[:18], 1):
            line_letters.append(_SKILL_LETTER.get(int(s), "?"))
            line_levels.append(f"{lvl:2d}")
        embed.add_field(
            name="📈 Ordre de skill (par niveau)",
            value=f"`Lvl  {' '.join(line_levels)}`\n`Skill { ' '.join(f' {l}' for l in line_letters)}`",
            inline=False,
        )

    # Priorite max
    smo = build.get("skill_max_order") or []
    if smo:
        prio = " > ".join(_SKILL_LETTER.get(int(s), "?") for s in smo)
        embed.add_field(name="🎯 Priorité d'augmentation", value=f"`{prio}`", inline=False)

    # Summoner spells
    spells = build.get("summoner_spells") or []
    if spells:
        names = [riot.SUMMONER_SPELL_NAMES.get(int(s), f"Spell #{s}") for s in spells[:2]]
        embed.add_field(name="✨ Sorts d'invocateur",
                        value=" + ".join(f"**{n}**" for n in names),
                        inline=False)

    icon = await riot.champion_icon_url(champion_id)
    if icon:
        embed.set_thumbnail(url=icon)
    embed.set_footer(text=f"Skill order + Sorts. {source}.")
    return embed, None


async def _render_build(build: dict, view_kind: str, cname: str, champion_id: int,
                        role_label: str, source: str, source_url: str):
    """Dispatch sur la vue choisie."""
    if view_kind == _VIEW_RUNES:
        return await _render_runes_view(build, cname, champion_id, role_label, source, source_url)
    if view_kind == _VIEW_SKILLS:
        return await _render_skills_view(build, cname, champion_id, role_label, source, source_url)
    return await _render_items_view(build, cname, champion_id, role_label, source, source_url)


# Backwards-compat (used elsewhere)
async def _render_build_embed(build, cname, champion_id, role_label, source, source_url):
    return await _render_items_view(build, cname, champion_id, role_label, source, source_url)


class LolBuildView(discord.ui.View):
    """View avec 2 rangs de boutons :
      - Row 0 : selection du build (1-5 boutons, types Mobalytics)
      - Row 1 : selection de la vue (Items / Runes / Skills+Sorts)
    Click = edit du meme message."""

    def __init__(self, author_id: int, builds: list[dict], cname: str,
                 cslug: str, champion_id: int, role_label: str,
                 source: str, source_url: str,
                 selected_build: int = 0, selected_view: str = _VIEW_ITEMS):
        super().__init__(timeout=300)
        self.author_id = author_id
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

    def _rebuild_buttons(self):
        self.clear_items()
        # Row 0 : builds (max 5)
        for idx, b in enumerate(self.builds[:5]):
            label = b.get("name") or f"Build {idx + 1}"
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
        # Row 1 : vues
        for kind, label, emoji in (
            (_VIEW_ITEMS,  "Items",   "🧱"),
            (_VIEW_RUNES,  "Runes",   "🌿"),
            (_VIEW_SKILLS, "Skills + Sorts", "⚔️"),
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
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "❌ Ce menu n'est pas pour toi. Lance `/lol build` toi-même.",
                    ephemeral=True,
                )
                return
            self.selected_build = idx
            await self._refresh(interaction)
        return cb

    def _make_view_cb(self, kind: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "❌ Ce menu n'est pas pour toi.",
                    ephemeral=True,
                )
                return
            self.selected_view = kind
            await self._refresh(interaction)
        return cb

    async def _refresh(self, interaction: discord.Interaction):
        build = self.builds[self.selected_build]
        embed, file = await _render_build(
            build, self.selected_view, self.cname, self.champion_id,
            self.role_label, self.source,
            build.get("source_url") or self.source_url,
        )
        self._rebuild_buttons()
        if file:
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        else:
            await interaction.response.edit_message(embed=embed, attachments=[], view=self)


# Autocomplete helpers (visibles a tous les sub-commands ci-dessous)
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


_SKIN_CACHE: dict = {}  # slug -> list of names (cache 6h cote Meraki deja)


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
        embed, file = await _build_stats_embed(target, prof)
        if file:
            await interaction.followup.send(embed=embed, file=file)
        else:
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
        rank_file = await _attach_emblem(embed, tier)
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

        if rank_file:
            await interaction.followup.send(embed=embed, file=rank_file)
        else:
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

    # ---------- /lol history ----------
    @lol_group.command(name="history", description="Tes N dernieres ranked Solo/Duo (champion, KDA, W/L)")
    @app_commands.describe(
        n="Nombre de parties (1-10, default 5)",
        membre="Membre Discord (optionnel)",
    )
    async def lol_history(interaction: discord.Interaction,
                           n: Optional[app_commands.Range[int, 1, 10]] = 5,
                           membre: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = membre or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed("Aucun compte lié",
                    f"**{target.display_name}** n'a pas lié de compte LoL.\n"
                    "Utilise `/lol link` d'abord."),
                ephemeral=True,
            )
            return

        platform = prof.get("platform") or "euw1"
        puuid = prof["puuid"]
        ids = await riot.match_ids_by_puuid(platform, puuid, count=int(n or 5), queue=420)
        if not ids:
            await interaction.followup.send(
                embed=_info_embed("📭 Aucun match",
                    f"Aucune partie Solo/Duo récente pour **{target.display_name}**.",
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
            lines.append(
                f"{tag} **{cname}** · `{k}/{d}/{a}` ({kda:.2f}) "
                f"· `{cs} CS` · `{mins:02d}:{secs:02d}`"
            )

        if not lines:
            await interaction.followup.send(
                embed=_info_embed("📭 Données indisponibles",
                    "Les matches existent mais le détail n'a pas pu être récupéré (rate-limit ou API down).",
                    color=0xE67E22),
            )
            return

        wr = (wins / len(lines)) * 100
        embed = discord.Embed(
            title=f"🎮 Historique Solo/Duo — {target.display_name}",
            description=(
                f"**{prof.get('game_name')}#{prof.get('tag_line')}** "
                f"({riot.PLATFORM_LABEL.get(platform, platform.upper())})\n"
                f"**{wins}V** / **{len(lines)-wins}D** · Winrate **{wr:.1f}%**\n\n"
                + "\n".join(lines)
            ),
            color=0x2ECC71 if wr >= 55 else (0xE67E22 if wr < 45 else 0x3498DB),
        )
        embed.set_footer(text="🟢 victoire · 🔴 défaite · KDA = (kills+assists)/deaths")
        await interaction.followup.send(embed=embed)

    # ---------- /lol live ----------
    @lol_group.command(name="live", description="Affiche la partie en cours d'un membre (si en jeu)")
    @app_commands.describe(membre="Membre Discord (optionnel)")
    async def lol_live(interaction: discord.Interaction,
                       membre: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = membre or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed("Aucun compte lié",
                    f"**{target.display_name}** n'a pas lié de compte LoL."),
                ephemeral=True,
            )
            return

        platform = prof.get("platform") or "euw1"
        puuid = prof["puuid"]
        game = await riot.active_game_by_puuid(platform, puuid)
        if not game:
            await interaction.followup.send(
                embed=_info_embed("💤 Pas en jeu",
                    f"**{target.display_name}** n'est pas en partie actuellement.",
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
            line = f"**{cname}** · `{p.get('riotId', '?')}`"
            if p.get("teamId") == 100:
                blue.append(line)
            else:
                red.append(line)

        embed = discord.Embed(
            title=f"🔴 En jeu — {target.display_name}",
            description=(
                f"**{prof.get('game_name')}#{prof.get('tag_line')}** "
                f"({riot.PLATFORM_LABEL.get(platform, platform.upper())})\n"
                f"**{q_label}** · Map `{map_id}` · Temps : `{mins:02d}:{secs:02d}`"
            ),
            color=0xE74C3C,
        )
        if blue:
            embed.add_field(name=f"🔵 Équipe Bleue {'(toi)' if my_team == 100 else ''}",
                            value="\n".join(blue), inline=True)
        if red:
            embed.add_field(name=f"🔴 Équipe Rouge {'(toi)' if my_team == 200 else ''}",
                            value="\n".join(red), inline=True)
        await interaction.followup.send(embed=embed)

    # ---------- /lol mastery ----------
    @lol_group.command(name="mastery", description="Top maitrises ou detail d'un champion specifique")
    @app_commands.describe(
        champion="Nom du champion (optionnel, sinon top 10)",
        membre="Membre Discord (optionnel)",
    )
    @app_commands.autocomplete(champion=_champion_autocomplete)
    async def lol_mastery(interaction: discord.Interaction,
                          champion: Optional[str] = None,
                          membre: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = membre or interaction.user
        prof = lol_profile_get(target.id)
        if not prof:
            await interaction.followup.send(
                embed=_err_embed("Aucun compte lié",
                    f"**{target.display_name}** n'a pas lié de compte LoL."),
                ephemeral=True,
            )
            return

        platform = prof.get("platform") or "euw1"
        puuid = prof["puuid"]

        if champion:
            # Cherche le champion id par nom (case-insensitive)
            await riot._dd_refresh()  # noqa : ensure cache loaded
            champs = riot._DD_CACHE["champions"]
            target_id = None
            wanted = champion.strip().lower()
            for cid, info in champs.items():
                if info["name"].lower() == wanted or info["slug"].lower() == wanted:
                    target_id = cid
                    break
            if not target_id:
                await interaction.followup.send(
                    embed=_err_embed("Champion introuvable",
                        f"Aucun champion nommé `{champion}` trouvé.\n"
                        "Vérifie l'orthographe (ex: `Lee Sin`, `Kai'Sa`, `Wukong`)."),
                    ephemeral=True,
                )
                return
            m = await riot.mastery_by_champion(platform, puuid, target_id)
            if not m:
                await interaction.followup.send(
                    embed=_info_embed("📭 Aucune maîtrise",
                        f"**{target.display_name}** n'a pas joué **{champs[target_id]['name']}** "
                        "(ou data Riot pas encore propagée).",
                        color=0x95A5A6),
                )
                return
            embed = discord.Embed(
                title=f"✨ Maîtrise — {champs[target_id]['name']}",
                description=(
                    f"**{target.display_name}** · {prof.get('game_name')}#{prof.get('tag_line')}\n\n"
                    f"Niveau : **{m.get('championLevel', 0)}**\n"
                    f"Points : **{int(m.get('championPoints', 0)):,}**".replace(",", " ")
                ),
                color=0x9B59B6,
            )
            icon = await riot.champion_icon_url(target_id)
            if icon:
                embed.set_thumbnail(url=icon)
            await interaction.followup.send(embed=embed)
            return

        # Top 10 par defaut
        all_m = await riot.mastery_all(platform, puuid)
        if not all_m:
            await interaction.followup.send(
                embed=_info_embed("📭 Aucune maîtrise",
                    "Pas de données de maîtrise disponibles.",
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
            lines.append(f"**{cname}** · Niv. `{lvl}` · `{pts:,}` pts".replace(",", " "))
        embed = discord.Embed(
            title=f"✨ Top 10 maîtrises — {target.display_name}",
            description=(
                f"**{prof.get('game_name')}#{prof.get('tag_line')}**\n\n"
                + "\n".join(lines)
            ),
            color=0x9B59B6,
        )
        # Thumbnail = icon du #1
        if top:
            icon = await riot.champion_icon_url(top[0].get("championId", 0))
            if icon:
                embed.set_thumbnail(url=icon)
        await interaction.followup.send(embed=embed)

    # ---------- /lol queue ----------
    @lol_group.command(name="queue",
                       description="Cree un voice channel temporaire pour stack ranked (5 slots)")
    async def lol_queue(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err_embed("Pas dispo en DM", "Utilise cette commande dans un serveur."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        from database import cs_queue_lobby_add
        guild = interaction.guild
        category = interaction.channel.category if interaction.channel and hasattr(interaction.channel, "category") else None
        name = f"⚔️ LoL Queue · {interaction.user.display_name}"
        try:
            vc = await guild.create_voice_channel(
                name=name[:100],
                user_limit=5,
                category=category,
                reason=f"LoL queue created by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=_err_embed("Permission manquante",
                    "Le bot doit avoir la permission **Gérer les salons** pour créer un voice channel."),
                ephemeral=True,
            )
            return
        except Exception as e:
            print(f"[lol/queue] create err: {type(e).__name__}")
            await interaction.followup.send(
                embed=_err_embed("Erreur",
                    "Impossible de créer le voice channel. Réessaie plus tard."),
                ephemeral=True,
            )
            return

        # Reuse de la table cs_queue_lobbies (meme mecanisme cleanup auto)
        cs_queue_lobby_add(vc.id, guild.id, interaction.user.id)
        await interaction.followup.send(
            embed=_info_embed(
                "⚔️ Queue LoL créée",
                f"Voice channel **{vc.mention}** créé (5 slots).\n"
                "Il sera supprimé automatiquement quand il sera vide.",
                color=0x2ECC71),
            ephemeral=True,
        )

    # ---------- /lol skin ----------
    @lol_group.command(name="skin",
                       description="Splash art + prix RP d'un skin (ou liste des skins d'un champion)")
    @app_commands.describe(
        champion="Nom du champion (ex: Jinx, Lee Sin)",
        skin="Nom du skin (optionnel, sinon liste tous les skins)",
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
                embed=_err_embed("Champion introuvable",
                    f"`{champion}` n'a pas été trouvé.\n"
                    "Exemples : `Jinx`, `Lee Sin`, `Kai'Sa`, `K'Sante`."),
                ephemeral=True,
            )
            return

        cname = champs[target_id]["name"]
        cslug = champs[target_id]["slug"]
        meraki = await riot.meraki_champion(cslug)
        all_skins = (meraki or {}).get("skins") or []

        # Mode "liste" : pas de skin demande
        if not skin:
            if not all_skins:
                await interaction.followup.send(
                    embed=_info_embed("📭 Pas de données skins",
                        f"Aucune donnée de skins disponible pour **{cname}**.",
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
                lines.append(f"• **{nm}** — `{cost_str}`")
            embed = discord.Embed(
                title=f"🎨 Skins — {cname}",
                description="\n".join(lines),
                color=0x9B59B6,
            )
            icon = await riot.champion_icon_url(target_id)
            if icon:
                embed.set_thumbnail(url=icon)
            embed.set_footer(text=f"Total : {len(all_skins)} skins. Utilise `/lol skin {cname} <nom>` pour le détail.")
            await interaction.followup.send(embed=embed)
            return

        # Mode "detail" : skin precis
        wanted_skin = skin.strip().lower()
        found = next((sk for sk in all_skins if (sk.get("name") or "").lower() == wanted_skin), None)
        if not found:
            await interaction.followup.send(
                embed=_err_embed("Skin introuvable",
                    f"Aucun skin nommé `{skin}` pour **{cname}**.\n"
                    f"Utilise `/lol skin {cname}` (sans 2e param) pour voir la liste."),
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
            description=f"Champion : **{cname}**\nPrix : **{cost_str}**",
            color=0x9B59B6,
        )
        embed.set_image(url=splash_url)
        release = found.get("release") or {}
        if release.get("date"):
            embed.add_field(name="Sortie", value=str(release["date"])[:10], inline=True)
        rarity = found.get("rarity")
        if rarity and rarity.lower() != "norarity":
            embed.add_field(name="Rareté", value=rarity, inline=True)
        embed.set_footer(text="Données : Data Dragon + Meraki Analytics")
        await interaction.followup.send(embed=embed)

    # ---------- /lol build ----------
    @lol_group.command(name="build",
                       description="Build (items, runes, sorts) d'un champion + role")
    @app_commands.describe(
        champion="Nom du champion",
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
                embed=_err_embed("Champion introuvable",
                    f"`{champion}` n'a pas été trouvé."),
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

        # Priorite OP.GG -> Mobalytics -> Data Dragon (toujours dispo)
        builds = None
        source = None
        source_url = None

        # 1. OP.GG (souvent bloque sur VPS, mais on tente)
        opgg = await riot.opgg_build(cslug, role.value)
        if opgg and opgg.get("data") and isinstance(opgg.get("data"), dict):
            # OP.GG NEXT_DATA pageProps structure variable, on skip si pas
            # exploitable directement
            pass

        # 2. Mobalytics
        if not builds:
            mb = await riot.mobalytics_builds_all(cslug, role.value)
            if mb:
                builds = mb
                source = "Mobalytics"
                source_url = mb[0].get("source_url")

        # 3. Data Dragon recommended (fallback toujours dispo)
        if not builds:
            dd = await riot.ddragon_recommended(cslug)
            if dd:
                builds = dd
                source = "Data Dragon (Riot)"
                source_url = builds[0].get("source_url")

        if not builds:
            # Aucune source : fallback liens
            embed = discord.Embed(
                title=f"📊 Build — {cname} {role.name}",
                description=(
                    f"⚠️ Sources de build temporairement indisponibles.\n"
                    f"Consulte directement :\n"
                    f"• [OP.GG]({opgg_url})\n"
                    f"• [U.GG]({ugg_url})\n"
                    f"• [DPM]({dpm_url})\n"
                    f"• [Mobalytics]({moba_url})"
                ),
                color=0xF1C40F,
            )
            icon_url = await riot.champion_icon_url(target_id)
            if icon_url:
                embed.set_thumbnail(url=icon_url)
            await interaction.followup.send(embed=embed)
            return

        # 1 ou plusieurs builds dispo : si plusieurs, on affiche un selector
        view = LolBuildView(
            interaction.user.id, builds, cname, cslug, target_id,
            role_label=role.name, source=source, source_url=source_url or builds[0]["source_url"],
        )
        embed, file = await _render_build_embed(builds[0], cname, target_id,
                                                role.name, source, builds[0].get("source_url") or source_url)
        if file:
            await interaction.followup.send(embed=embed, view=view, file=file)
        else:
            await interaction.followup.send(embed=embed, view=view)

    bot.tree.add_command(lol_group)
