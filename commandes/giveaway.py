"""Giveaways: slash command + persistent view + finalize loop.

Flow:
- /giveaway create duration winners prize [channel]
- the bot posts an embed in the target channel with a 🎉 Enter button
- each click = 1 entry (toggle)
- task loop (every minute) detects finished giveaways
  -> picks random winners, edits the message, pings the winners
- /giveaway reroll <id> and /giveaway cancel <id>

The view is registered with a stable custom_id so it survives restarts.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import random
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    giveaway_create, giveaway_set_message_id, giveaway_get, giveaway_get_by_message,
    giveaways_list, giveaway_entry_add, giveaway_entry_remove, giveaway_entries,
    giveaway_entries_count, giveaway_set_ended, giveaway_cancel,
    giveaways_pending_finalize,
)
from services.i18n import DEFAULT_LOCALE, guild_locale, t, ti


_DURATION_RE = re.compile(r"^\s*(\d+)\s*(s|m|h|d|j)?\s*$", re.IGNORECASE)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "j": 86400}


def _gw_locale(gw: dict) -> str:
    """Locale of the server owning this giveaway (fallback: English)."""
    try:
        return guild_locale(int(gw.get("guild_id"))) or DEFAULT_LOCALE
    except (TypeError, ValueError):
        return DEFAULT_LOCALE


def parse_duration(text: str) -> Optional[int]:
    """Parse '1h', '30m', '2d', '90' (seconds by default). Returns sec or None."""
    if not text:
        return None
    m = _DURATION_RE.match(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or "m").lower()
    return n * _UNITS.get(unit, 60)


def fmt_duration(sec: int) -> str:
    if sec <= 0:
        return t("server.giveaway.duration_over")
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m and not d: parts.append(f"{m}m")
    if s and not d and not h: parts.append(f"{s}s")
    return " ".join(parts) or f"{sec}s"


class GiveawayJoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=t("server.giveaway.btn_join"), style=discord.ButtonStyle.success,
                       custom_id="gw:join")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        gw = giveaway_get_by_message(interaction.message.id) if interaction.message else None
        if not gw:
            try: await interaction.response.send_message(
                ti(interaction, "server.giveaway.not_found"), ephemeral=True)
            except Exception: pass
            return
        if gw.get("ended") or gw.get("cancelled"):
            try: await interaction.response.send_message(
                ti(interaction, "server.giveaway.ended"), ephemeral=True)
            except Exception: pass
            return
        gid = gw["id"]
        uid = interaction.user.id
        # Toggle: already an entrant -> remove
        existing = uid in {int(x) for x in giveaway_entries(gid)}
        if existing:
            giveaway_entry_remove(gid, uid)
            try:
                await interaction.response.send_message(
                    ti(interaction, "server.giveaway.entry_removed"), ephemeral=True)
            except Exception: pass
        else:
            giveaway_entry_add(gid, uid)
            try:
                await interaction.response.send_message(
                    ti(interaction, "server.giveaway.entry_added", prize=gw["prize"]),
                    ephemeral=True)
            except Exception: pass
        # Update count in original embed
        try:
            count = giveaway_entries_count(gid)
            msg = interaction.message
            if msg and msg.embeds:
                emb = msg.embeds[0]
                field_label = t("server.giveaway.field_participants", _gw_locale(gw))
                # Look for the "Participants" field or append it
                new_fields = []
                replaced = False
                for f in emb.fields:
                    if f.name.startswith("👥"):
                        new_fields.append((field_label, str(count), True))
                        replaced = True
                    else:
                        new_fields.append((f.name, f.value, f.inline))
                if not replaced:
                    new_fields.append((field_label, str(count), True))
                emb.clear_fields()
                for n, v, i in new_fields:
                    emb.add_field(name=n, value=v, inline=i)
                await msg.edit(embed=emb, view=self)
        except Exception as e:
            print(f"[giveaway/update count] {type(e).__name__}: {e}")


def make_giveaway_embed(gw: dict, *, participants_count: int = 0,
                       finished: bool = False,
                       winners: Optional[list] = None) -> discord.Embed:
    loc = _gw_locale(gw)
    now = _dt.datetime.now(_dt.timezone.utc)
    ends_at = _dt.datetime.fromisoformat(gw["ends_at"].replace("Z", "+00:00"))
    remaining = max(0, int((ends_at - now).total_seconds()))
    if finished or gw.get("ended"):
        color = 0x95A5A6 if not (winners) else 0x2ECC71
        title = t("server.giveaway.title_finished", loc, prize=gw["prize"])
    else:
        color = 0xF1C40F
        title = t("server.giveaway.title_active", loc, prize=gw["prize"])
    embed = discord.Embed(title=title, color=color, timestamp=now)
    if not finished and not gw.get("ended"):
        embed.description = t(
            "server.giveaway.desc_active", loc,
            prize=gw["prize"], winners=gw["winners_count"],
            ends_ts=int(ends_at.timestamp()),
        )
    else:
        if winners:
            mentions = ", ".join(f"<@{w}>" for w in winners)
            embed.description = t("server.giveaway.desc_winners", loc,
                                  prize=gw["prize"], winners=mentions)
        else:
            embed.description = t("server.giveaway.desc_no_winner", loc, prize=gw["prize"])
    embed.add_field(name=t("server.giveaway.field_participants", loc),
                    value=str(participants_count), inline=True)
    if gw.get("created_by"):
        embed.add_field(name=t("server.giveaway.field_host", loc),
                        value=f"<@{gw['created_by']}>", inline=True)
    return embed


async def _finalize_giveaway(bot: commands.Bot, gw: dict) -> Optional[list[str]]:
    """Draw the winners, edit the message, ping the winners. Returns winner ids."""
    gid = gw["id"]
    loc = _gw_locale(gw)
    entries = giveaway_entries(gid)
    n = int(gw["winners_count"])
    if not entries:
        giveaway_set_ended(gid, [])
        winners = []
    else:
        winners = random.sample(entries, min(n, len(entries)))
        giveaway_set_ended(gid, winners)

    # Update Discord message
    try:
        ch = bot.get_channel(int(gw["channel_id"]))
        if ch and gw.get("message_id"):
            msg = await ch.fetch_message(int(gw["message_id"]))
            embed = make_giveaway_embed(
                {**gw, "ended": 1},
                participants_count=len(entries),
                finished=True,
                winners=winners,
            )
            view = GiveawayJoinView()
            for child in view.children:
                child.disabled = True
            await msg.edit(embed=embed, view=view)
            if winners:
                mentions = " ".join(f"<@{w}>" for w in winners)
                await ch.send(t("server.giveaway.announce_winners", loc,
                                mentions=mentions, prize=gw["prize"]))
            else:
                await ch.send(t("server.giveaway.announce_no_participants", loc,
                                prize=gw["prize"]))
    except discord.NotFound:
        print(f"[giveaway/finalize] message disappeared gw={gid}")
    except Exception as e:
        print(f"[giveaway/finalize] err gw={gid}: {type(e).__name__}: {e}")
    return winners


async def giveaway_finalize_sweep(bot: commands.Bot):
    """Check every giveaway that has reached its end time."""
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    pending = giveaways_pending_finalize(now_iso)
    for gw in pending:
        try:
            await _finalize_giveaway(bot, gw)
        except Exception as e:
            print(f"[giveaway/sweep] err: {type(e).__name__}: {e}")


async def reroll_giveaway(bot: commands.Bot, giveaway_id: int) -> Optional[list[str]]:
    gw = giveaway_get(giveaway_id)
    if not gw:
        return None
    entries = giveaway_entries(giveaway_id)
    if not entries:
        return []
    # Draw among the entrants, excluding the previous winners
    prev = []
    try:
        prev = _json.loads(gw.get("winner_ids") or "[]")
    except Exception:
        prev = []
    pool = [e for e in entries if e not in prev] or entries
    n = int(gw["winners_count"])
    winners = random.sample(pool, min(n, len(pool)))
    giveaway_set_ended(giveaway_id, winners)
    # Post a new message
    try:
        ch = bot.get_channel(int(gw["channel_id"]))
        if ch:
            mentions = " ".join(f"<@{w}>" for w in winners)
            await ch.send(t("server.giveaway.announce_reroll", _gw_locale(gw),
                            giveaway_id=giveaway_id, mentions=mentions, prize=gw["prize"]))
    except Exception as e:
        print(f"[giveaway/reroll] post err: {type(e).__name__}: {e}")
    return winners


def setup_giveaway_commands(bot: commands.Bot):
    # Register the persistent view so it survives restarts
    bot.add_view(GiveawayJoinView())

    gw_group = app_commands.Group(name="giveaway", description="Prize draws")

    @gw_group.command(name="create", description="Create a giveaway")
    @app_commands.describe(
        duration="Duration (e.g. 1h, 30m, 2d). Default: minutes when no unit is given.",
        winners="Number of winners (1-50)",
        prize="Description of the reward",
        channel="Channel to post in (default: current channel)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def gw_create(interaction: discord.Interaction,
                        duration: str,
                        winners: app_commands.Range[int, 1, 50],
                        prize: str,
                        channel: Optional[discord.TextChannel] = None):
        if not interaction.guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.guild_only"), ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.need_manage_guild"), ephemeral=True)
            return
        sec = parse_duration(duration)
        if not sec or sec < 30 or sec > 30 * 86400:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.invalid_duration"), ephemeral=True)
            return
        if len(prize) > 200:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.prize_too_long"), ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.target_must_be_text"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        ends_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=sec)
        gid = giveaway_create(
            interaction.guild.id, target.id, prize, winners,
            ends_at.isoformat(), interaction.user.id,
        )
        gw = giveaway_get(gid)
        embed = make_giveaway_embed(gw, participants_count=0)
        view = GiveawayJoinView()
        try:
            msg = await target.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.followup.send(
                ti(interaction, "server.giveaway.post_forbidden"), ephemeral=True)
            return
        giveaway_set_message_id(gid, msg.id)
        await interaction.followup.send(
            ti(interaction, "server.giveaway.created", giveaway_id=gid,
               channel=target.mention, ends_ts=int(ends_at.timestamp())),
            ephemeral=True,
        )

    @gw_group.command(name="list", description="Show the giveaways currently running")
    @app_commands.default_permissions(manage_guild=True)
    async def gw_list(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.dm_unavailable"), ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.no_perm"), ephemeral=True); return
        rows = giveaways_list(interaction.guild.id, only_active=True, limit=25)
        if not rows:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.none_active"), ephemeral=True); return
        lines = []
        for g in rows:
            entries = giveaway_entries_count(g["id"])
            ends_ts = int(_dt.datetime.fromisoformat(g["ends_at"].replace("Z", "+00:00")).timestamp())
            lines.append(ti(interaction, "server.giveaway.list_line",
                            giveaway_id=g["id"], prize=g["prize"],
                            channel_id=g["channel_id"], entries=entries, ends_ts=ends_ts))
        embed = discord.Embed(title=ti(interaction, "server.giveaway.list_title"),
                              description="\n".join(lines), color=0xF1C40F)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @gw_group.command(name="reroll", description="Draw new winners for a finished giveaway")
    @app_commands.describe(giveaway_id="Giveaway ID (shown in the message title)")
    @app_commands.default_permissions(manage_guild=True)
    async def gw_reroll(interaction: discord.Interaction, giveaway_id: int):
        if not interaction.guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.dm_unavailable"), ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.no_perm"), ephemeral=True); return
        gw = giveaway_get(giveaway_id)
        if not gw or str(gw["guild_id"]) != str(interaction.guild.id):
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.not_found_id", giveaway_id=giveaway_id),
                ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        winners = await reroll_giveaway(bot, giveaway_id)
        if not winners:
            await interaction.followup.send(
                ti(interaction, "server.giveaway.no_eligible_reroll"), ephemeral=True); return
        await interaction.followup.send(
            ti(interaction, "server.giveaway.reroll_done",
               winners=", ".join(f"<@{w}>" for w in winners)),
            ephemeral=True,
        )

    @gw_group.command(name="cancel", description="Cancel a running giveaway")
    @app_commands.describe(giveaway_id="Giveaway ID")
    @app_commands.default_permissions(manage_guild=True)
    async def gw_cancel(interaction: discord.Interaction, giveaway_id: int):
        if not interaction.guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.dm_unavailable"), ephemeral=True); return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.no_perm"), ephemeral=True); return
        gw = giveaway_get(giveaway_id)
        if not gw or str(gw["guild_id"]) != str(interaction.guild.id):
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.not_found_id", giveaway_id=giveaway_id),
                ephemeral=True); return
        if gw.get("ended"):
            await interaction.response.send_message(
                ti(interaction, "server.giveaway.already_ended"), ephemeral=True); return
        giveaway_cancel(giveaway_id)
        # Update message
        try:
            ch = bot.get_channel(int(gw["channel_id"]))
            if ch and gw.get("message_id"):
                msg = await ch.fetch_message(int(gw["message_id"]))
                embed = msg.embeds[0] if msg.embeds else discord.Embed()
                embed.title = t("runtime.giveaway.cancelled_title", _gw_locale(gw),
                                prize=gw["prize"])
                embed.color = 0xE74C3C
                view = GiveawayJoinView()
                for child in view.children:
                    child.disabled = True
                await msg.edit(embed=embed, view=view)
        except Exception:
            pass
        await interaction.response.send_message(
            ti(interaction, "server.giveaway.cancelled", giveaway_id=giveaway_id),
            ephemeral=True)

    bot.tree.add_command(gw_group)
