"""Slash commands /remind, /reminders, /unremind.

Supported durations: `1h`, `30min`, `2d`, `1d2h30min`, `45s`. 30 days max.
"""
from __future__ import annotations

import datetime as _dt
import re
import discord
from discord import app_commands

from database import (
    reminder_add, reminders_list_user, reminder_delete,
)

from services.i18n import ti


_DURATION_RE = re.compile(
    r"(?:(?P<d>\d+)\s*[dj])?\s*"   # 2d / 2j (legacy FR alias)
    r"(?:(?P<h>\d+)\s*h)?\s*"      # 2h
    r"(?:(?P<m>\d+)\s*(?:min|m))?\s*"
    r"(?:(?P<s>\d+)\s*s)?",
    re.IGNORECASE,
)
_MAX_SECONDS = 30 * 24 * 3600  # 30 days


def parse_duration(s: str) -> int:
    """Parse 'XdYhZminWs' -> total seconds. Returns 0 when invalid."""
    if not s:
        return 0
    s = s.strip().lower().replace(" ", "")
    m = _DURATION_RE.fullmatch(s)
    if not m:
        return 0
    parts = m.groupdict(default="0")
    total = (int(parts["d"]) * 86400
             + int(parts["h"]) * 3600
             + int(parts["m"]) * 60
             + int(parts["s"]))
    return total


def _fmt_due(due_dt: _dt.datetime) -> str:
    """Human format: 'June 7, 2026 at 14:32'."""
    return due_dt.strftime("%B %d, %Y at %H:%M")


def _parse_date_input(raw: str) -> _dt.datetime | None:
    """Parse a flexible date-input string.

    Accepts:
    - Relative duration: '1h', '30min', '2d', '1d2h30min'
    - Absolute date: 'DD/MM/YYYY HH:MM' or 'DD/MM HH:MM' or 'YYYY-MM-DD HH:MM'
    - Time of day: 'HH:MM' (read as today, or tomorrow when already past)

    Returns a UTC datetime, or None when invalid.
    """
    if not raw:
        return None
    s = raw.strip()

    # 1) Relative duration?
    sec = parse_duration(s)
    if sec > 0 and sec <= _MAX_SECONDS:
        return _dt.datetime.utcnow() + _dt.timedelta(seconds=sec)

    # 2) Absolute formats (server local time = UTC, we assume UTC).
    formats = [
        "%d/%m/%Y %H:%M", "%d/%m/%Y %Hh%M", "%d/%m/%Y %H",
        "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%d/%m %H:%M", "%d-%m %H:%M",
        "%H:%M",
    ]
    now = _dt.datetime.utcnow()
    for fmt in formats:
        try:
            dt = _dt.datetime.strptime(s, fmt)
            # No year given: use the current one
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
            # Only HH:MM given: today or tomorrow
            if fmt == "%H:%M":
                dt = now.replace(hour=dt.hour, minute=dt.minute,
                                 second=0, microsecond=0)
                if dt <= now:
                    dt += _dt.timedelta(days=1)
            return dt
        except ValueError:
            continue
    return None


def _resolve_channel(raw: str, guild: discord.Guild) -> discord.TextChannel | None:
    """Parse a channel input: '<#123>', '#name', 'name', '123'."""
    if not raw:
        return None
    s = raw.strip()
    # Mention <#123>
    m = re.match(r"<#(\d+)>", s)
    if m:
        return guild.get_channel(int(m.group(1)))
    # Raw ID
    if s.isdigit():
        return guild.get_channel(int(s))
    # Name (with or without #)
    name = s.lstrip("#").lower()
    for ch in guild.text_channels:
        if ch.name.lower() == name:
            return ch
    return None


