"""Tempvoice: temporary voice channels.

Concept:
- The admin configures a "lobby" through /tempvoice setup. It is a voice
  channel users join to trigger the creation of their own voice channel.
- When a user joins the lobby, the bot creates a voice channel named after
  them in the configured category and moves them into it automatically.
- The channel is deleted as soon as it becomes empty (no member left).
- The owner of the temp channel can configure it through /voc (rename, limit,
  lock, kick, transfer).

Storage: tempvoice_config (per-guild config) + tempvoice_active (tracks the
created channels to identify the owner + cleanup at boot).

The on_voice_state_update event is centralised in bot.py. This module exposes
`tempvoice_on_voice_state_update(...)` which must be called by the central
handler.
"""
from __future__ import annotations

import asyncio
import discord
from discord import app_commands

from database import (
    tempvoice_config_get, tempvoice_config_set, tempvoice_config_disable,
    tempvoice_track, tempvoice_untrack, tempvoice_owner_of, tempvoice_transfer,
    tempvoice_list_active,
)
from services.i18n import DEFAULT_LOCALE, guild_locale, t, ti


_CREATION_LOCK: dict[int, asyncio.Lock] = {}


def _format_default_name(template: str, user: discord.Member, locale: str = DEFAULT_LOCALE) -> str:
    name = (template or t("server.tempvoice.default_name", locale)).replace("{user}", user.display_name)
    return name[:90]  # Discord limit = 100


async def _create_temp_voice(bot, member: discord.Member, cfg: dict):
    gid = member.guild.id
    locale = guild_locale(gid) or DEFAULT_LOCALE
    lock = _CREATION_LOCK.setdefault(gid, asyncio.Lock())
    async with lock:
        # Target category: config OR the same one as the lobby
        category = None
        if cfg.get("category_id"):
            category = member.guild.get_channel(int(cfg["category_id"]))
            if not isinstance(category, discord.CategoryChannel):
                category = None
        if category is None:
            lobby = member.guild.get_channel(int(cfg["lobby_channel_id"]))
            category = lobby.category if lobby else None

        name = _format_default_name(cfg.get("default_name"), member, locale)
        # Permissions: the owner can do anything in their own channel
        overwrites = {
            member.guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            member: discord.PermissionOverwrite(
                manage_channels=True, move_members=True, mute_members=True,
                deafen_members=True, view_channel=True, connect=True, speak=True,
            ),
        }
        try:
            ch = await member.guild.create_voice_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                reason=f"Tempvoice for {member}",
            )
            tempvoice_track(ch.id, gid, member.id)
            try:
                await member.move_to(ch, reason="Tempvoice")
            except Exception as e:
                print(f"[tempvoice] move_to err: {type(e).__name__}: {e}")
        except discord.Forbidden:
            print(f"[tempvoice] create channel forbidden guild={gid}")
        except Exception as e:
            print(f"[tempvoice] create channel err: {type(e).__name__}: {e}")


async def tempvoice_on_voice_state_update(member: discord.Member,
                                          before: discord.VoiceState,
                                          after: discord.VoiceState,
                                          bot):
    """To be called from the central on_voice_state_update of bot.py.
    Handles: creation when joining the lobby + cleanup when a temp channel is
    empty + automatic ownership transfer when the owner leaves other members
    behind."""
    if member.bot:
        return
    try:
        # Case 1: user joins a channel -> is it the lobby?
        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            cfg = tempvoice_config_get(member.guild.id)
            if cfg and str(after.channel.id) == str(cfg["lobby_channel_id"]):
                await _create_temp_voice(bot, member, cfg)

        # Case 2: user leaves a channel -> if it was a temp one and it is empty, delete it
        if before.channel and (not after.channel or before.channel.id != after.channel.id):
            owner = tempvoice_owner_of(before.channel.id)
            if owner is not None:
                if len(before.channel.members) == 0:
                    try:
                        await before.channel.delete(reason="Empty tempvoice channel")
                    except Exception as e:
                        print(f"[tempvoice] delete empty err: {type(e).__name__}: {e}")
                    finally:
                        tempvoice_untrack(before.channel.id)
                elif str(owner) == str(member.id):
                    # The owner leaves but others stay: transfer to a present member
                    successor = next((m for m in before.channel.members if not m.bot), None)
                    if successor:
                        tempvoice_transfer(before.channel.id, successor.id)
                        try:
                            await before.channel.send(
                                t("server.tempvoice.owner_left",
                                  guild_locale(member.guild.id) or DEFAULT_LOCALE,
                                  member=successor.display_name)
                            )
                        except Exception:
                            pass
    except Exception as e:
        print(f"[tempvoice] on_voice_state_update err: {type(e).__name__}: {e}")


