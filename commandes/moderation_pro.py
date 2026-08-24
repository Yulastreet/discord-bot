"""Enriched moderation commands: /warn, /modlogs, /clearwarns, /note.

Every sanction created here (warn / kick / ban / timeout / note) is persisted
in the mod_actions table and posted in the mod-log channel configured for the
guild through the dashboard.

Auto-timeout: when the number of active warns goes over autotimeout_threshold
(set per guild in mod_config), the member is automatically timed out for
autotimeout_duration seconds.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    mod_action_add, mod_actions_list, mod_action_get, mod_action_revoke,
    mod_action_count_active, mod_config_get,
)
from services.i18n import DEFAULT_LOCALE, guild_locale, locale_of, t, ti


# action_type -> i18n key of the human label
ACTION_LABEL_KEY = {
    "warn":       "server.modpro.action.warn",
    "kick":       "server.modpro.action.kick",
    "ban":        "server.modpro.action.ban",
    "unban":      "server.modpro.action.unban",
    "timeout":    "server.modpro.action.timeout",
    "untimeout":  "server.modpro.action.untimeout",
    "note":       "server.modpro.action.note",
}
ACTION_COLOR = {
    "warn":      0xF1C40F,
    "kick":      0xE67E22,
    "ban":       0xC0392B,
    "unban":     0x2ECC71,
    "timeout":   0x9B59B6,
    "untimeout": 0x2ECC71,
    "note":      0x95A5A6,
}


def _action_label(atype: str, locale: str = DEFAULT_LOCALE) -> str:
    key = ACTION_LABEL_KEY.get(atype)
    return t(key, locale) if key else atype


def _err(title: str, msg: str) -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=msg, color=0xE74C3C)


def _ok(title: str, msg: str, color: int = 0x2ECC71) -> discord.Embed:
    return discord.Embed(title=title, description=msg, color=color)


def _fmt_dt(s: Optional[str]) -> str:
    if not s:
        return "—"
    return s.split(".")[0]


async def _post_modlog(guild: discord.Guild, embed: discord.Embed) -> None:
    """Post the embed in the configured modlog_channel when available."""
    cfg = mod_config_get(guild.id)
    ch_id = cfg.get("modlog_channel_id")
    if not ch_id:
        return
    ch = guild.get_channel(int(ch_id))
    if not isinstance(ch, (discord.TextChannel, discord.Thread)):
        return
    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"[mod/modlog] send err: {type(e).__name__}: {e}")


def _build_action_embed(action: dict, *, member: Optional[discord.abc.User] = None,
                        moderator: Optional[discord.abc.User] = None,
                        title_prefix: str = "",
                        locale: str = DEFAULT_LOCALE) -> discord.Embed:
    atype = action["action_type"]
    color = ACTION_COLOR.get(atype, 0x95A5A6)
    title = f"{title_prefix}{_action_label(atype, locale)} · #{action['id']}"
    embed = discord.Embed(title=title, color=color, timestamp=_dt.datetime.now(_dt.timezone.utc))
    field_member = t("server.modpro.field_member", locale)
    if member:
        embed.add_field(name=field_member, value=f"{member.mention} (`{member.id}`)", inline=False)
    else:
        embed.add_field(name=field_member, value=f"`{action['user_id']}`", inline=False)
    embed.add_field(name=t("server.modpro.field_reason", locale),
                    value=action.get("reason") or t("server.modpro.no_reason_italic", locale),
                    inline=False)
    if moderator:
        embed.add_field(name=t("server.modpro.field_moderator", locale),
                        value=f"{moderator.mention}", inline=True)
    elif action.get("moderator_id"):
        embed.add_field(name=t("server.modpro.field_moderator", locale),
                        value=f"<@{action['moderator_id']}>", inline=True)
    if action.get("duration_sec"):
        mins = action["duration_sec"] // 60
        embed.add_field(name=t("server.modpro.field_duration", locale),
                        value=t("server.modpro.duration_minutes", locale, minutes=mins), inline=True)
    if action.get("revoked_at"):
        embed.add_field(
            name=t("server.modpro.field_revoked", locale),
            value=t("server.modpro.revoked_value", locale,
                    moderator=action.get("revoked_by"),
                    date=_fmt_dt(action.get("revoked_at")),
                    reason=action.get("revoke_reason") or t("server.modpro.no_reason", locale)),
            inline=False,
        )
    return embed


async def _apply_auto_timeout_if_needed(member: discord.Member, moderator: discord.abc.User,
                                        locale: str = DEFAULT_LOCALE) -> Optional[int]:
    """Apply an automatic timeout when the member reached the active warn threshold.
    Returns the applied duration (in seconds) or None."""
    cfg = mod_config_get(member.guild.id)
    threshold = int(cfg.get("autotimeout_threshold") or 0)
    if threshold <= 0:
        return None
    active_warns = mod_action_count_active(member.guild.id, member.id, "warn")
    if active_warns < threshold:
        return None
    duration_sec = int(cfg.get("autotimeout_duration") or 600)
    try:
        until = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=duration_sec)
        await member.timeout(until, reason=f"Auto-timeout: {active_warns} active warns (threshold {threshold})")
    except discord.Forbidden:
        print(f"[mod/auto-timeout] forbidden member={member.id}")
        return None
    except Exception as e:
        print(f"[mod/auto-timeout] err: {type(e).__name__}: {e}")
        return None
    action_id = mod_action_add(
        member.guild.id, member.id, "timeout",
        reason=t("server.modpro.auto_timeout_reason", locale, count=active_warns),
        moderator_id=moderator.id,
        duration_sec=duration_sec,
    )
    auto_embed = _build_action_embed(
        mod_action_get(action_id) or {"id": action_id, "action_type": "timeout",
                                      "user_id": str(member.id), "duration_sec": duration_sec},
        member=member,
        moderator=moderator,
        title_prefix="🤖 ",
        locale=locale,
    )
    await _post_modlog(member.guild, auto_embed)
    return duration_sec


def setup_mod_commands(bot: commands.Bot):

    # --------- /warn ---------
    @bot.tree.command(name="warn", description="Warn a member (admin/mod only)")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    @app_commands.default_permissions(kick_members=True)
    async def warn_cmd(interaction: discord.Interaction, member: discord.Member, reason: str):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.dm_title"),
                           ti(interaction, "server.modpro.dm_body")), ephemeral=True)
            return
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.perm_title"),
                           ti(interaction, "server.modpro.perm_kick_members")),
                ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.invalid_target"),
                           ti(interaction, "server.modpro.cannot_warn_bot")), ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.invalid_target"),
                           ti(interaction, "server.modpro.cannot_warn_self")), ephemeral=True)
            return
        if len(reason) > 500:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.reason_too_long"),
                           ti(interaction, "server.modpro.max_500_chars")), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        locale = locale_of(interaction)

        action_id = mod_action_add(
            interaction.guild.id, member.id, "warn",
            reason=reason, moderator_id=interaction.user.id,
        )
        active_count = mod_action_count_active(interaction.guild.id, member.id, "warn")
        action_data  = mod_action_get(action_id) or {}
        embed_log = _build_action_embed(action_data, member=member, moderator=interaction.user,
                                        locale=guild_locale(interaction.guild.id) or DEFAULT_LOCALE)
        embed_log.set_footer(text=t("server.modpro.active_warns_footer",
                                    guild_locale(interaction.guild.id) or DEFAULT_LOCALE,
                                    count=active_count))
        await _post_modlog(interaction.guild, embed_log)

        # DM the member
        try:
            dm_embed = discord.Embed(
                title=t("server.modpro.dm_warn_title", locale, guild=interaction.guild.name),
                description=t("server.modpro.dm_warn_body", locale,
                              reason=reason, count=active_count),
                color=0xF1C40F,
            )
            await member.send(embed=dm_embed)
        except Exception:
            pass

        # Auto-timeout when the threshold is reached
        auto_duration = await _apply_auto_timeout_if_needed(
            member, interaction.user, guild_locale(interaction.guild.id) or DEFAULT_LOCALE)
        msg = t("server.modpro.warn_done", locale,
                member=member.display_name, action_id=action_id, count=active_count)
        if auto_duration:
            mins = auto_duration // 60
            msg += "\n" + t("server.modpro.warn_auto_timeout", locale, minutes=mins)
        await interaction.followup.send(
            embed=_ok(ti(interaction, "server.modpro.warn_saved"), msg), ephemeral=True)

    # --------- /modlogs ---------
    @bot.tree.command(name="modlogs", description="Show the sanction history of a member")
    @app_commands.describe(member="Member to inspect")
    @app_commands.default_permissions(kick_members=True)
    async def modlogs_cmd(interaction: discord.Interaction, member: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.dm_title"),
                           ti(interaction, "server.modpro.dm_body")), ephemeral=True)
            return
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.perm_title"),
                           ti(interaction, "server.modpro.perm_kick_members")),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        locale = locale_of(interaction)
        actions = mod_actions_list(interaction.guild.id, user_id=member.id, limit=20)
        active_warns = sum(1 for a in actions if a["action_type"] == "warn" and not a.get("revoked_at"))
        total = len(actions)
        embed = discord.Embed(
            title=t("server.modpro.modlogs_title", locale, member=member.display_name),
            description=t("server.modpro.modlogs_summary", locale,
                          total=total, active=active_warns),
            color=0x3498DB,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if not actions:
            embed.add_field(name="​", value=t("server.modpro.modlogs_empty", locale), inline=False)
        else:
            lines = []
            for a in actions[:15]:
                tag = "~~" if a.get("revoked_at") else ""
                label = _action_label(a["action_type"], locale)
                date  = _fmt_dt(a.get("created_at"))
                mod   = f"<@{a.get('moderator_id')}>" if a.get("moderator_id") else "?"
                reason = (a.get("reason") or t("server.modpro.no_reason_italic", locale))[:80]
                lines.append(f"{tag}`#{a['id']}` {label} · {date} · "
                             + t("server.modpro.modlogs_by", locale, moderator=mod)
                             + f"\n→ {reason}{tag}")
            embed.add_field(name=t("server.modpro.modlogs_history", locale),
                            value="\n\n".join(lines), inline=False)
        if total > 15:
            embed.set_footer(text=t("server.modpro.modlogs_older", locale, count=total - 15))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # --------- /clearwarns ---------
    @bot.tree.command(name="clearwarns", description="Revoke every active warn of a member")
    @app_commands.describe(member="Member whose warns will be cleared", reason="Reason (optional)")
    @app_commands.default_permissions(manage_guild=True)
    async def clearwarns_cmd(interaction: discord.Interaction, member: discord.Member,
                             reason: Optional[str] = None):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.dm_title"),
                           ti(interaction, "server.modpro.dm_body")), ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.perm_title"),
                           ti(interaction, "server.modpro.perm_manage_guild")),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        locale = locale_of(interaction)
        glocale = guild_locale(interaction.guild.id) or DEFAULT_LOCALE
        active = mod_actions_list(interaction.guild.id, user_id=member.id,
                                  action_types=["warn"], include_revoked=False, limit=200)
        n = 0
        for a in active:
            if mod_action_revoke(a["id"], interaction.user.id, reason or "clearwarns"):
                n += 1
        embed = _ok(
            t("server.modpro.clearwarns_title", locale),
            t("server.modpro.clearwarns_body", locale, count=n, member=member.display_name),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        log = discord.Embed(
            title=t("server.modpro.clearwarns_log_title", glocale, member=member.display_name),
            description=t("server.modpro.clearwarns_log_body", glocale,
                          count=n, moderator=interaction.user.mention,
                          reason=reason or t("server.modpro.no_reason", glocale)),
            color=0x2ECC71,
            timestamp=_dt.datetime.now(_dt.timezone.utc),
        )
        await _post_modlog(interaction.guild, log)

    # --------- /note (internal, does not notify the user) ---------
    @bot.tree.command(name="note", description="Private mod note about a member (the user is not notified)")
    @app_commands.describe(member="Member concerned", text="Content of the note")
    @app_commands.default_permissions(kick_members=True)
    async def note_cmd(interaction: discord.Interaction, member: discord.Member, text: str):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.dm_title"),
                           ti(interaction, "server.modpro.dm_body")), ephemeral=True)
            return
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.perm_title"),
                           ti(interaction, "server.modpro.perm_kick_members")),
                ephemeral=True)
            return
        if len(text) > 1000:
            await interaction.response.send_message(
                embed=_err(ti(interaction, "server.modpro.note_too_long"),
                           ti(interaction, "server.modpro.max_1000_chars")), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        action_id = mod_action_add(
            interaction.guild.id, member.id, "note",
            reason=text, moderator_id=interaction.user.id,
        )
        embed_log = _build_action_embed(mod_action_get(action_id) or {}, member=member,
                                        moderator=interaction.user,
                                        locale=guild_locale(interaction.guild.id) or DEFAULT_LOCALE)
        await _post_modlog(interaction.guild, embed_log)
        await interaction.followup.send(
            embed=_ok(ti(interaction, "server.modpro.note_saved_title"),
                      ti(interaction, "server.modpro.note_saved_body",
                         action_id=action_id, member=member.display_name)),
            ephemeral=True,
        )
