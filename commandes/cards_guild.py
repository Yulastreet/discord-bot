"""/guild commands: player guilds (cross-server clubs).
Config driven by the bot owner (get_guild_config). XP hooks called from /roll, boss, etc.
"""
from __future__ import annotations

import discord
from discord import app_commands

from services.i18n import t, ti, locale_of
from services.ui_v2 import Panel

from database import (
    get_guild_config, guild_create, guild_get, guild_get_by_name, guild_of_user,
    guild_member_role, guild_members, guild_member_count, guild_add_member,
    guild_remove_member, guild_set_role, guild_set_owner, guild_delete,
    guild_left_at, guild_invite_add, guild_invite_has, guild_add_xp, guild_top,
    guild_bank_add, guild_member_action_xp, guild_rewards_for_level,
    currency_get, currency_add,
    compute_player_combat_stats, combat_power, user_card_count,
    guild_bank_spend, guild_member_ids, roll_give_user,
    guild_set_color, guild_set_emblem, profile_color_hex, PROFILE_COLORS,
    guild_set_name, guild_quests_daily_get, guild_quests_weekly_get,
    guild_admin_update, guild_application_add, guild_application_remove,
    guild_application_list, guild_application_has,
    guild_meets_requirements,
)
import datetime as _dt
import os as _os


def _is_owner(user_id) -> bool:
    owner = (_os.getenv("DISCORD_OWNER_ID") or "").strip()
    return bool(owner) and str(user_id) == owner


def _count_emojis(s: str) -> int:
    """Number of "visual" emojis (clusters) in s. Handles ZWJ sequences (families),
    variation selectors, skin tone modifiers and flags (pair of regional indicators)."""
    import unicodedata
    clusters = 0
    join = False
    ri_run = 0
    for c in s:
        cp = ord(c)
        if c == "‍":          # ZWJ: joins the next emoji to the current cluster
            join = True
            continue
        # modifiers that belong to the previous cluster (not a new emoji)
        if (0xFE00 <= cp <= 0xFE0F) or (0x1F3FB <= cp <= 0x1F3FF) or unicodedata.combining(c):
            continue
        if join:
            join = False
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:   # regional indicator: a pair = one flag
            ri_run += 1
            if ri_run == 2:
                clusters += 1
                ri_run = 0
            continue
        ri_run = 0
        clusters += 1
    if ri_run == 1:
        clusters += 1
    return clusters


def _can_master(gid, user_id) -> bool:
    """Guild master OR bot owner (full access)."""
    return _is_owner(user_id) or guild_member_role(gid, user_id) == "master"


def _can_officer(gid, user_id) -> bool:
    """Master/Officer OR bot owner."""
    return _is_owner(user_id) or guild_member_role(gid, user_id) in ("master", "officer")


def _guild_xp_bar(bot, into, span, segments=14):
    filled = min(segments, int(round(segments * into / span))) if span > 0 else segments
    full = str(discord.utils.get(bot.emojis, name="playerlifebarfull") or "🟩")
    empty = str(discord.utils.get(bot.emojis, name="lifebarempty") or "⬛")
    return full * filled + empty * (segments - filled)


def _fmt_n(n):
    return f"{int(n):,}"


def _xp_needed_cumul(level, cfg):
    base = float(cfg.get("level_base", 600)); g = float(cfg.get("level_growth", 1.1))
    return sum(base * (g ** (n - 2)) for n in range(2, level + 1)) if level >= 2 else 0


def _progress_line(guild, cfg, locale="en"):
    lvl = guild["level"]; xp = guild["xp"]; maxlv = int(cfg.get("max_level", 60))
    if lvl >= maxlv:
        return t("guilds.guild.progress_max", locale, level=lvl, xp=f"{xp:,}")
    cur = _xp_needed_cumul(lvl, cfg)
    nxt = _xp_needed_cumul(lvl + 1, cfg)
    into = xp - cur; span = max(1, nxt - cur)
    pct = int(100 * into / span)
    return t("guilds.guild.progress_line", locale, level=lvl, into=f"{int(into):,}",
             span=f"{int(span):,}", pct=pct, next=lvl + 1)


def _perk_lines(rew, locale, short=False):
    """Human readable list of the perks unlocked by a reward tier."""
    out = []
    if rew.get("essence_pct"):
        out.append(t("guilds.guild.perk_essence", locale, pct=rew["essence_pct"]))
    if rew.get("xp_pct"):
        out.append(t("guilds.guild.perk_xp_short" if short else "guilds.guild.perk_xp",
                     locale, pct=rew["xp_pct"]))
    if rew.get("roll_cd_min"):
        out.append(t("guilds.guild.perk_roll_cd_short" if short else "guilds.guild.perk_roll_cd",
                     locale, min=rew["roll_cd_min"]))
    if rew.get("charges"):
        out.append(t("guilds.guild.perk_charges", locale, n=rew["charges"]))
    if rew.get("wishlist"):
        out.append(t("guilds.guild.perk_wishlist", locale, n=rew["wishlist"]))
    if rew.get("boss_pct"):
        out.append(t("guilds.guild.perk_boss", locale, pct=rew["boss_pct"]))
    return out


