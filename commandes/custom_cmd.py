"""Custom commands: dynamic per-guild registration.

Each custom command becomes a real slash command `/<name>` scoped to the
server (guild-scoped). The builder lives on the dashboard (/custom-commands).

Variables supported in the text or in the embed fields:
  {user}        -> @mention of the user
  {user.name}   -> display name
  {user.id}     -> ID
  {server}      -> server name
  {channel}     -> #channel mention
  {date}        -> current date (YYYY-MM-DD)
  {time}        -> time HH:MM
"""
from __future__ import annotations

import datetime as _dt
import json as _json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import os as _os

from database import (
    custom_cmds_list, custom_cmd_get, custom_cmd_increment_uses,
    has_premium_grant, user_has_active_entitlement,
)
from services.i18n import ti


_SKU_TOOKBOT_PLUS  = _os.getenv("SKU_TOOKBOT_PLUS", "").strip() or None
_DISCORD_OWNER_ID  = _os.getenv("DISCORD_OWNER_ID", "").strip() or None


def _owner_has_tookbot_plus(guild) -> bool:
    """True when the server owner has TookBot+ (manual grant OR SKU OR global owner)."""
    if not guild:
        return False
    owner_id = getattr(guild, "owner_id", None)
    if not owner_id:
        return False
    oid = str(owner_id)
    if has_premium_grant(oid, feature="tookbot_plus", inherit_all=False):
        return True
    if _SKU_TOOKBOT_PLUS and user_has_active_entitlement(oid, sku_id=_SKU_TOOKBOT_PLUS):
        return True
    if _DISCORD_OWNER_ID and oid == _DISCORD_OWNER_ID:
        return True
    return False


def _interpolate(s: str, *, user: discord.abc.User, guild: discord.Guild,
                 channel: discord.abc.GuildChannel) -> str:
    if not s:
        return s
    now = _dt.datetime.now()
    return (s
            .replace("{user.mention}", getattr(user, "mention", "?"))
            .replace("{user.name}",    getattr(user, "display_name", "?"))
            .replace("{user.id}",      str(user.id))
            .replace("{user}",         getattr(user, "mention", "?"))
            .replace("{server}",       guild.name if guild else "?")
            .replace("{channel}",      getattr(channel, "mention", "?"))
            .replace("{date}",         now.strftime("%Y-%m-%d"))
            .replace("{time}",         now.strftime("%H:%M")))


def _build_embed_from_json(raw: str, *, user, guild, channel) -> Optional[discord.Embed]:
    try:
        data = _json.loads(raw or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    title = _interpolate(data.get("title") or "", user=user, guild=guild, channel=channel)
    desc  = _interpolate(data.get("description") or "", user=user, guild=guild, channel=channel)
    color_raw = data.get("color")
    try:
        color_int = int(color_raw, 16) if isinstance(color_raw, str) else int(color_raw or 0x3498DB)
    except Exception:
        color_int = 0x3498DB
    embed = discord.Embed(title=title or None, description=desc or None, color=color_int)
    if data.get("image"):
        embed.set_image(url=data["image"])
    if data.get("thumbnail"):
        embed.set_thumbnail(url=data["thumbnail"])
    if data.get("footer"):
        embed.set_footer(text=_interpolate(data["footer"], user=user, guild=guild, channel=channel))
    for f in (data.get("fields") or []):
        if not isinstance(f, dict):
            continue
        embed.add_field(
            name=_interpolate(f.get("name") or "​", user=user, guild=guild, channel=channel)[:256],
            value=_interpolate(f.get("value") or "​", user=user, guild=guild, channel=channel)[:1024],
            inline=bool(f.get("inline", False)),
        )
    return embed


async def _execute_custom(interaction: discord.Interaction, name: str):
    """Shared execution logic: fetch the command from the DB and answer."""
    if not interaction.guild:
        await interaction.response.send_message(
            ti(interaction, "server.custom_cmd.dm_unavailable"), ephemeral=True)
        return
    # TookBot+ gate: custom commands are paid. The server owner must have TookBot+.
    if not _owner_has_tookbot_plus(interaction.guild):
        embed = discord.Embed(
            title=ti(interaction, "server.custom_cmd.plus_title"),
            description=ti(interaction, "server.custom_cmd.plus_body", name=name),
            color=0xffa726,
        )
        embed.set_footer(text=ti(interaction, "server.custom_cmd.plus_footer"))
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    row = custom_cmd_get(interaction.guild.id, name)
    if not row or not row.get("enabled"):
        await interaction.response.send_message(
            ti(interaction, "server.custom_cmd.not_found", name=name),
            ephemeral=True)
        return
    try:
        if row.get("use_embed"):
            embed = _build_embed_from_json(
                row.get("response_embed") or "{}",
                user=interaction.user, guild=interaction.guild,
                channel=interaction.channel,
            )
            if not embed:
                raise ValueError("invalid embed")
            await interaction.response.send_message(embed=embed)
        else:
            text = _interpolate(
                row.get("response_text") or "",
                user=interaction.user, guild=interaction.guild,
                channel=interaction.channel,
            )
            if not text.strip():
                text = ti(interaction, "server.custom_cmd.empty_response")
            await interaction.response.send_message(text)
        custom_cmd_increment_uses(row["id"])
    except Exception as e:
        print(f"[custom_cmd] exec err {name}: {type(e).__name__}: {e}")
        try:
            await interaction.response.send_message(
                ti(interaction, "server.custom_cmd.exec_failed"),
                ephemeral=True)
        except Exception:
            pass


def _make_callback(name: str):
    """Builds an async callback that executes the custom command 'name'."""
    async def _cb(interaction: discord.Interaction):
        await _execute_custom(interaction, name)
    return _cb


def _build_command(name: str, description: Optional[str]) -> app_commands.Command:
    desc = (description or "Custom server command")[:100]
    return app_commands.Command(
        name=name,
        description=desc,
        callback=_make_callback(name),
    )


async def sync_custom_commands_for_guild(bot: commands.Bot, guild_id) -> int:
    """Reload the custom slash commands of a server (used at boot + after a
    save/delete from the dashboard). Returns the number of registered commands."""
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return 0
    try:
        bot.tree.clear_commands(guild=guild)
    except Exception as e:
        print(f"[custom_cmd] clear {guild_id} fail : {e}")
    rows = custom_cmds_list(guild.id, enabled_only=True)
    added = 0
    for r in rows:
        try:
            cmd = _build_command(r["name"], r.get("description"))
            bot.tree.add_command(cmd, guild=guild, override=True)
            added += 1
        except Exception as e:
            print(f"[custom_cmd] add err {r['name']} ({guild_id}): {e}")
    try:
        await bot.tree.sync(guild=guild)
    except Exception as e:
        print(f"[custom_cmd] sync {guild_id} fail : {e}")
    return added


def setup_custom_cmd_commands(bot: commands.Bot):
    """Kept for compatibility. No global `/cmd` command is registered anymore:
    custom commands are added dynamically per guild through
    sync_custom_commands_for_guild() at bot startup and after every change made
    from the dashboard."""
    return