def setup_remind_commands(bot, deps):
    globals().update(deps)

    class RemindModal(discord.ui.Modal, title="Create a reminder"):
        date_input = discord.ui.TextInput(
            label="Date / Duration",
            placeholder="e.g. 1h30min  •  15/06/2026 14:30  •  18:00",
            max_length=60,
            required=True,
        )
        message_input = discord.ui.TextInput(
            label="Reminder message",
            style=discord.TextStyle.paragraph,
            placeholder="What the bot will post when the reminder fires",
            max_length=500,
            required=True,
        )
        channel_input = discord.ui.TextInput(
            label="Channel (#channel, name, or ID) — empty = current",
            placeholder="e.g. #general OR 1234567890",
            max_length=100,
            required=False,
        )

        async def on_submit(self, interaction: discord.Interaction):
            # 1) Parse the date
            due_dt = _parse_date_input(str(self.date_input.value))
            if due_dt is None:
                await interaction.response.send_message(
                    ti(interaction, "utils.remind.invalid_date"),
                    ephemeral=True,
                )
                return
            now = _dt.datetime.utcnow()
            if due_dt <= now:
                await interaction.response.send_message(
                    ti(interaction, "utils.remind.past_date"), ephemeral=True,
                )
                return
            if (due_dt - now).total_seconds() > _MAX_SECONDS:
                await interaction.response.send_message(
                    ti(interaction, "utils.remind.too_far"), ephemeral=True,
                )
                return

            # 2) Channel: from the input, or the current one
            ch_raw = str(self.channel_input.value or "").strip()
            if ch_raw:
                ch = _resolve_channel(ch_raw, interaction.guild)
                if ch is None:
                    await interaction.response.send_message(
                        ti(interaction, "utils.remind.channel_not_found", raw=ch_raw),
                        ephemeral=True,
                    )
                    return
                if not isinstance(ch, (discord.TextChannel, discord.Thread)):
                    await interaction.response.send_message(
                        ti(interaction, "utils.remind.channel_not_text"), ephemeral=True,
                    )
                    return
            else:
                ch = interaction.channel

            # Check the send permission
            try:
                me = interaction.guild.me
                if not ch.permissions_for(me).send_messages:
                    await interaction.response.send_message(
                        ti(interaction, "utils.remind.no_send_perm", channel=ch.mention),
                        ephemeral=True,
                    )
                    return
            except Exception:
                pass

            # 3) Create the reminder
            text = str(self.message_input.value).strip()[:500]
            due_iso = due_dt.strftime("%Y-%m-%d %H:%M:%S")
            rid = reminder_add(
                interaction.guild.id, interaction.user.id, ch.id, text, due_iso,
            )
            unix = int((due_dt - _dt.datetime(1970, 1, 1)).total_seconds())
            await interaction.response.send_message(
                ti(interaction, "utils.remind.created", id=rid, channel=ch.mention,
                   unix=unix, text=text[:200]),
                ephemeral=True,
            )

    @bot.tree.command(name="remind", description="Create a reminder through a builder")
    async def remind(interaction: discord.Interaction):
        await interaction.response.send_modal(RemindModal())


    @bot.tree.command(name="reminders", description="List your pending reminders")
    async def reminders_list_cmd(interaction: discord.Interaction):
        rows = reminders_list_user(interaction.user.id, include_fired=False, limit=20)
        if not rows:
            await interaction.response.send_message(
                ti(interaction, "utils.remind.list_empty"),
                ephemeral=True,
            )
            return
        lines = []
        for r in rows:
            try:
                due_dt = _dt.datetime.strptime(r["due_at"], "%Y-%m-%d %H:%M:%S")
                unix = int((due_dt - _dt.datetime(1970, 1, 1)).total_seconds())
                lines.append(ti(interaction, "utils.remind.list_line",
                                id=r["id"], unix=unix, text=r["text"][:80]))
            except Exception:
                lines.append(ti(interaction, "utils.remind.list_line_raw",
                                id=r["id"], due=r["due_at"], text=r["text"][:80]))
        await interaction.response.send_message(
            ti(interaction, "utils.remind.list_header") + "\n" + "\n".join(lines)
            + ti(interaction, "utils.remind.list_footer"),
            ephemeral=True,
        )


    @bot.tree.command(name="unremind", description="Delete one of your reminders")
    @app_commands.describe(id="Reminder number (see /reminders)")
    async def unremind(interaction: discord.Interaction, id: int):
        ok = reminder_delete(id, interaction.user.id)
        if ok:
            await interaction.response.send_message(
                ti(interaction, "utils.remind.deleted", id=id), ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                ti(interaction, "utils.remind.delete_failed", id=id), ephemeral=True,
            )