def setup_guild_commands(bot, deps):
    globals().update(deps)

    grp = app_commands.Group(name="guild", description="Player guilds (clubs)")

    def _is_officer(role):
        return role in ("master", "officer")

    # ---- create ----
    @grp.command(name="create", description="Create a guild (costs essences)")
    @app_commands.describe(name="Guild name", tag="Short tag (optional, e.g. TKBT)")
    async def g_create(interaction: discord.Interaction, name: str, tag: str = None):
        uid = interaction.user.id
        cfg = get_guild_config()
        if guild_of_user(uid):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.already_in"), ephemeral=True); return
        name = name.strip()[:40]
        if len(name) < 2:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.name_too_short"), ephemeral=True); return
        if guild_get_by_name(name):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.name_taken"), ephemeral=True); return
        cost = int(cfg.get("create_cost", 10000))
        if currency_get(uid) < cost:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.create_cost", cost=f"{cost:,}"), ephemeral=True); return
        currency_add(uid, -cost)
        gid = guild_create(name, uid, tag=(tag or "").strip()[:8] or None)
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.created", name=name), ephemeral=True)

    # ---- info ----
    @grp.command(name="info", description="View a guild (yours by default)")
    @app_commands.describe(name="A guild name (yours if omitted)")
    async def g_info(interaction: discord.Interaction, name: str = None):
        loc = locale_of(interaction)
        cfg = get_guild_config()
        g = guild_get_by_name(name) if name else guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message(
                t("guilds.guild.not_found", loc) if name else t("guilds.guild.no_guild_hint", loc),
                ephemeral=True); return
        members = guild_members(g["id"])
        rew = guild_rewards_for_level(g["level"], cfg)
        perks = _perk_lines(rew, loc)
        unlocks = [k for k in ("bank", "raids", "shop") if rew.get(k)]
        title = f"🛡️ {g['name']}" + (f" [{g['tag']}]" if g.get("tag") else "")
        emb = Panel(title)
        emb.field(t("guilds.guild.f_progress", loc), _progress_line(g, cfg, loc))
        emb.field(t("guilds.guild.f_members", loc, count=len(members),
                    max=cfg.get('max_members', 30)),
                  "\n".join(f"{'👑' if m['role']=='master' else ('🔧' if m['role']=='officer' else '▫️')} "
                            f"<@{m['user_id']}> · {m['xp_contributed']:,} XP"
                            for m in members[:30]) or "—")
        emb.field(t("guilds.guild.f_bank", loc), f"{g['bank']:,} ✨", inline=True)
        if perks:
            emb.field(t("guilds.guild.f_perks", loc), " · ".join(perks))
        if unlocks:
            emb.field(t("guilds.guild.f_unlocked", loc), " · ".join(unlocks))
        await interaction.response.send_message(view=emb.view(),
                                                allowed_mentions=discord.AllowedMentions.none())

    # ---- invite ----
    @grp.command(name="invite", description="Invite a player to your guild (Master/Officer)")
    @app_commands.describe(member="Player to invite")
    async def g_invite(interaction: discord.Interaction, member: discord.Member):
        cfg = get_guild_config()
        g = guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.no_guild"), ephemeral=True); return
        if not _is_officer(guild_member_role(g["id"], interaction.user.id)):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.officers_only"), ephemeral=True); return
        if member.bot:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.no_bots"), ephemeral=True); return
        if guild_of_user(member.id):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.player_in_guild"), ephemeral=True); return
        if guild_member_count(g["id"]) >= int(cfg.get("max_members", 30)):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.full"), ephemeral=True); return
        guild_invite_add(g["id"], member.id)
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.invited", member=member.mention, guild=g['name']),
            allowed_mentions=discord.AllowedMentions(users=[member]))

    # ---- accept ----
    @grp.command(name="accept", description="Join a guild that invited you")
    @app_commands.describe(name="Guild name")
    async def g_accept(interaction: discord.Interaction, name: str):
        uid = interaction.user.id
        cfg = get_guild_config()
        if guild_of_user(uid):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.already_in"), ephemeral=True); return
        g = guild_get_by_name(name)
        if not g:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.not_found"), ephemeral=True); return
        if not guild_invite_has(g["id"], uid):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.no_invite"), ephemeral=True); return
        # anti guild-hopping cooldown (owner bypass)
        la = guild_left_at(uid)
        cd_h = int(cfg.get("hop_cooldown_h", 24))
        if la and cd_h > 0 and not _is_owner(uid):
            try:
                left = _dt.datetime.fromisoformat(la.replace("Z", ""))
                delta_h = (_dt.datetime.utcnow() - left).total_seconds() / 3600
                if delta_h < cd_h:
                    await interaction.response.send_message(
                        ti(interaction, "guilds.guild.hop_cooldown", hours=int(cd_h - delta_h) + 1),
                        ephemeral=True); return
            except Exception:
                pass
        if guild_member_count(g["id"]) >= int(cfg.get("max_members", 30)):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.full"), ephemeral=True); return
        guild_add_member(g["id"], uid, "member")
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.joined", name=g['name']), ephemeral=True)

    def _hop_remaining_h(uid, cfg):
        """Hours left on the anti guild-hopping cooldown, or 0. Owner bypass."""
        if _is_owner(uid):
            return 0
        la = guild_left_at(uid)
        cd_h = int(cfg.get("hop_cooldown_h", 24))
        if not la or cd_h <= 0:
            return 0
        try:
            left = _dt.datetime.fromisoformat(la.replace("Z", ""))
            delta_h = (_dt.datetime.utcnow() - left).total_seconds() / 3600
            if delta_h < cd_h:
                return int(cd_h - delta_h) + 1
        except Exception:
            pass
        return 0

    # ---- apply (application / open join) ----
    @grp.command(name="apply", description="Apply to join a guild")
    @app_commands.describe(name="Guild name")
    async def g_apply(interaction: discord.Interaction, name: str):
        uid = interaction.user.id
        cfg = get_guild_config()
        if guild_of_user(uid):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.already_in"), ephemeral=True); return
        g = guild_get_by_name(name)
        if not g:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.not_found"), ephemeral=True); return
        rem = _hop_remaining_h(uid, cfg)
        if rem:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.hop_cooldown", hours=rem), ephemeral=True); return
        if guild_member_count(g["id"]) >= int(cfg.get("max_members", 30)):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.full"), ephemeral=True); return
        ok, reason = guild_meets_requirements(g["id"], uid)
        if not ok:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.reqs_not_met", reason=reason), ephemeral=True); return
        if g.get("open_join"):
            guild_add_member(g["id"], uid, "member")
            guild_application_remove(g["id"], uid)
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.open_joined", name=g['name']), ephemeral=True); return
        if guild_application_has(g["id"], uid):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.apply_pending"), ephemeral=True); return
        guild_application_add(g["id"], uid)
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.apply_sent", name=g['name']), ephemeral=True)

    # ---- applications (Master/Officer) ----
    @grp.command(name="applications", description="View applications to your guild (Master/Officer)")
    async def g_apps(interaction: discord.Interaction):
        uid = interaction.user.id
        g = guild_of_user(uid)
        if not g:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.no_guild"), ephemeral=True); return
        if not _is_officer(guild_member_role(g["id"], uid)):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.officers_only"), ephemeral=True); return
        apps = guild_application_list(g["id"])
        if not apps:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.apps_none"), ephemeral=True); return
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.apps_header", count=len(apps), name=g['name']),
            view=ApplicationsView(bot, g["id"], locale_of(interaction)), ephemeral=True)

    class ApplicationsView(discord.ui.View):
        def __init__(self, bot, gid, locale="en"):
            super().__init__(timeout=180)
            self.gid = gid
            self.locale = locale
            for app in guild_application_list(gid)[:5]:
                auid = app["user_id"]
                u = bot.get_user(int(auid)) if str(auid).isdigit() else None
                name = (u.display_name if u else str(auid))[:40]
                acc = discord.ui.Button(label=f"✅ {name}", style=discord.ButtonStyle.success)
                rej = discord.ui.Button(label="❌", style=discord.ButtonStyle.danger)
                acc.callback = self._mk(auid, True)
                rej.callback = self._mk(auid, False)
                self.add_item(acc); self.add_item(rej)

        def _mk(self, auid, accept):
            async def cb(inter: discord.Interaction):
                if not _is_officer(guild_member_role(self.gid, inter.user.id)):
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.officers_only"), ephemeral=True); return
                g = guild_get(self.gid)
                if not g:
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.gone"), ephemeral=True); return
                if not accept:
                    guild_application_remove(self.gid, auid)
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.app_rejected", user=auid),
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none()); return
                cfg = get_guild_config()
                if guild_of_user(auid):
                    guild_application_remove(self.gid, auid)
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.player_in_guild"), ephemeral=True); return
                if guild_member_count(self.gid) >= int(cfg.get("max_members", 30)):
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.full"), ephemeral=True); return
                ok, reason = guild_meets_requirements(self.gid, auid)
                if not ok:
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.app_reqs_lost", reason=reason),
                        ephemeral=True); return
                guild_add_member(self.gid, auid, "member")
                guild_application_remove(self.gid, auid)
                await inter.response.send_message(
                    ti(inter, "guilds.guild.app_accepted", user=auid),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none())
            return cb

    # ---- leave ----
    @grp.command(name="leave", description="Leave your guild")
    async def g_leave(interaction: discord.Interaction):
        uid = interaction.user.id
        g = guild_of_user(uid)
        if not g:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.no_guild"), ephemeral=True); return
        if guild_member_role(g["id"], uid) == "master":
            if guild_member_count(g["id"]) > 1:
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.leave_master"), ephemeral=True); return
            guild_delete(g["id"])
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.disbanded_alone"), ephemeral=True); return
        guild_remove_member(g["id"], uid)
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.left", name=g['name']), ephemeral=True)

    # ---- kick ----
    @grp.command(name="kick", description="Kick a member out of the guild (Master/Officer)")
    @app_commands.describe(member="Member to kick")
    async def g_kick(interaction: discord.Interaction, member: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.no_guild"), ephemeral=True); return
        my = guild_member_role(g["id"], interaction.user.id)
        if not _is_officer(my):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.officers_only"), ephemeral=True); return
        tgt = guild_member_role(g["id"], member.id)
        if not tgt:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.not_member"), ephemeral=True); return
        if tgt == "master" or (tgt == "officer" and my != "master"):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.kick_denied"), ephemeral=True); return
        guild_remove_member(g["id"], member.id)
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.kicked", member=member.mention, name=g['name']),
            allowed_mentions=discord.AllowedMentions.none())

    # ---- promote / demote / transfer ----
    @grp.command(name="promote", description="Promote a member to officer (Master)")
    @app_commands.describe(member="Member")
    async def g_promote(interaction: discord.Interaction, member: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
        if guild_member_role(g["id"], member.id) != "member":
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.invalid_member"), ephemeral=True); return
        guild_set_role(g["id"], member.id, "officer")
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.promoted", member=member.mention),
            allowed_mentions=discord.AllowedMentions.none())

    @grp.command(name="demote", description="Demote an officer (Master)")
    @app_commands.describe(member="Officer")
    async def g_demote(interaction: discord.Interaction, member: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
        if guild_member_role(g["id"], member.id) != "officer":
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.not_officer"), ephemeral=True); return
        guild_set_role(g["id"], member.id, "member")
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.demoted", member=member.mention),
            allowed_mentions=discord.AllowedMentions.none())

    @grp.command(name="transfer", description="Transfer guild mastership (Master)")
    @app_commands.describe(member="New Master")
    async def g_transfer(interaction: discord.Interaction, member: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
        if not guild_member_role(g["id"], member.id):
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.not_member"), ephemeral=True); return
        guild_set_role(g["id"], interaction.user.id, "officer")
        guild_set_owner(g["id"], member.id)
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.transferred", member=member.mention),
            allowed_mentions=discord.AllowedMentions.none())

    # ---- disband ----
    @grp.command(name="disband", description="Disband your guild (Master)")
    async def g_disband(interaction: discord.Interaction):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
        guild_delete(g["id"])
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.disbanded", name=g['name']), ephemeral=True)

    # ---- donate ----
    @grp.command(name="donate", description="Donate essences to your guild bank")
    @app_commands.describe(amount="Essences to donate")
    async def g_donate(interaction: discord.Interaction, amount: int):
        uid = interaction.user.id
        g = guild_of_user(uid)
        if not g:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.no_guild"), ephemeral=True); return
        cfg = get_guild_config()
        if not guild_rewards_for_level(g["level"], cfg).get("bank"):
            lv = _unlock_level(cfg, "bank") or "?"
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.bank_locked", level=lv), ephemeral=True); return
        if amount <= 0:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.invalid_amount"), ephemeral=True); return
        if currency_get(uid) < amount:
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.not_enough_essence"), ephemeral=True); return
        currency_add(uid, -amount)
        guild_bank_add(g["id"], amount)
        per100 = int(cfg.get("xp", {}).get("essence_per_100", 0))
        xp = (amount // 100) * per100
        if xp > 0:
            # NOTE: "don d'essences" is a stored XP-log key mapped by
            # templates/cards_my_guild.html - do not translate it.
            guild_member_action_xp(uid, xp, source="don d'essences")
        try:
            from database import guild_quest_progress
            guild_quest_progress(uid, "donate", amount)
        except Exception:
            pass
        xp_txt = ti(interaction, "guilds.guild.donated_xp", xp=xp) if xp else ""
        await interaction.response.send_message(
            ti(interaction, "guilds.guild.donated", amount=f"{amount:,}",
               name=g['name'], xp=xp_txt), ephemeral=True)

    # ---- top ----
    @grp.command(name="top", description="Guild leaderboard")
    async def g_top(interaction: discord.Interaction):
        loc = locale_of(interaction)
        rows = guild_top(15)
        if not rows:
            await interaction.response.send_message(
                t("guilds.guild.top_empty", loc), ephemeral=True); return
        lines = []
        for i, g in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i}.`")
            tag = f" [{g['tag']}]" if g.get("tag") else ""
            lines.append(t("guilds.guild.top_line", loc, medal=medal, name=g['name'],
                           tag=tag, level=g['level'], members=g['members']))
        emb = Panel(t("guilds.guild.top_title", loc), "\n".join(lines))
        await interaction.response.send_message(view=emb.view())

    @g_accept.autocomplete("name")
    @g_info.autocomplete("name")
    @g_apply.autocomplete("name")
    async def _guild_name_ac(interaction: discord.Interaction, current: str):
        try:
            q = (current or "").strip().lower()
            rows = guild_top(200)
            out = [g["name"] for g in rows if q in g["name"].lower()][:25] if q else [g["name"] for g in rows[:25]]
            return [app_commands.Choice(name=n[:100], value=n[:100]) for n in out]
        except Exception:
            return []

    bot.tree.add_command(grp)

    # ===== /guildprofile: rich business card of a guild =====
    _ROLE_ICON = {"master": "👑", "officer": "🔧", "member": "▫️"}
    _ROLE_RANK = {"master": 0, "officer": 1, "member": 2}

    def _build_member_rows(g):
        """[{user_id, role, power, cards}] for every member (stats computed)."""
        rows = []
        for m in guild_members(g["id"]):
            try:
                st = compute_player_combat_stats(m["user_id"])
                pw = combat_power(st["hp"], st["atk"])
            except Exception:
                pw = 0
            try:
                cards = user_card_count(m["user_id"])
            except Exception:
                cards = 0
            rows.append({"user_id": m["user_id"], "role": m["role"], "power": pw, "cards": cards})
        return rows

    def _guildprofile_panel(g, rows, sort, locale="en"):
        """Components V2 panel of the guild profile (replaces the former embed).
        No accent colour any more: the profile colour is dropped."""
        cfg = get_guild_config()
        if sort == "role":
            rows = sorted(rows, key=lambda r: (_ROLE_RANK.get(r["role"], 9), -r["power"]))
        elif sort == "cards":
            rows = sorted(rows, key=lambda r: -r["cards"])
        else:
            sort = "power"; rows = sorted(rows, key=lambda r: -r["power"])
        total_power = sum(r["power"] for r in rows)
        # XP bar inside the current level
        lvl = g["level"]; maxlv = int(cfg.get("max_level", 60))
        cur = _xp_needed_cumul(lvl, cfg); nxt = _xp_needed_cumul(lvl + 1, cfg)
        into = g["xp"] - cur; span = max(1, nxt - cur)
        bar = _guild_xp_bar(bot, into, span)
        pct = 100 if lvl >= maxlv else int(100 * into / span)
        tag = f" [{g['tag']}]" if g.get("tag") else ""
        emblem = g.get("emblem") or "🛡️"
        pan = Panel(f"{emblem} {g['name']}{tag}")
        pan.field(
            t("guilds.guild.profile_level_max" if lvl >= maxlv else "guilds.guild.profile_level",
              locale, level=lvl),
            (f"{bar}  **{pct}%**\n" + (f"_{_fmt_n(into)} / {_fmt_n(span)} XP_" if lvl < maxlv else f"_{_fmt_n(g['xp'])} XP_")))
        # Total power in custom digit emojis (members stay plain text)
        try:
            from services.card_boss import _power_digits
            pw_str = _power_digits(bot, total_power)
        except Exception:
            pw_str = f"**{_fmt_n(total_power)}**"
        pan.field(t("guilds.guild.profile_power", locale), pw_str + "\n​")
        pan.field(t("guilds.guild.profile_bank", locale),
                  f"{_fmt_n(g['bank'])} ✨\n​")
        # Next tier (level + what it brings that is NEW)
        cur_rew = guild_rewards_for_level(lvl, cfg)
        nxt = next((p for p in sorted(cfg.get("rewards", []), key=lambda x: x.get("level", 0))
                    if p.get("level", 0) > lvl), None)
        if lvl >= maxlv or not nxt:
            up_txt = t("guilds.guild.profile_max_tier", locale)
        else:
            bits = _perk_lines(nxt, locale, short=True)
            for k, lbl_key in (("bank", "guilds.guild.feat_bank"), ("raids", "guilds.guild.feat_raids"),
                               ("shop", "guilds.guild.feat_shop")):
                if nxt.get(k) and not cur_rew.get(k):
                    bits.append(t("guilds.guild.profile_unlocks", locale, feature=t(lbl_key, locale)))
            up_txt = t("guilds.guild.profile_next_line", locale, level=nxt['level'],
                       perks=(" · ".join(bits) or "—"))
        pan.field(t("guilds.guild.profile_next", locale), up_txt)
        lines = []
        for r in rows[:30]:
            lines.append(f"{_ROLE_ICON.get(r['role'],'▫️')} <@{r['user_id']}> — "
                         + t("guilds.guild.profile_member_line", locale,
                             power=_fmt_n(r['power']), cards=r['cards']))
        pan.field("───────────────────────",
                  t("guilds.guild.profile_members_header", locale,
                    count=len(rows), sort=sort) + "\n" + ("\n".join(lines) or "—"))
        return pan

    class ShopBuyView(discord.ui.LayoutView):
        """Panel + shop buttons (5 per ActionRow, a classic View spread them itself)."""

        def __init__(self, gid, locale="en", panel=None):
            super().__init__(timeout=120)
            self.gid = gid
            self.locale = locale
            self._rows = []
            cur = discord.ui.ActionRow()
            for it in (get_guild_config().get("shop") or [])[:20]:
                if len(cur.children) >= 5:
                    self._rows.append(cur)
                    cur = discord.ui.ActionRow()
                b = discord.ui.Button(label=f"{it['name']} — {it['cost']} ✨",
                                      style=discord.ButtonStyle.primary)
                b.callback = self._mk(it)
                cur.add_item(b)
            if cur.children:
                self._rows.append(cur)
            self.set_panel(panel)

        def set_panel(self, panel):
            self.clear_items()
            if panel is not None:
                self.add_item(panel.container())
            for r in self._rows:
                self.add_item(r)
            return self

        def _mk(self, it):
            async def cb(inter: discord.Interaction):
                if not guild_get(self.gid):
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.gone"), ephemeral=True); return
                if not _can_officer(self.gid, inter.user.id):
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.officers_only"), ephemeral=True); return
                if not guild_bank_spend(self.gid, int(it["cost"])):
                    await inter.response.send_message(
                        ti(inter, "guilds.guild.shop_no_bank"), ephemeral=True); return
                kind = it["type"]; v = int(it["value"])
                if kind == "guild_xp":
                    guild_add_xp(self.gid, v)
                    eff = ti(inter, "guilds.guild.shop_eff_xp", amount=v)
                elif kind == "rolls_all":
                    for mid in guild_member_ids(self.gid):
                        roll_give_user(mid, v)
                    eff = ti(inter, "guilds.guild.shop_eff_rolls", amount=v)
                elif kind == "essence_all":
                    for mid in guild_member_ids(self.gid):
                        currency_add(mid, v)
                    eff = ti(inter, "guilds.guild.shop_eff_essence", amount=v)
                else:
                    eff = ti(inter, "guilds.guild.shop_eff_other")
                g = guild_get(self.gid)
                await inter.response.send_message(
                    ti(inter, "guilds.guild.shop_bought", item=it['name'],
                       buyer=inter.user.mention, effect=eff, bank=_fmt_n(g['bank'])),
                    allowed_mentions=discord.AllowedMentions.none())
            return cb

    def _unlock_level(cfg, key):
        """Lowest level at which an unlock (bank/shop/raids) becomes active, or None."""
        for p in sorted(cfg.get("rewards", []), key=lambda x: x.get("level", 0)):
            if p.get(key):
                return p.get("level")
        return None

    def _gone_view(interaction):
        """A V2 message cannot fall back to plain content: replace the whole view."""
        return Panel(description=ti(interaction, "guilds.guild.gone")).view()

    class _ProfileRowTop(discord.ui.ActionRow):
        @discord.ui.button(label="Sort: power", emoji="⚡", style=discord.ButtonStyle.secondary)
        async def s_rotate(self, interaction, btn):
            v = self.view
            v.sort = v._SORT_ROT.get(v.sort, "power")
            btn.label = ti(interaction, v._SORT_KEY[v.sort])
            btn.emoji = v._SORT_EMO[v.sort]
            await v._refresh(interaction)

        @discord.ui.button(label="Quests", emoji="📜", style=discord.ButtonStyle.primary)
        async def b_quests(self, interaction, btn):
            v = self.view
            g = guild_get(v.gid)
            if not g:
                await interaction.response.edit_message(view=_gone_view(interaction)); return
            loc = locale_of(interaction)
            qv = QuestView(v.gid, v.rows, v.invoker_role, interaction.user.id, loc)
            qv.set_panel(_quests_daily_panel(interaction.client, g, interaction.user.id, loc))
            await interaction.response.edit_message(
                view=qv, allowed_mentions=discord.AllowedMentions.none())

    class _ProfileRowBottom(discord.ui.ActionRow):
        @discord.ui.button(label="Bank", emoji="💰", style=discord.ButtonStyle.success)
        async def b_bank(self, interaction, btn):
            v = self.view
            if not v._bank_ok:
                lv = v._bank_lv or "?"
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.bank_locked_lv", level=lv), ephemeral=True); return
            g = guild_get(v.gid)
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.bank_info", name=g['name'], bank=_fmt_n(g['bank'])),
                ephemeral=True)

        @discord.ui.button(label="Shop", emoji="🛒", style=discord.ButtonStyle.primary)
        async def b_shop(self, interaction, btn):
            v = self.view
            loc = locale_of(interaction)
            if not v._shop_ok:
                lv = v._shop_lv or "?"
                await interaction.response.send_message(
                    t("guilds.guild.shop_locked_lv", loc, level=lv), ephemeral=True); return
            items = get_guild_config().get("shop") or []
            if not items:
                await interaction.response.send_message(
                    t("guilds.guild.shop_empty", loc), ephemeral=True); return
            desc = "\n".join(f"**{it['name']}** — {_fmt_n(it['cost'])} ✨\n_{it.get('desc','')}_"
                             for it in items)
            emb = Panel(t("guilds.guild.shop_title", loc), desc)
            emb.footer(t("guilds.guild.shop_footer", loc))
            await interaction.response.send_message(
                view=ShopBuyView(v.gid, loc, emb), ephemeral=True)

        @discord.ui.button(label="Guild customization", emoji="🎨", style=discord.ButtonStyle.secondary)
        async def b_custom(self, interaction, btn):
            v = self.view
            if not _can_master(v.gid, interaction.user.id):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
            loc = locale_of(interaction)
            await interaction.response.send_message(
                t("guilds.guild.custom_intro", loc),
                view=GuildCustomizeView(v.gid, loc), ephemeral=True)

    class GuildProfileView(discord.ui.LayoutView):
        def __init__(self, gid, rows, invoker_role=None, locale="en"):
            super().__init__(timeout=180)
            self.gid = gid; self.rows = rows; self.sort = "power"
            self.invoker_role = invoker_role
            self.locale = locale
            self.row_top = _ProfileRowTop()
            self.row_bottom = _ProfileRowBottom()
            self.row_top.s_rotate.label = t("guilds.guild.sort_power", locale)
            self.row_top.b_quests.label = t("guilds.guild.btn_quests", locale)
            self.row_bottom.b_bank.label = t("guilds.guild.btn_bank", locale)
            self.row_bottom.b_shop.label = t("guilds.guild.btn_shop", locale)
            self.row_bottom.b_custom.label = t("guilds.guild.btn_custom", locale)
            cfg = get_guild_config()
            g = guild_get(gid)
            rew = guild_rewards_for_level(g["level"], cfg) if g else {}
            self._bank_ok = bool(rew.get("bank"))
            self._shop_ok = bool(rew.get("shop"))
            self._bank_lv = _unlock_level(cfg, "bank")
            self._shop_lv = _unlock_level(cfg, "shop")
            # Shop: only visible to Master / Officer
            if invoker_role not in ("master", "officer"):
                self.row_bottom.remove_item(self.row_bottom.b_shop)
            elif not self._shop_ok:
                self.row_bottom.b_shop.style = discord.ButtonStyle.secondary
                self.row_bottom.b_shop.emoji = "🔒"
            # Customization: Master only
            if invoker_role != "master":
                self.row_bottom.remove_item(self.row_bottom.b_custom)
            # Visual lock on the bank (greyed out, padlock) if not unlocked, still clickable
            if not self._bank_ok:
                self.row_bottom.b_bank.style = discord.ButtonStyle.secondary
                self.row_bottom.b_bank.emoji = "🔒"
            self.set_panel(_guildprofile_panel(g, rows, self.sort, locale) if g else None)

        def set_panel(self, panel):
            """V2 refresh: clear_items + re-add the container and the rows."""
            self.clear_items()
            if panel is not None:
                self.add_item(panel.container())
            self.add_item(self.row_top)
            self.add_item(self.row_bottom)
            return self

        async def _refresh(self, interaction):
            g = guild_get(self.gid)
            if not g:
                await interaction.response.edit_message(view=_gone_view(interaction)); return
            self.set_panel(_guildprofile_panel(g, self.rows, self.sort, self.locale))
            await interaction.response.edit_message(
                view=self, allowed_mentions=discord.AllowedMentions.none())

        _SORT_ROT = {"power": "role", "role": "cards", "cards": "power"}
        _SORT_KEY = {"power": "guilds.guild.sort_power", "role": "guilds.guild.sort_role",
                     "cards": "guilds.guild.sort_cards"}
        _SORT_EMO = {"power": "⚡", "role": "👑", "cards": "🎴"}

    class GuildCustomizeView(discord.ui.View):
        def __init__(self, gid, locale="en"):
            super().__init__(timeout=300)
            self.gid = gid
            self.locale = locale
            self.rename.label = t("guilds.guild.btn_rename", locale)
            self.set_emblem.label = t("guilds.guild.btn_emblem", locale)
            self.clear_emblem.label = t("guilds.guild.btn_emblem_clear", locale)
            self.reqs.label = t("guilds.guild.btn_reqs", locale)
            self.add_item(self._ColorSelect(gid, locale))

        class _ColorSelect(discord.ui.Select):
            def __init__(self, gid, locale="en"):
                self.gid = gid
                g = guild_get(gid)
                cur = (g or {}).get("color")
                opts = [discord.SelectOption(label=c["name"], value=c["key"],
                                             default=(c["key"] == cur)) for c in PROFILE_COLORS]
                super().__init__(placeholder=t("guilds.guild.color_placeholder", locale),
                                 options=opts, min_values=1, max_values=1)

            async def callback(self, interaction):
                if not _can_master(self.gid, interaction.user.id):
                    await interaction.response.send_message(
                        ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
                key = self.values[0]
                guild_set_color(self.gid, key)
                col = next((c for c in PROFILE_COLORS if c["key"] == key), None)
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.color_applied",
                       color=(col['name'] if col else key)), ephemeral=True)

        @discord.ui.button(label="Rename guild", emoji="✏️", style=discord.ButtonStyle.secondary)
        async def rename(self, interaction, btn):
            if not _can_master(self.gid, interaction.user.id):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
            g = guild_get(self.gid)
            # Cooldown 1/month (30 days)
            ra = (g or {}).get("renamed_at")
            if ra:
                try:
                    last = _dt.datetime.fromisoformat(ra.replace("Z", ""))
                    days = (_dt.datetime.utcnow() - last).total_seconds() / 86400
                    if days < 30:
                        await interaction.response.send_message(
                            ti(interaction, "guilds.guild.rename_cooldown",
                               days=int(30 - days) + 1), ephemeral=True)
                        return
                except Exception:
                    pass
            await interaction.response.send_modal(
                GuildRenameModal(self.gid, locale_of(interaction)))

        @discord.ui.button(label="Set emblem", emoji="🏅", style=discord.ButtonStyle.primary)
        async def set_emblem(self, interaction, btn):
            if not _can_master(self.gid, interaction.user.id):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
            await interaction.response.send_modal(EmblemModal(self.gid, locale_of(interaction)))

        @discord.ui.button(label="Remove emblem", emoji="🗑️", style=discord.ButtonStyle.secondary)
        async def clear_emblem(self, interaction, btn):
            if not _can_master(self.gid, interaction.user.id):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
            guild_set_emblem(self.gid, None)
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.emblem_cleared"), ephemeral=True)

        @discord.ui.button(label="Requirements & access", emoji="🚪", style=discord.ButtonStyle.secondary)
        async def reqs(self, interaction, btn):
            if not _can_master(self.gid, interaction.user.id):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
            await interaction.response.send_modal(GuildReqModal(self.gid, locale_of(interaction)))

    class GuildReqModal(discord.ui.Modal):
        def __init__(self, gid, locale="en"):
            super().__init__(title=t("guilds.guild.reqs_title", locale))
            self.gid = gid
            self.locale = locale
            g = guild_get(gid) or {}
            self.pw_in = discord.ui.TextInput(
                label=t("guilds.guild.reqs_power_label", locale),
                placeholder=t("guilds.guild.reqs_placeholder", locale), required=False,
                default=str(g.get("min_power") or 0), max_length=12)
            self.cards_in = discord.ui.TextInput(
                label=t("guilds.guild.reqs_cards_label", locale),
                placeholder=t("guilds.guild.reqs_placeholder", locale), required=False,
                default=str(g.get("min_level") or 0), max_length=8)
            self.open_in = discord.ui.TextInput(
                label=t("guilds.guild.reqs_open_label", locale),
                placeholder=t("guilds.guild.reqs_open_placeholder", locale),
                required=False,
                default=t("guilds.guild.reqs_yes" if g.get("open_join") else "guilds.guild.reqs_no",
                          locale),
                max_length=4)
            self.add_item(self.pw_in); self.add_item(self.cards_in); self.add_item(self.open_in)

        async def on_submit(self, interaction):
            from database import guild_admin_update
            if not _can_master(self.gid, interaction.user.id):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
            def _n(v):
                try: return max(0, int(str(v).strip() or 0))
                except Exception: return 0
            # legacy "oui"/"o" kept as tolerated aliases for existing users
            open_join = 1 if str(self.open_in.value).strip().lower() in (
                "yes", "y", "true", "1", "oui", "o") else 0
            guild_admin_update(self.gid, {
                "min_power": _n(self.pw_in.value),
                "min_level": _n(self.cards_in.value),
                "open_join": open_join,
            })
            mode = ti(interaction, "guilds.guild.reqs_mode_open" if open_join
                      else "guilds.guild.reqs_mode_apply")
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.reqs_saved", power=_n(self.pw_in.value),
                   cards=_n(self.cards_in.value), mode=mode), ephemeral=True)

    class EmblemModal(discord.ui.Modal):
        def __init__(self, gid, locale="en"):
            super().__init__(title=t("guilds.guild.emblem_title", locale))
            self.gid = gid
            self.locale = locale
            self.emoji_in = discord.ui.TextInput(
                label=t("guilds.guild.emblem_label", locale),
                placeholder=t("guilds.guild.emblem_placeholder", locale),
                max_length=8, required=True)
            self.add_item(self.emoji_in)

        async def on_submit(self, interaction):
            val = str(self.emoji_in.value).strip()
            if not val:
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.emblem_empty"), ephemeral=True); return
            if "<" in val or ":" in val:
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.emblem_custom"), ephemeral=True); return
            if any(ch.isalnum() for ch in val):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.emblem_text"), ephemeral=True); return
            if _count_emojis(val) != 1:
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.emblem_one"), ephemeral=True); return
            guild_set_emblem(self.gid, val)
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.emblem_set", emoji=val), ephemeral=True)

    class GuildRenameModal(discord.ui.Modal):
        def __init__(self, gid, locale="en"):
            super().__init__(title=t("guilds.guild.rename_title", locale))
            self.gid = gid
            self.locale = locale
            self.name_in = discord.ui.TextInput(
                label=t("guilds.guild.rename_label", locale),
                placeholder=t("guilds.guild.rename_placeholder", locale),
                min_length=3, max_length=32, required=True)
            self.add_item(self.name_in)

        async def on_submit(self, interaction):
            if not _can_master(self.gid, interaction.user.id):
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.master_only"), ephemeral=True); return
            g = guild_get(self.gid)
            if not g:
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.gone"), ephemeral=True); return
            # Re-check the cooldown on submit (anti bypass)
            ra = g.get("renamed_at")
            if ra:
                try:
                    last = _dt.datetime.fromisoformat(ra.replace("Z", ""))
                    days = (_dt.datetime.utcnow() - last).total_seconds() / 86400
                    if days < 30:
                        await interaction.response.send_message(
                            ti(interaction, "guilds.guild.rename_cooldown_short",
                               days=int(30 - days) + 1), ephemeral=True); return
                except Exception:
                    pass
            new = str(self.name_in.value).strip()
            if len(new) < 3:
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.rename_short"), ephemeral=True); return
            if new.lower() == (g.get("name") or "").lower():
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.rename_same"), ephemeral=True); return
            other = guild_get_by_name(new)
            if other and other["id"] != self.gid:
                await interaction.response.send_message(
                    ti(interaction, "guilds.guild.rename_taken"), ephemeral=True); return
            guild_set_name(self.gid, new)
            await interaction.response.send_message(
                ti(interaction, "guilds.guild.renamed", name=new), ephemeral=True)

    def _quest_bar(into, span, seg=10):
        span = max(1, span)
        filled = min(seg, int(round(seg * into / span)))
        full = str(discord.utils.get(bot.emojis, name="playerlifebarfull") or "🟩")
        empty = str(discord.utils.get(bot.emojis, name="lifebarempty") or "⬛")
        return full * filled + empty * (seg - filled)

    def _quests_daily_panel(bot, g, user_id, locale="en"):
        pan = Panel(t("guilds.guild.quests_daily_title", locale, name=g['name']),
                    t("guilds.guild.quests_daily_desc", locale))
        for q in guild_quests_daily_get(user_id, g["id"]):
            prog = min(q["progress"], q["target"])
            check = "✅" if q["done"] else "⬜"
            pan.field(
                f"{check} {q['label']}",
                f"{_quest_bar(prog, q['target'])}  **{prog}/{q['target']}**  ·  +{q['xp']} XP")
        pan.footer(t("guilds.guild.quests_daily_footer", locale))
        return pan

    def _quests_weekly_panel(bot, g, locale="en"):
        pan = Panel(t("guilds.guild.quests_weekly_title", locale, name=g['name']),
                    t("guilds.guild.quests_weekly_desc", locale))
        for q in guild_quests_weekly_get(g["id"]):
            prog = min(q["progress"], q["target"])
            check = "✅" if q["done"] else "⬜"
            reward = t("guilds.guild.quest_reward_xp", locale, xp=q['xp'])
            if q.get("bank"):
                reward += t("guilds.guild.quest_reward_bank", locale, amount=_fmt_n(q['bank']))
            top = q.get("contrib", [])[:3]
            contrib_txt = ""
            if top:
                contrib_txt = "\n" + " · ".join(f"<@{c['user_id']}> ({_fmt_n(c['contrib'])})" for c in top)
            pan.field(
                f"{check} {q['label']}",
                f"{_quest_bar(prog, q['target'])}  **{_fmt_n(prog)}/{_fmt_n(q['target'])}**  ·  {reward}"
                + contrib_txt)
        pan.footer(t("guilds.guild.quests_weekly_footer", locale))
        return pan

    class _QuestRow(discord.ui.ActionRow):
        @discord.ui.button(label="Daily / Weekly", emoji="🔁", style=discord.ButtonStyle.primary)
        async def toggle(self, interaction, btn):
            v = self.view
            g = guild_get(v.gid)
            if not g:
                await interaction.response.edit_message(view=_gone_view(interaction)); return
            v.page = "weekly" if v.page == "daily" else "daily"
            pan = (_quests_weekly_panel(interaction.client, g, v.locale) if v.page == "weekly"
                   else _quests_daily_panel(interaction.client, g, v.invoker_id, v.locale))
            v.set_panel(pan)
            await interaction.response.edit_message(
                view=v, allowed_mentions=discord.AllowedMentions.none())

        @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary)
        async def back(self, interaction, btn):
            v = self.view
            g = guild_get(v.gid)
            if not g:
                await interaction.response.edit_message(view=_gone_view(interaction)); return
            view = GuildProfileView(v.gid, v.rows, invoker_role=v.invoker_role,
                                    locale=v.locale)
            await interaction.response.edit_message(
                view=view, allowed_mentions=discord.AllowedMentions.none())

    class QuestView(discord.ui.LayoutView):
        def __init__(self, gid, rows, invoker_role, invoker_id, locale="en"):
            super().__init__(timeout=180)
            self.gid = gid; self.rows = rows
            self.invoker_role = invoker_role; self.invoker_id = invoker_id
            self.locale = locale
            self.page = "daily"
            self.quest_row = _QuestRow()
            self.quest_row.toggle.label = t("guilds.guild.btn_quest_toggle", locale)
            self.quest_row.back.label = t("guilds.guild.btn_back", locale)
            self.set_panel(None)

        def set_panel(self, panel):
            self.clear_items()
            if panel is not None:
                self.add_item(panel.container())
            self.add_item(self.quest_row)
            return self

    @bot.tree.command(name="guildprofile",
                       description="A guild's profile (yours by default)")
    @app_commands.describe(name="A guild name (yours if omitted)")
    async def guildprofile(interaction: discord.Interaction, name: str = None):
        loc = locale_of(interaction)
        g = guild_get_by_name(name) if name else guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message(
                t("guilds.guild.not_found", loc) if name else t("guilds.guild.no_guild", loc),
                ephemeral=True)
            return
        await interaction.response.defer()
        rows = _build_member_rows(g)
        inv_role = "master" if _is_owner(interaction.user.id) else guild_member_role(g["id"], interaction.user.id)
        view = GuildProfileView(g["id"], rows, invoker_role=inv_role, locale=loc)
        await interaction.followup.send(
            view=view, allowed_mentions=discord.AllowedMentions.none())

    @guildprofile.autocomplete("name")
    async def _gp_ac(interaction: discord.Interaction, current: str):
        try:
            q = (current or "").strip().lower()
            rows = guild_top(200)
            out = [g["name"] for g in rows if q in g["name"].lower()][:25] if q else [g["name"] for g in rows[:25]]
            return [app_commands.Choice(name=n[:100], value=n[:100]) for n in out]
        except Exception:
            return []