def setup_tempvoice(bot, deps):
    globals().update(deps)

    # ===== Admin slash commands: /tempvoice setup/disable/info =====
    tempvoice_grp = app_commands.Group(name="tempvoice",
                                       description="Configure the temporary voice channels (admin)")

    @tempvoice_grp.command(name="setup", description="Configure the lobby channel (admin)")
    @app_commands.describe(
        lobby="Voice channel to join to trigger the creation",
        category="Category where the created channels go (default: same as the lobby)",
        default_name="Template for the name (use {user} for the nickname)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def tempvoice_setup(interaction: discord.Interaction,
                               lobby: discord.VoiceChannel,
                               category: discord.CategoryChannel = None,
                               default_name: str = None):
        tempvoice_config_set(
            interaction.guild.id,
            lobby_channel_id=lobby.id,
            category_id=category.id if category else None,
            default_name=default_name,
        )
        category_txt = (ti(interaction, "server.tempvoice.setup_category", category=category.name)
                        if category else ti(interaction, "server.tempvoice.setup_category_lobby"))
        await interaction.response.send_message(
            ti(interaction, "server.tempvoice.setup_done",
               lobby=lobby.id, category=category_txt,
               template=default_name or ti(interaction, "server.tempvoice.default_name")),
            ephemeral=True,
        )

    @tempvoice_grp.command(name="disable", description="Disable the temporary voice channels (admin)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def tempvoice_disable(interaction: discord.Interaction):
        tempvoice_config_disable(interaction.guild.id)
        await interaction.response.send_message(
            ti(interaction, "server.tempvoice.disabled"), ephemeral=True)

    @tempvoice_grp.command(name="info", description="Show the tempvoice configuration")
    async def tempvoice_info(interaction: discord.Interaction):
        cfg = tempvoice_config_get(interaction.guild.id)
        if not cfg:
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.not_configured"),
                ephemeral=True,
            )
            return
        active = tempvoice_list_active(interaction.guild.id)
        await interaction.response.send_message(
            ti(interaction, "server.tempvoice.info",
               lobby=cfg["lobby_channel_id"],
               category=('<#' + cfg['category_id'] + '>' if cfg.get("category_id")
                         else ti(interaction, "server.tempvoice.same_as_lobby")),
               template=cfg["default_name"],
               active=len(active)),
            ephemeral=True,
        )

    bot.tree.add_command(tempvoice_grp)


    # ===== User slash commands: /voc rename/limit/lock/unlock/kick/transfer =====
    voc_grp = app_commands.Group(name="voc",
                                  description="Manage your temporary voice channel")

    async def _require_owner(interaction: discord.Interaction):
        """Returns the temp channel when the user owns it, otherwise replies + None."""
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.must_be_in_channel"),
                ephemeral=True,
            )
            return None
        ch = interaction.user.voice.channel
        owner = tempvoice_owner_of(ch.id)
        if owner is None:
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.not_a_temp_channel"), ephemeral=True,
            )
            return None
        if str(owner) != str(interaction.user.id):
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.owner_only"), ephemeral=True,
            )
            return None
        return ch

    @voc_grp.command(name="rename", description="Rename your temp channel")
    @app_commands.describe(name="New name (max 90 chars)")
    async def voc_rename(interaction: discord.Interaction, name: str):
        ch = await _require_owner(interaction)
        if not ch: return
        try:
            await ch.edit(name=name[:90], reason=f"Renamed by {interaction.user}")
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.renamed", name=name[:90]), ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] rename err: {e!r}")
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.rename_failed"), ephemeral=True)

    @voc_grp.command(name="limit", description="Limit the number of members (0 = unlimited)")
    @app_commands.describe(limit="Max number of members (0 to 99)")
    async def voc_limit(interaction: discord.Interaction, limit: int):
        ch = await _require_owner(interaction)
        if not ch: return
        limit = max(0, min(99, limit))
        try:
            await ch.edit(user_limit=limit, reason=f"Limit set by {interaction.user}")
            label = ti(interaction, "server.tempvoice.unlimited") if limit == 0 else str(limit)
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.limit_set", limit=label), ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] limit err: {e!r}")
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.limit_failed"), ephemeral=True)

    @voc_grp.command(name="lock", description="Prevent new members from joining")
    async def voc_lock(interaction: discord.Interaction):
        ch = await _require_owner(interaction)
        if not ch: return
        try:
            await ch.set_permissions(interaction.guild.default_role, connect=False)
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.locked"), ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] lock err: {e!r}")
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.lock_failed"), ephemeral=True)

    @voc_grp.command(name="unlock", description="Re-open your channel to everyone")
    async def voc_unlock(interaction: discord.Interaction):
        ch = await _require_owner(interaction)
        if not ch: return
        try:
            await ch.set_permissions(interaction.guild.default_role, connect=None)
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.unlocked"), ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] unlock err: {e!r}")
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.unlock_failed"), ephemeral=True)

    @voc_grp.command(name="kick", description="Kick a member from your channel")
    @app_commands.describe(member="Member to kick")
    async def voc_kick(interaction: discord.Interaction, member: discord.Member):
        ch = await _require_owner(interaction)
        if not ch: return
        if member.voice is None or member.voice.channel is None or member.voice.channel.id != ch.id:
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.member_not_here"), ephemeral=True)
            return
        try:
            await member.move_to(None, reason=f"Kicked by {interaction.user} (tempvoice)")
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.kicked", member=member.mention), ephemeral=True)
        except Exception as e:
            print(f"[tempvoice] kick err: {e!r}")
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.kick_failed"), ephemeral=True)

    @voc_grp.command(name="transfer", description="Give the ownership of the channel to another member")
    @app_commands.describe(member="New owner (must be in the channel)")
    async def voc_transfer(interaction: discord.Interaction, member: discord.Member):
        ch = await _require_owner(interaction)
        if not ch: return
        if member.bot:
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.no_bot"), ephemeral=True); return
        if member.voice is None or member.voice.channel is None or member.voice.channel.id != ch.id:
            await interaction.response.send_message(
                ti(interaction, "server.tempvoice.new_owner_not_here"), ephemeral=True)
            return
        tempvoice_transfer(ch.id, member.id)
        # Update permissions: the new owner can manage, the previous one goes back to default
        try:
            await ch.set_permissions(interaction.user, overwrite=None)
            await ch.set_permissions(member, manage_channels=True, move_members=True,
                                      mute_members=True, deafen_members=True,
                                      view_channel=True, connect=True, speak=True)
        except Exception:
            pass
        await interaction.response.send_message(
            ti(interaction, "server.tempvoice.transferred", member=member.mention), ephemeral=True,
        )

    bot.tree.add_command(voc_grp)
