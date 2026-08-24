import asyncio
import collections as _col
import datetime as _dt
import os
import re as _re
import time as _time

import discord
from discord import app_commands
from discord.ext import tasks

from services.i18n import guild_locale, t, ti


# XP anti-spam cooldown: (guild_id, user_id) -> ts of the last gain
_XP_LAST_GAIN: dict = {}

# Per-channel AI conversation memory: channel_id -> {"history": [...], "ts": epoch}
# Auto reset after 1h without a message (keeps token usage down).
_AI_MEMORY: dict = {}
_AI_MEMORY_TTL = 3600        # 1h of inactivity => reset
_AI_MEMORY_MAX_MSGS = 12     # keep the last 12 messages (6 exchanges)


def setup_runtime(bot, deps):
    globals().update(deps)

    def _resolve_setup_channel(guild, key: str):
        """Return the channel configured through /setup for `key` (welcome/logs/alerts/admin).

        Fallback: if not configured OR the channel is missing OR not writable,
        return the first writable text channel of the server (system_channel
        first, otherwise the first writable text_channel). None if nothing available.
        """
        if not guild:
            return None
        me = guild.me
        cid = guild_setting_get(guild.id, f"setup_{key}_channel_id", "")
        if cid:
            try:
                ch = guild.get_channel(int(cid))
            except (TypeError, ValueError):
                ch = None
            if ch and me and ch.permissions_for(me).send_messages:
                return ch
        # Fallback: system_channel then the first writable text_channel
        if guild.system_channel and me and guild.system_channel.permissions_for(me).send_messages:
            return guild.system_channel
        for ch in guild.text_channels:
            if me and ch.permissions_for(me).send_messages:
                return ch
        return None

    @bot.event
    async def on_ready():
        print(f"✅ Bot connected as {bot.user}")
        # Re-register persistent views AFTER connecting (reliable timing:
        # makes sure old ticket messages are caught without re-posting)
        try:
            if hasattr(bot, "_register_ticket_views"):
                bot._register_ticket_views()
        except Exception as e:
            print(f"[ticket] re-register views on_ready: {e!r}")
        # Register every guild the bot is in + sync its channels
        for guild in bot.guilds:
            upsert_guild(
                guild.id, guild.name,
                icon_url=str(guild.icon.url) if guild.icon else None,
                member_count=guild.member_count or 0,
                owner_id=guild.owner_id,
            )
            try:
                _sync_guild_channels(guild)
            except Exception as e:
                print(f"[channels] sync {guild.name} failed: {e}")
            try:
                _sync_guild_roles(guild)
            except Exception as e:
                print(f"[roles] sync {guild.name} failed: {e}")
            try:
                _sync_guild_members(guild)
            except Exception as e:
                print(f"[members] sync {guild.name} failed: {e}")
        print(f"👀 {len(USER_REACTIONS)} reaction(s) loaded across {len(bot.guilds)} server(s)")
        if not reload_reactions.is_running():
            reload_reactions.start()
        if not process_bot_commands.is_running():
            process_bot_commands.start()
        if not daily_logs_purge.is_running():
            daily_logs_purge.start()
        if not pass_rotation_loop.is_running():
            pass_rotation_loop.start()
        if not social_alerts_poll.is_running():
            social_alerts_poll.start()
        if not anti_spam_cleanup.is_running():
            anti_spam_cleanup.start()
        if not status_writer.is_running():
            status_writer.start()
        if not rotate_presence.is_running():
            rotate_presence.start()
        if not voice_idle_disconnect.is_running():
            voice_idle_disconnect.start()
        if not tookbot_plus_expiry_cleanup.is_running():
            tookbot_plus_expiry_cleanup.start()
        if not reminders_dispatch.is_running():
            reminders_dispatch.start()
        if not topgg_stats_poster.is_running():
            topgg_stats_poster.start()
        if not card_event_drop_loop.is_running():
            card_event_drop_loop.start()
        if not card_render_bake_loop.is_running():
            card_render_bake_loop.start()
        if not auto_boss_loop.is_running():
            auto_boss_loop.start()
        # Resume orphan boss fights (asyncio task killed by the restart)
        try:
            from services.card_boss import resume_active_bosses
            await resume_active_bosses(bot)
        except Exception as e:
            print(f"[boss] resume err: {e!r}")
        # CS2 queue sweep (safety net if on_voice_state_update misses an event)
        cs2_loop = globals().get("cs2_queue_sweep_loop")
        if cs2_loop is not None and not cs2_loop.is_running():
            cs2_loop.start()
        # Giveaway finalize loop
        gw_loop = globals().get("giveaway_finalize_loop")
        if gw_loop is not None and not gw_loop.is_running():
            gw_loop.start()
        # Resume music: disabled by default. Discord voice handshakes can stall the
        # gateway at boot if the saved channel state is stale.
        if MUSIC_RESUME and os.getenv("MUSIC_RESUME_ON_BOOT", "0") == "1":
            await MUSIC_RESUME()
        # Sync existing entitlements (purchases made before the bot came online)
        try:
            count = 0
            async for ent in bot.entitlements(exclude_ended=True):
                upsert_entitlement(_entitlement_to_dict(ent))
                count += 1
            print(f"[entitlement] boot sync: {count} active entitlement(s) loaded")
        except Exception as e:
            print(f"[entitlement] boot sync error: {e!r}")
        # Purge orphan per-guild commands (duplicates are possible if an older
        # version synced global + per-guild without copy_global_to, which made
        # every handler fire twice).
        for guild in bot.guilds:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
            except Exception as e:
                print(f"[sync] clear guild {guild.name} failed: {e}")
        await bot.tree.sync()
        print("✅ Slash commands synced globally")

        # Register custom commands (per-guild slash commands) AFTER the global
        # sync, otherwise the clear above wipes them.
        from commandes.custom_cmd import sync_custom_commands_for_guild
        for guild in bot.guilds:
            try:
                n = await sync_custom_commands_for_guild(bot, guild.id)
                if n:
                    print(f"[custom_cmd] {guild.name}: {n} custom command(s) registered")
            except Exception as e:
                print(f"[custom_cmd] boot sync {guild.name} failed: {e}")

    def _sync_guild_roles(guild):
        """Push a guild's roles into the guild_roles table (cache used by the
        dashboard pickers)."""
        rows = []
        for r in guild.roles:
            rows.append({
                "role_id":     r.id,
                "name":        r.name,
                "color":       r.color.value if r.color else 0,
                "position":    r.position,
                "managed":     r.managed,
                "is_everyone": r.is_default(),
            })
        replace_guild_roles(guild.id, rows)


    def _sync_guild_members(guild):
        from database import member_roles_set
        members = []
        for m in guild.members:
            members.append({
                "user_id":    m.id,
                "username":   str(m),
                "avatar_url": str(m.display_avatar.url) if m.display_avatar else None,
                "is_bot":     m.bot,
                "joined_at":  m.joined_at,
            })
        replace_guild_members(guild.id, members)
        # Sync each member's roles (without @everyone)
        for m in guild.members:
            try:
                role_ids = [str(r.id) for r in m.roles if r.name != "@everyone"]
                member_roles_set(guild.id, m.id, role_ids)
            except Exception:
                pass

    def _sync_guild_channels(guild):
        """Push a guild's channels into the guild_channels table."""
        rows = []
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                ctype = "category"
            elif isinstance(ch, discord.VoiceChannel):
                ctype = "voice"
            elif isinstance(ch, discord.StageChannel):
                ctype = "stage"
            elif isinstance(ch, (discord.TextChannel,)):
                ctype = "text"
            else:
                # forum, news, etc.
                ctype = getattr(ch, "type", "text")
                ctype = str(ctype) if ctype else "text"
            rows.append({
                "channel_id": ch.id,
                "name":       ch.name,
                "type":       ctype,
                "position":   getattr(ch, "position", 0) or 0,
            })
        replace_guild_channels(guild.id, rows)

    @bot.event
    async def on_guild_join(guild):
        upsert_guild(guild.id, guild.name,
                     icon_url=str(guild.icon.url) if guild.icon else None,
                     member_count=guild.member_count or 0,
                     owner_id=guild.owner_id)
        _sync_guild_channels(guild)
        # Global sync only (a per-guild sync without copy_global_to created a
        # duplicate command set -> handlers fired twice)
        try:
            await bot.tree.sync()
        except Exception:
            pass

        # ===== ONBOARDING DM: 1 consolidated message to the inviter + 1 to the owner =====
        # June 2026 rework: less wall-of-text, stronger visual hierarchy,
        # direct URL buttons to dashboard / commands / support.
        DASHBOARD_URL = "https://dashboard.tookbot.click"
        LANDING_URL   = "https://tookbot.click"
        SUPPORT_URL   = "https://discord.gg/hx4KEFSGJA"

        # Look up the inviter through the audit log (fallback: owner)
        inviter = None
        try:
            async for entry in guild.audit_logs(limit=8, action=discord.AuditLogAction.bot_add):
                if entry.target and entry.target.id == bot.user.id:
                    inviter = entry.user
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass
        if inviter is None:
            inviter = guild.owner

        lang = guild_locale(guild.id) or "en"
        dash_label = DASHBOARD_URL.replace("https://", "")

        def _build_invite_view():
            v = discord.ui.View(timeout=None)
            v.add_item(discord.ui.Button(label=t("runtime.onboarding.btn_dashboard", lang), style=discord.ButtonStyle.link, url=DASHBOARD_URL, emoji="🎛️"))
            v.add_item(discord.ui.Button(label=t("runtime.onboarding.btn_commands", lang), style=discord.ButtonStyle.link, url=f"{LANDING_URL}/commandes.html", emoji="📚"))
            v.add_item(discord.ui.Button(label=t("runtime.onboarding.btn_support", lang), style=discord.ButtonStyle.link, url=SUPPORT_URL, emoji="💬"))
            return v

        if inviter is not None and not inviter.bot:
            embed = discord.Embed(
                title=t("runtime.onboarding.inviter_title", lang, guild=guild.name),
                description=t("runtime.onboarding.inviter_desc", lang,
                              user=inviter.display_name),
                color=0xB9F23A,
            )
            embed.add_field(
                name=t("runtime.onboarding.step1_name", lang),
                value=t("runtime.onboarding.step1_value", lang),
                inline=False,
            )
            embed.add_field(
                name=t("runtime.onboarding.step2_name", lang),
                value=t("runtime.onboarding.step2_value", lang,
                        dashboard_label=dash_label, dashboard_url=DASHBOARD_URL),
                inline=False,
            )
            embed.add_field(
                name=t("runtime.onboarding.step3_name", lang),
                value=t("runtime.onboarding.step3_value", lang),
                inline=False,
            )
            embed.add_field(
                name=t("runtime.onboarding.tip_name", lang),
                value=t("runtime.onboarding.tip_value", lang),
                inline=False,
            )
            embed.set_footer(text=t("runtime.onboarding.inviter_footer", lang,
                                    guild=guild.name, members=guild.member_count or 0))
            if guild.icon:
                embed.set_thumbnail(url=str(guild.icon.url))

            try:
                await inviter.send(embed=embed, view=_build_invite_view())
            except (discord.Forbidden, discord.HTTPException):
                # Fallback: post in system_channel or the first writable channel
                try:
                    target = None
                    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                        target = guild.system_channel
                    else:
                        for ch in guild.text_channels:
                            if ch.permissions_for(guild.me).send_messages:
                                target = ch
                                break
                    if target:
                        await target.send(embed=embed, view=_build_invite_view())
                except Exception:
                    pass

        # OWNER DM (when different from the inviter): focus on moderator permissions
        server_owner = guild.owner
        if server_owner and not server_owner.bot and (
            inviter is None or server_owner.id != inviter.id
        ):
            embed_owner = discord.Embed(
                title=t("runtime.onboarding.owner_title", lang),
                description=t("runtime.onboarding.owner_desc", lang,
                              user=server_owner.display_name, guild=guild.name),
                color=0xB9F23A,
            )
            embed_owner.add_field(
                name=t("runtime.onboarding.owner_warn_name", lang),
                value=t("runtime.onboarding.owner_warn_value", lang),
                inline=False,
            )
            embed_owner.add_field(
                name=t("runtime.onboarding.owner_how_name", lang),
                value=t("runtime.onboarding.owner_how_value", lang,
                        dashboard_label=dash_label, dashboard_url=DASHBOARD_URL),
                inline=False,
            )
            embed_owner.set_footer(text=t("runtime.onboarding.owner_footer", lang))

            owner_view = discord.ui.View(timeout=None)
            owner_view.add_item(discord.ui.Button(label=t("runtime.onboarding.btn_configure_dashboard", lang), style=discord.ButtonStyle.link, url=DASHBOARD_URL, emoji="🎛️"))

            try:
                await server_owner.send(embed=embed_owner, view=owner_view)
            except (discord.Forbidden, discord.HTTPException):
                pass

    @bot.event
    async def on_guild_remove(guild):
        mark_guild_left(guild.id)

    @bot.event
    async def on_guild_channel_create(channel):
        if not channel.guild: return
        ctype = "voice" if isinstance(channel, discord.VoiceChannel) else \
                "category" if isinstance(channel, discord.CategoryChannel) else \
                "text"
        upsert_channel(channel.guild.id, channel.id, channel.name, ctype, getattr(channel, "position", 0))

    @bot.event
    async def on_guild_channel_update(before, after):
        if not after.guild: return
        ctype = "voice" if isinstance(after, discord.VoiceChannel) else \
                "category" if isinstance(after, discord.CategoryChannel) else \
                "text"
        upsert_channel(after.guild.id, after.id, after.name, ctype, getattr(after, "position", 0))

    @bot.event
    async def on_guild_channel_delete(channel):
        if not channel.guild: return
        remove_channel(channel.guild.id, channel.id)


    @bot.event
    async def on_guild_role_create(role):
        try:
            _sync_guild_roles(role.guild)
        except Exception as e:
            print(f"[roles] sync on create: {e}")


    @bot.event
    async def on_guild_role_update(before, after):
        try:
            _sync_guild_roles(after.guild)
        except Exception as e:
            print(f"[roles] sync on update: {e}")


    @bot.event
    async def on_guild_role_delete(role):
        try:
            _sync_guild_roles(role.guild)
        except Exception as e:
            print(f"[roles] sync on delete: {e}")


    # ===== LOGS - event capture =====
    @bot.event
    async def on_app_command_completion(interaction: discord.Interaction, command):
        if not interaction.guild:
            return
        # Rebuild a readable argument list
        args_str = ""
        try:
            opts = (interaction.data or {}).get("options") or []
            parts = []
            for o in opts:
                if "value" in o:
                    parts.append(f"{o['name']}={o['value']}")
                elif "options" in o:
                    # subcommand
                    parts.append(o.get("name", ""))
                    for sub in o["options"]:
                        if "value" in sub:
                            parts.append(f"{sub['name']}={sub['value']}")
            args_str = " ".join(parts)
        except Exception:
            pass
        add_log(
            interaction.guild.id, "command",
            user_id=interaction.user.id, username=str(interaction.user),
            channel_id=interaction.channel.id if interaction.channel else None,
            channel_name=getattr(interaction.channel, "name", None),
            content=f"/{command.qualified_name}" + (f" {args_str}" if args_str else ""),
        )

    @bot.event
    async def on_message_delete(message: discord.Message):
        if not message.guild or message.author.bot:
            return
        add_log(
            message.guild.id, "action_message_delete",
            user_id=message.author.id, username=str(message.author),
            channel_id=message.channel.id, channel_name=getattr(message.channel, "name", None),
            content=message.content or t("runtime.log.empty_message",
                                        guild_locale(message.guild.id) or "en"),
        )

    @bot.event
    async def on_message_edit(before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot:
            return
        if (before.content or "") == (after.content or ""):
            return  # just an embed/preview refreshing itself
        add_log(
            after.guild.id, "action_message_edit",
            user_id=after.author.id, username=str(after.author),
            channel_id=after.channel.id, channel_name=getattr(after.channel, "name", None),
            content=(after.content or "")[:1000],
            meta={"before": (before.content or "")[:1000]},
        )

    @bot.event
    async def on_voice_state_update(member, before, after):
        if not member.guild:
            return
        # 1) Log voice changes (join/leave/move) - useful for analytics
        if before.channel != after.channel:
            if before.channel is None and after.channel is not None:
                add_log(member.guild.id, "action_voice_join",
                        user_id=member.id, username=str(member),
                        channel_id=after.channel.id, channel_name=after.channel.name)
            elif before.channel is not None and after.channel is None:
                add_log(member.guild.id, "action_voice_leave",
                        user_id=member.id, username=str(member),
                        channel_id=before.channel.id, channel_name=before.channel.name)
            else:
                add_log(member.guild.id, "action_voice_move",
                        user_id=member.id, username=str(member),
                        channel_id=after.channel.id, channel_name=after.channel.name,
                        meta={"from": before.channel.name, "to": after.channel.name})

        # 2) CS2 voice hook (cleanup of empty CS2 voice channels)
        try:
            from commandes.cs2 import on_voice_state_update as _cs2_voice
            await _cs2_voice(member, before, after, bot)
        except Exception as e:
            print(f"[cs2/voice-hook] {type(e).__name__}: {e}")

        # 3) Tempvoice (lobby -> create a personal channel + cleanup when empty)
        try:
            from commandes.tempvoice import tempvoice_on_voice_state_update as _tv_voice
            await _tv_voice(member, before, after, bot)
        except Exception as e:
            print(f"[tempvoice/voice-hook] {type(e).__name__}: {e}")

    @bot.event
    async def on_member_remove(member):
        if not member.guild:
            return
        add_log(member.guild.id, "action_member_leave",
                user_id=member.id, username=str(member),
                content=t("runtime.log.member_leave",
                          guild_locale(member.guild.id) or "en",
                          guild=member.guild.name))
        try:
            remove_member(member.guild.id, member.id)
        except Exception:
            pass
        try:
            from database import member_roles_clear
            member_roles_clear(member.guild.id, member.id)
        except Exception:
            pass

    @bot.event
    async def on_member_update(before, after):
        if not after.guild:
            return
        try:
            upsert_member(after.guild.id, after.id, str(after),
                          avatar_url=str(after.display_avatar.url) if after.display_avatar else None,
                          is_bot=after.bot, joined_at=after.joined_at)
        except Exception:
            pass
        # Sync roles when they changed
        try:
            before_ids = {str(r.id) for r in (before.roles or [])}
            after_ids  = {str(r.id) for r in (after.roles or [])}
            if before_ids != after_ids:
                from database import member_roles_set
                member_roles_set(after.guild.id, after.id, list(after_ids))
        except Exception:
            pass

        # Supporter role detection (VIP / Super VIP) -> thank-you message
        try:
            support_guild_id = os.getenv("SUPPORT_GUILD_ID", "1502322150822908115")
            # Channel: dashboard setting first, then env, then default
            soutien_chan_id = (get_setting("soutien_channel_id", "") or "").strip() \
                or os.getenv("SOUTIEN_CHANNEL_ID", "1510450694195511436")

            if support_guild_id and soutien_chan_id and str(after.guild.id) == support_guild_id:
                before_ids_r = {r.id for r in (before.roles or [])}
                after_roles  = {r.id: r for r in (after.roles or [])}
                gained_ids = set(after_roles.keys()) - before_ids_r

                # Trigger roles: configured IDs (dashboard) first, otherwise default names
                cfg_ids_csv = (get_setting("soutien_role_ids", "") or "").strip()
                trigger_ids = {int(x) for x in cfg_ids_csv.split(",") if x.strip().isdigit()}

                matched_role = None
                for rid in gained_ids:
                    role = after_roles[rid]
                    if trigger_ids:
                        if rid in trigger_ids:
                            matched_role = role
                            break
                    else:
                        # Fallback by name (default)
                        if role.name in {"💎 VIP", "🧡 Super VIP"}:
                            matched_role = role
                            break

                if matched_role:
                    chan = bot.get_channel(int(soutien_chan_id))
                    if chan:
                        import datetime as _dt2
                        template = get_setting("soutien_message", "") \
                            or t("runtime.soutien.default_message",
                                 guild_locale(after.guild.id) or "en")
                        msg = (template
                               .replace("<user>", f"<@{after.id}>")
                               .replace("<username>", after.display_name)
                               .replace("<role>", matched_role.name)
                               .replace("<server>", after.guild.name)
                               .replace("<timestamp>", f"<t:{int(_dt2.datetime.now().timestamp())}:F>"))
                        await chan.send(
                            msg,
                            allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
                        )
                    else:
                        print(f"[soutien] channel {soutien_chan_id} not found (bot not in the server or wrong ID)")
        except Exception as _e:
            print(f"[soutien] notif err: {_e!r}")

    @tasks.loop(seconds=5)
    async def reload_reactions():
        USER_REACTIONS.clear()
        USER_REACTIONS.update(get_all_reactions_index())

    _PLATFORM_DEFAULT_MSG = {
        "youtube": "📺 **{author}** published a new video: **{title}**\n{url}",
        "reddit":  "🟠 New post from **{target}**: **{title}**\n{url}",
        "twitch":  "🔴 **{target}** is LIVE - *{title}*\n🎮 {game} · 👀 {viewers} viewers\n{url}",
    }

    _PLATFORM_COLOR = {
        "twitch":  0x9146FF,
        "youtube": 0xFF0000,
        "reddit":  0xFF4500,
    }
    _PLATFORM_ICON = {
        "twitch":  "https://cdn.discordapp.com/emojis/892812477145309244.png",
        "youtube": "https://www.youtube.com/s/desktop/12d6b690/img/favicon_144x144.png",
        "reddit":  "https://www.redditstatic.com/desktop2x/img/favicon/apple-icon-180x180.png",
    }


    def _build_social_embed(platform: str, target_label: str, item: dict,
                            custom_template: str | None,
                            lang: str = "en") -> tuple[str | None, discord.Embed]:
        """Build (content, embed) per platform. If the user set a custom message,
        it is used as the content above the embed (ping-friendly)."""
        title = (item.get("title") or "").strip()
        url   = item.get("url") or ""
        author = item.get("author") or target_label

        content = None
        if custom_template:
            try:
                content = custom_template.format(
                    target=target_label, title=title, url=url, author=author,
                    game=item.get("game", ""), viewers=item.get("viewers", 0),
                )
            except (KeyError, IndexError):
                content = custom_template

        color = _PLATFORM_COLOR.get(platform, 0x2B2D31)
        embed = discord.Embed(color=color, url=url or None)

        if platform == "twitch":
            embed.title = title or t("runtime.social.twitch_title", lang, target=target_label)
            embed.url = url
            embed.description = t("runtime.social.twitch_desc", lang, target=target_label)
            if item.get("game"):
                embed.add_field(name=t("runtime.social.twitch_game", lang),
                                value=str(item["game"]), inline=True)
            if item.get("viewers") is not None:
                embed.add_field(name=t("runtime.social.twitch_viewers", lang),
                                value=f"{item['viewers']:,}".replace(",", " "), inline=True)
            thumb = item.get("thumb")
            if thumb:
                embed.set_image(url=thumb)
            embed.set_author(name=f"{target_label} · Twitch",
                             url=f"https://twitch.tv/{target_label}",
                             icon_url=_PLATFORM_ICON["twitch"])

        elif platform == "youtube":
            embed.title = title or t("runtime.social.youtube_title", lang)
            embed.url = url
            embed.description = t("runtime.social.youtube_desc", lang, author=author)
            # YouTube thumbnail from the videoId
            vid = item.get("id")
            if vid:
                embed.set_image(url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
            embed.set_author(name=f"{author} · YouTube",
                             url=url, icon_url=_PLATFORM_ICON["youtube"])

        elif platform == "reddit":
            embed.title = title or t("runtime.social.reddit_title", lang)
            embed.url = url
            embed.description = t("runtime.social.reddit_desc", lang, target=target_label)
            embed.set_author(name=f"{target_label} · Reddit",
                             url=url, icon_url=_PLATFORM_ICON["reddit"])

        else:
            embed.title = title or t("runtime.social.generic_title", lang)
            embed.url = url
            embed.description = t("runtime.social.generic_desc", lang, target=target_label)

        embed.timestamp = _dt.datetime.now(_dt.timezone.utc)
        return content, embed


    @tasks.loop(minutes=5)
    async def social_alerts_poll():
        """Poll every active social alert. Compare with last_seen_id and post
        anything new in the configured channel."""
        try:
            alerts = social_alerts_list(enabled_only=True)
        except Exception as e:
            print(f"[social] list error: {e!r}")
            return
        twitch_warned = False
        for alert in alerts:
            try:
                # Warn loud if twitch creds missing (silent fail otherwise)
                if (alert["platform"] == "twitch" and not twitch_warned
                        and not (os.getenv("TWITCH_CLIENT_ID") and os.getenv("TWITCH_CLIENT_SECRET"))):
                    print("[social] WARNING: TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set in env — twitch alerts will never fire")
                    twitch_warned = True
                new_items = await social.check_platform(
                    alert["platform"], alert["target_id"], alert["last_seen_id"],
                )
                print(f"[social] check {alert['platform']}/{alert['target_id']} alert={alert['id']} "
                      f"last_seen={alert.get('last_seen_id')!r} -> {len(new_items)} item(s)")
                # Always touch last_check_at so dashboard reflects the poll happened
                social_alert_touch_check(alert["id"])
                if not new_items:
                    # First run -> write the marker for the next poll
                    if not alert.get("last_seen_id") and alert["platform"] != "twitch":
                        # Try a read to seed last_seen_id
                        seed = await social.check_platform(
                            alert["platform"], alert["target_id"], "__seed__",
                        )
                        # check_platform with a non-empty last_seen_id returns [] since it
                        # looks for the marker. We grab the current first video through a
                        # manual seed: reload the raw page
                        if alert["platform"] == "youtube":
                            latest = await _social_latest_youtube_id(alert["target_id"])
                            if latest:
                                social_alert_update_seen(alert["id"], latest)
                        elif alert["platform"] == "reddit":
                            latest = await _social_latest_reddit_id(alert["target_id"])
                            if latest:
                                social_alert_update_seen(alert["id"], latest)
                    if alert["platform"] == "twitch" and not alert.get("last_seen_id"):
                        social_alert_update_seen(alert["id"], "offline")
                    continue

                channel = bot.get_channel(int(alert["channel_id"]))
                if not channel:
                    continue

                template = alert.get("message_template")  # may be None
                target_label = alert.get("target_label") or alert["target_id"]
                alert_lang = guild_locale(getattr(channel, "guild", None)
                                          and channel.guild.id) or "en"
                for item in new_items:
                    if item.get("_silent"):
                        # Twitch went offline: only update the marker
                        social_alert_update_seen(alert["id"], item["id"])
                        continue

                    content, embed = _build_social_embed(
                        alert["platform"], target_label, item, template, alert_lang,
                    )
                    sent_ok = False
                    try:
                        await channel.send(content=content, embed=embed)
                        sent_ok = True
                    except discord.Forbidden:
                        print(f"[social] forbidden post #{alert['channel_id']} alert={alert['id']} - will retry on next poll")
                    except Exception as e:
                        print(f"[social] send err alert={alert['id']}: {e!r} - will retry on next poll")
                    if sent_ok:
                        social_alert_update_seen(alert["id"], item["id"])
            except Exception as e:
                print(f"[social] poll err alert={alert.get('id')}: {e!r}")


    async def _social_latest_youtube_id(target_id: str) -> str | None:
        if not target_id.startswith("UC"):
            target_id = (await social.youtube_resolve_handle(target_id)) or target_id
        items = await social.check_youtube(target_id, "__nope__")
        if items:
            return items[0]["id"]
        # Fallback: redo the raw request to grab the first entry
        try:
            s = await social._get_session()
            url = social.YOUTUBE_FEED_URL.format(cid=target_id)
            async with s.get(url) as resp:
                if resp.status != 200:
                    return None
                xml = await resp.text()
            import xml.etree.ElementTree as _ET
            root = _ET.fromstring(xml)
            first = root.find("{http://www.w3.org/2005/Atom}entry/{http://www.youtube.com/xml/schemas/2015}videoId")
            return first.text if first is not None else None
        except Exception:
            return None


    async def _social_latest_reddit_id(target_id: str) -> str | None:
        items = await social.check_reddit(target_id, "__nope__")
        if items:
            return items[0]["id"]
        return None


    @social_alerts_poll.before_loop
    async def _before_social_poll():
        await bot.wait_until_ready()


    @tasks.loop(hours=6)
    async def pass_rotation_loop():
        """Battle Pass rotation (every 6h):
        - Generate the seasonal backgrounds of the current month if missing
        - From the 25th of the month, pre-generate next month's ones
        - Create the current month season through get_or_create_current_season()
          (which seeds pass_rewards + sabers automatically)
        """
        import datetime as _dt
        import os as _os, subprocess as _sp, sys as _sys
        # Repo root: tasks/runtime.py -> tasks/ -> repo root
        _REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        _BG_SEASONAL_ROOT = _os.path.join(_REPO_ROOT, "assets", "niveau_bg", "seasonal")
        _BG_SCRIPT = _os.path.join(_REPO_ROOT, "scripts", "generate_seasonal_backgrounds.py")

        def _ensure_bgs(mk: str, label: str):
            target_dir = _os.path.join(_BG_SEASONAL_ROOT, mk)
            if _os.path.isdir(target_dir) and len(_os.listdir(target_dir)) >= 5:
                return
            if not _os.path.exists(_BG_SCRIPT):
                print(f"[pass rotation] {label} script absent: {_BG_SCRIPT}")
                return
            res = _sp.run(
                [_sys.executable, _BG_SCRIPT, mk],
                cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
            )
            if res.returncode == 0:
                print(f"[pass rotation] BGs generated for {mk} ({label})")
            else:
                print(f"[pass rotation] BG gen FAILED {mk} ({label}) rc={res.returncode}\n"
                      f"  stdout: {res.stdout[-400:]}\n  stderr: {res.stderr[-400:]}")

        try:
            # Current season (created if missing, auto seeded)
            season = get_or_create_current_season()
            mk = season["month_key"]

            _ensure_bgs(mk, "current month")

            # From the 25th of the month, pre-generate next month's ones
            now = _dt.datetime.utcnow()
            if now.day >= 25:
                if now.month == 12:
                    next_mk = f"{now.year + 1}-01"
                else:
                    next_mk = f"{now.year}-{now.month + 1:02d}"
                _ensure_bgs(next_mk, "next month preheat")
        except Exception as e:
            print(f"[pass rotation] error: {e!r}")


    @pass_rotation_loop.before_loop
    async def _before_pass_rotation():
        await bot.wait_until_ready()


    @tasks.loop(hours=24)
    async def daily_logs_purge():
        """Daily purge: > 90 days OR > 5000 logs per guild. Reclaims disk space with VACUUM."""
        try:
            keep = max(100, int(get_setting("log_keep_per_guild") or "5000"))
            age  = max(7,   int(get_setting("log_retention_days")  or "90"))
            res = prune_logs_global(keep_per_guild=keep, max_age_days=age)
            if res["by_age"] or res["by_count"]:
                print(f"[purge] logs: -{res['by_age']} (age) -{res['by_count']} (count) + VACUUM")
        except Exception as e:
            print(f"[purge] error: {e}")
            BOT_STATE["last_error"]    = f"purge: {e}"
            BOT_STATE["last_error_at"] = _time.time()

    @daily_logs_purge.before_loop
    async def _before_purge():
        await bot.wait_until_ready()

    @bot.event
    async def on_member_join(member):
        add_log(member.guild.id, "action_member_join",
                user_id=member.id, username=str(member),
                content=t("runtime.log.member_join",
                          guild_locale(member.guild.id) or "en",
                          guild=member.guild.name))
        # Automod: raid protection (counts joins per minute)
        try:
            from services.automod import automod_on_member_join
            await automod_on_member_join(member, bot)
        except Exception as e:
            print(f"[automod/on_member_join] {type(e).__name__}: {e}")
        # Update member cache
        try:
            upsert_member(member.guild.id, member.id, str(member),
                          avatar_url=str(member.display_avatar.url) if member.display_avatar else None,
                          is_bot=member.bot, joined_at=member.joined_at)
        except Exception:
            pass
        # Init roles (empty on join unless the guild auto-assigns roles)
        try:
            from database import member_roles_set
            role_ids = [str(r.id) for r in (member.roles or []) if r.name != "@everyone"]
            member_roles_set(member.guild.id, member.id, role_ids)
        except Exception:
            pass

        data = get_welcome(member.guild.id)
        if not data:
            print(f"[welcome] no config for guild={member.guild.id}")
            return
        channel = bot.get_channel(data["channel_id"])
        if not channel:
            # Fallback 1: direct fetch when not cached
            try:
                channel = await bot.fetch_channel(int(data["channel_id"]))
            except Exception as e:
                print(f"[welcome] channel {data['channel_id']} not found guild={member.guild.id}: {e}")
                channel = None
        if not channel:
            # Fallback 2: channel configured through /setup (welcome) when the welcome
            # table points to a deleted channel. Auto-repairs the table.
            channel = _resolve_setup_channel(member.guild, "welcome")
            if channel:
                try:
                    from database import set_welcome
                    set_welcome(member.guild.id, channel.id, data.get("message"))
                    print(f"[welcome] table repaired -> /setup channel {channel.id} guild={member.guild.id}")
                except Exception as e:
                    print(f"[welcome] repair table err: {e}")
            else:
                print(f"[welcome] no valid channel (dead table + no /setup) guild={member.guild.id}")
                return
        template = data.get("message") or guild_setting_get(str(member.guild.id), "welcome_template", DEFAULT_WELCOME_MESSAGE)
        try:
            send_kwargs = build_welcome_send_kwargs(template, member)
        except Exception as e:
            print(f"[welcome] build kwargs err: {e}")
            send_kwargs = {"content": t("runtime.welcome.fallback",
                                        guild_locale(member.guild.id) or "en",
                                        user=member.mention)}
        try:
            await channel.send(**send_kwargs)
            print(f"[welcome] sent guild={member.guild.id} channel={data['channel_id']} user={member.id}")
        except Exception as e:
            print(f"[welcome] SEND FAILED guild={member.guild.id} channel={data['channel_id']}: {type(e).__name__}: {e}")
        return
    # ===== MONETIZATION : entitlements Discord =====

    def _entitlement_to_dict(ent) -> dict:
        """Convert an Entitlement object (discord.py) into a clean dict for the DB."""
        try:
            d = ent.to_dict()  # discord.py 2.4+
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {
            "id":        getattr(ent, "id", None),
            "user_id":   getattr(ent, "user_id", None),
            "sku_id":    getattr(ent, "sku_id", None),
            "type":      int(getattr(getattr(ent, "type", 0), "value", getattr(ent, "type", 0)) or 0),
            "starts_at": str(getattr(ent, "starts_at", "") or "") or None,
            "ends_at":   str(getattr(ent, "ends_at", "") or "") or None,
            "consumed":  bool(getattr(ent, "consumed", False)),
            "deleted":   bool(getattr(ent, "deleted", False)),
        }


    # ========== REACTION ROLES ==========

    def _format_emoji_key(payload_emoji) -> str:
        """Convert a RawReactionActionEvent emoji into a canonical string:
        - Unicode emoji  -> raw character (e.g. '🟢')
        - Custom emoji   -> '<:name:id>' or '<a:name:id>' when animated
        """
        e = payload_emoji
        if e.id:
            prefix = "a" if e.animated else ""
            return f"<{prefix}:{e.name}:{e.id}>"
        return e.name


    _SUGGEST_CHANNEL_ID = 1513592894265757716
    _VOTE_EMOJIS = ("🔼", "🔽")

    async def _recount_suggestion_votes(payload):
        """Recount 🔼/🔽 under a suggestion message and store the ratio in the DB."""
        try:
            if payload.channel_id != _SUGGEST_CHANNEL_ID:
                return
            if str(payload.emoji) not in _VOTE_EMOJIS:
                return
            from database import card_suggestion_get_by_forward, card_suggestion_set_votes
            sugg = card_suggestion_get_by_forward(payload.message_id)
            if not sugg:
                return
            channel = bot.get_channel(payload.channel_id)
            if not channel:
                return
            msg = await channel.fetch_message(payload.message_id)
            up = down = 0
            for r in msg.reactions:
                if str(r.emoji) == "🔼":
                    up = max(0, r.count - 1)   # drop the bot's own reaction
                elif str(r.emoji) == "🔽":
                    down = max(0, r.count - 1)
            card_suggestion_set_votes(sugg["id"], up, down)
        except Exception as e:
            print(f"[suggest votes] recount err: {e}")

    @bot.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == bot.user.id:
            return
        await _recount_suggestion_votes(payload)
        emoji_key = _format_emoji_key(payload.emoji)
        mapping = db_rr_get(payload.guild_id, payload.message_id, emoji_key)
        if not mapping:
            return
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
        if not member or member.bot:
            return
        role = guild.get_role(int(mapping["role_id"]))
        if not role:
            return
        try:
            # 'unique' mode: remove the other roles of the same group_key first
            if mapping.get("mode") == "unique" and mapping.get("group_key"):
                others = db_rr_list_unique(payload.guild_id, payload.message_id, mapping["group_key"])
                for o in others:
                    if o["emoji"] == emoji_key:
                        continue
                    other_role = guild.get_role(int(o["role_id"]))
                    if other_role and other_role in member.roles:
                        await member.remove_roles(other_role, reason="ReactionRole unique group")
                    # Also remove their reaction from the message
                    try:
                        channel = guild.get_channel(payload.channel_id)
                        msg = await channel.fetch_message(payload.message_id)
                        await msg.remove_reaction(o["emoji"], member)
                    except Exception:
                        pass
            if role not in member.roles:
                await member.add_roles(role, reason=f"ReactionRole {emoji_key}")
        except discord.Forbidden:
            print(f"[rolereaction] Forbidden: missing permission for role {role.id} on {guild.id}")
        except Exception as e:
            print(f"[rolereaction] add error: {e!r}")


    @bot.event
    async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == bot.user.id:
            return
        await _recount_suggestion_votes(payload)
        emoji_key = _format_emoji_key(payload.emoji)
        mapping = db_rr_get(payload.guild_id, payload.message_id, emoji_key)
        if not mapping:
            return
        if mapping.get("mode") == "add_only":
            return
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        try:
            member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
        except Exception:
            return
        if not member or member.bot:
            return
        role = guild.get_role(int(mapping["role_id"]))
        if not role:
            return
        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"ReactionRole - {emoji_key}")
        except discord.Forbidden:
            print(f"[rolereaction] Forbidden: missing permission for role {role.id} on {guild.id}")
        except Exception as e:
            print(f"[rolereaction] remove error: {e!r}")


    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        # Handles rolereaction buttons only (custom_id "rr:<role_id>").
        # Other interactions (slash commands, other components) are handled elsewhere.
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("rr:"):
            return
        if not interaction.guild:
            return
        try:
            role_id = int(cid.split(":", 1)[1])
        except (ValueError, IndexError):
            return
        guild = interaction.guild
        member = interaction.user
        if not isinstance(member, discord.Member) or member.bot:
            return
        role = guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(
                ti(interaction, "runtime.rolebutton.role_not_found"), ephemeral=True)
            return
        # Fetch the mapping for mode/group
        rows = db_rr_list(guild.id, interaction.message.id)
        mapping = next((r for r in rows if str(r["role_id"]) == str(role_id)), None)
        mode = mapping.get("mode") if mapping else "toggle"
        group_key = mapping.get("group_key") if mapping else None
        try:
            if mode == "unique" and group_key:
                # Remove the other roles of the group
                others = db_rr_list_unique(guild.id, interaction.message.id, group_key)
                for o in others:
                    if str(o["role_id"]) == str(role_id):
                        continue
                    other_role = guild.get_role(int(o["role_id"]))
                    if other_role and other_role in member.roles:
                        await member.remove_roles(other_role, reason="RoleButton unique group")
                if role not in member.roles:
                    await member.add_roles(role, reason="RoleButton unique")
                    await interaction.response.send_message(
                        ti(interaction, "runtime.rolebutton.granted", role=role.mention),
                        ephemeral=True)
                else:
                    await interaction.response.send_message(
                        ti(interaction, "runtime.rolebutton.already_owned", role=role.mention),
                        ephemeral=True)
            elif mode == "add_only":
                if role not in member.roles:
                    await member.add_roles(role, reason="RoleButton add_only")
                    await interaction.response.send_message(
                        ti(interaction, "runtime.rolebutton.granted", role=role.mention),
                        ephemeral=True)
                else:
                    await interaction.response.send_message(
                        ti(interaction, "runtime.rolebutton.already_owned", role=role.mention),
                        ephemeral=True)
            else:
                # toggle
                if role in member.roles:
                    await member.remove_roles(role, reason="RoleButton toggle off")
                    await interaction.response.send_message(
                        ti(interaction, "runtime.rolebutton.removed", role=role.mention),
                        ephemeral=True)
                else:
                    await member.add_roles(role, reason="RoleButton toggle on")
                    await interaction.response.send_message(
                        ti(interaction, "runtime.rolebutton.granted", role=role.mention),
                        ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                ti(interaction, "runtime.rolebutton.no_perm"), ephemeral=True)
        except Exception as e:
            print(f"[rolebutton] error: {e!r}")
            try:
                await interaction.response.send_message(
                    ti(interaction, "runtime.rolebutton.error"), ephemeral=True)
            except Exception:
                pass


    @bot.event
    async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command):
        """Slash command completed successfully -> +1 use_commands Pass quest."""
        try:
            if interaction.user and not interaction.user.bot:
                _track_pass_quest(interaction.user.id, "use_commands", 1)
        except Exception:
            pass


    @bot.event
    async def on_entitlement_create(entitlement):
        try:
            d = _entitlement_to_dict(entitlement)
            upsert_entitlement(d)
            print(f"[entitlement] CREATE user={d.get('user_id')} sku={d.get('sku_id')}")
        except Exception as e:
            print(f"[entitlement] create error: {e!r}")


    @bot.event
    async def on_entitlement_update(entitlement):
        try:
            d = _entitlement_to_dict(entitlement)
            upsert_entitlement(d)
            print(f"[entitlement] UPDATE user={d.get('user_id')} sku={d.get('sku_id')}")
        except Exception as e:
            print(f"[entitlement] update error: {e!r}")


    @bot.event
    async def on_entitlement_delete(entitlement):
        try:
            eid = getattr(entitlement, "id", None) or _entitlement_to_dict(entitlement).get("id")
            if eid:
                mark_entitlement_deleted(str(eid))
                print(f"[entitlement] DELETE id={eid}")
        except Exception as e:
            print(f"[entitlement] delete error: {e!r}")


    # Anti double dispatch: Discord can re-deliver MESSAGE_CREATE after a reconnect
    # or a gateway timeout. Caches already handled message_ids (max 2048, naive LRU).
    _MSG_SEEN = _col.OrderedDict()
    _MSG_SEEN_MAX = 2048

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        # Dedup
        mid = getattr(message, "id", None)
        if mid is not None:
            if mid in _MSG_SEEN:
                return
            _MSG_SEEN[mid] = True
            if len(_MSG_SEEN) > _MSG_SEEN_MAX:
                _MSG_SEEN.popitem(last=False)
        if message.guild is None:
            # DM (user -> bot): nothing is stored anymore (privacy reasons).
            await bot.process_commands(message)
            return
        guild_id_str = str(message.guild.id)

        # Card support channel: only the bot owner can write there. Everyone else
        # goes through /cardsuggest and /cardmodify (slash). The rest is deleted.
        if message.channel and getattr(message.channel, "id", None) == 1513592894265757716:
            try:
                if not await bot.is_owner(message.author):
                    await message.delete()
                    return
            except Exception:
                pass

        # Card events: claim through a text captcha
        try:
            from services.card_events import handle_message_claim
            if await handle_message_claim(bot, message):
                return  # claim done, skip rest
        except Exception as e:
            print(f"[card_event on_message] err: {e!r}")

        # Automod: TookBot+ filters (banned words, invites, mention spam)
        try:
            from services.automod import automod_on_message
            await automod_on_message(message, bot)
        except Exception as e:
            print(f"[automod/on_message] {type(e).__name__}: {e}")

        # ===== Groq AI: bot mention + author in the allowlist =====
        # Restricted to the support server only (nowhere else).
        _ai_support_guild = os.getenv("SUPPORT_GUILD_ID", "1502322150822908115")
        _ai_on_support = bool(message.guild and _ai_support_guild
                               and str(message.guild.id) == str(_ai_support_guild))
        if (_ai_on_support and bot.user in message.mentions and not message.author.bot
                and get_setting("ai_enabled", "0") == "1"):
            try:
                from services.groq_ai import groq_chat, get_groq_api_key
                allowed_csv = (get_setting("ai_allowed_user_ids", "") or "").strip()
                allowed_ids = {x.strip() for x in allowed_csv.split(",") if x.strip()}
                uid = str(message.author.id)
                bot_owner_id = (DISCORD_OWNER_ID or "")
                if uid in allowed_ids or (bot_owner_id and uid == str(bot_owner_id)):
                    if not get_groq_api_key():
                        await message.reply(
                            t("runtime.ai.not_configured",
                              guild_locale(message.guild.id) or "en"),
                            mention_author=False)
                    else:
                        # Strip the bot mention; replace the other mentions with
                        # @DisplayName so the model knows who is being talked about.
                        prompt = message.content
                        mention_map = {}   # display_name.lower() -> Member
                        for m in message.mentions:
                            if m.id == bot.user.id:
                                prompt = prompt.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
                            else:
                                nm = m.display_name
                                prompt = (prompt.replace(f"<@{m.id}>", f"@{nm}")
                                                .replace(f"<@!{m.id}>", f"@{nm}"))
                                mention_map[nm.lower()] = m
                        prompt = prompt.strip()
                        if not prompt:
                            await message.reply(
                                t("runtime.ai.no_question",
                                  guild_locale(message.guild.id) or "en"),
                                mention_author=False)
                        else:
                            # Per-channel conversation memory, reset after 1h idle
                            now_ai = _time.time()
                            chan_id = message.channel.id
                            mem = _AI_MEMORY.get(chan_id)
                            if mem is None or (now_ai - mem["ts"]) > _AI_MEMORY_TTL:
                                mem = {"history": [], "ts": now_ai}
                                _AI_MEMORY[chan_id] = mem
                            mem["ts"] = now_ai

                            # Build the system prompt + list of mentionable people.
                            # IMPORTANT: several people talk in the same channel. Each
                            # user message is prefixed with its author so the model does
                            # not mix up the speakers.
                            author_name = message.author.display_name
                            sys_prompt = get_setting("ai_system_prompt", "") or ""
                            sys_prompt += (
                                "\n\nSeveral different people can talk to you in this channel. "
                                "Every user message is prefixed with its author name in the "
                                "form 'Name: message'. Never mix up the speakers: treat them "
                                "as distinct people."
                            )
                            if mention_map:
                                who = ", ".join(sorted({m.display_name for m in mention_map.values()}))
                                sys_prompt += (
                                    f"\n\nPeople mentioned in this channel: {who}. "
                                    f"To mention someone in your reply, write their name "
                                    f"prefixed with @ (example: @{next(iter(mention_map.values())).display_name})."
                                )

                                # Inject the Discord profile of the mentioned members
                                # (so the AI can describe who is who).
                                profiles = []
                                for member in mention_map.values():
                                    created = getattr(member, "created_at", None)
                                    joined = getattr(member, "joined_at", None)
                                    roles = [r.name for r in getattr(member, "roles", []) if r.name != "@everyone"]
                                    status = str(getattr(member, "status", "?"))
                                    activity = getattr(member, "activity", None)
                                    activity_str = (f" - Activity: {activity.name}"
                                                    if activity and getattr(activity, "name", None) else "")
                                    avatar_url = (member.display_avatar.url
                                                  if getattr(member, "display_avatar", None) else "?")
                                    profiles.append(
                                        f"- @{member.display_name} (id {member.id})"
                                        f" | Discord username: {member.name}"
                                        f" | Account created on: {created.strftime('%Y-%m-%d') if created else '?'}"
                                        f" | Joined the server on: {joined.strftime('%Y-%m-%d') if joined else '?'}"
                                        f" | Status: {status}{activity_str}"
                                        f" | Roles: {', '.join(roles) if roles else 'none'}"
                                        f" | Avatar: {avatar_url}"
                                    )
                                sys_prompt += "\n\nDiscord profiles of the mentioned people:\n" + "\n".join(profiles)

                                # Profile of the author themselves (useful for personal replies)
                                a = message.author
                                a_created = getattr(a, "created_at", None)
                                a_joined = getattr(a, "joined_at", None)
                                a_roles = [r.name for r in getattr(a, "roles", []) if r.name != "@everyone"]
                                sys_prompt += (
                                    f"\n\nProfile of the author of the current message: "
                                    f"@{a.display_name} (id {a.id}), "
                                    f"account created {a_created.strftime('%Y-%m-%d') if a_created else '?'}, "
                                    f"joined the server {a_joined.strftime('%Y-%m-%d') if a_joined else '?'}, "
                                    f"roles: {', '.join(a_roles) if a_roles else 'none'}."
                                )

                            # --- Detect images / GIFs in attachments ---
                            image_urls = []
                            for att in (message.attachments or []):
                                ct = (att.content_type or "").lower()
                                if ct.startswith("image/"):
                                    image_urls.append(att.url)
                            # Discord embeds too (Tenor/Giphy gifs for instance)
                            for emb in (message.embeds or []):
                                img = getattr(emb, "image", None)
                                if img and getattr(img, "url", None):
                                    image_urls.append(img.url)
                                thumb = getattr(emb, "thumbnail", None)
                                if thumb and getattr(thumb, "url", None):
                                    image_urls.append(thumb.url)
                            image_urls = image_urls[:5]  # cap

                            # When an image is present, switch to a Groq vision model
                            base_model = get_setting("ai_model", "llama-3.3-70b-versatile")
                            vision_model = get_setting("ai_vision_model",
                                                       "meta-llama/llama-4-scout-17b-16e-instruct")
                            if image_urls:
                                used_model = vision_model
                                sys_prompt += (
                                    "\n\nThe user attached one or more images/GIFs."
                                    " Describe them precisely and take them into account in your reply."
                                )
                                # Groq vision sometimes does not support system + text history;
                                # we simplify by sending only the prompt + images, without history.
                                history_to_send = []
                            else:
                                used_model = base_model
                                history_to_send = list(mem["history"])

                            # The author goes into the system prompt as context, NOT as a
                            # message prefix (otherwise the AI repeats the name in its reply).
                            sys_prompt += (
                                f"\n\nCurrent message sent by the user '{author_name}'."
                                " Reply to them directly, NEVER start your reply with their name"
                                " (neither '{author_name}:' nor '@{author_name}'). Discord already shows a reply."
                                "\n\nIF you are asked to pass a message to another member"
                                " (like 'tell @X that...', 'ask @Y to...'), talk DIRECTLY to that"
                                " person with their @X mention, without repeating the request to the author"
                                " ('Hey bro tell @X...' = FORBIDDEN). You address the target, not the sender."
                            )
                            prompt_for_model = prompt
                            try:
                                async with message.channel.typing():
                                    res = await groq_chat(
                                        prompt_for_model,
                                        system_prompt=sys_prompt,
                                        model=used_model,
                                        max_tokens=int(get_setting("ai_max_tokens", "400") or "400"),
                                        history=history_to_send,
                                        image_urls=image_urls or None,
                                    )
                                txt = res["text"] if isinstance(res, dict) else str(res)

                                # Remember the exchange (user message prefixed with the author)
                                mem["history"].append({"role": "user", "content": prompt_for_model})
                                mem["history"].append({"role": "assistant", "content": txt})
                                if len(mem["history"]) > _AI_MEMORY_MAX_MSGS:
                                    mem["history"] = mem["history"][-_AI_MEMORY_MAX_MSGS:]

                                # Turn @DisplayName in the reply into real Discord mentions
                                if mention_map:
                                    for nm in sorted(mention_map.keys(), key=len, reverse=True):
                                        member = mention_map[nm]
                                        txt = _re.sub(
                                            r"@" + _re.escape(member.display_name),
                                            f"<@{member.id}>", txt, flags=_re.IGNORECASE,
                                        )

                                allowed_m = discord.AllowedMentions(
                                    everyone=False, roles=False, users=True, replied_user=False,
                                )

                                # Voice mode: when enabled, the reply is synthesized to MP3.
                                # Configurable provider: "edge" (free, robotic) or
                                # "elevenlabs" (top quality, 10k chars/mo free, edge fallback when out of quota).
                                voice_sent = False
                                if get_setting("ai_voice_enabled", "0") == "1":
                                    try:
                                        from services.tts import synthesize
                                        import io as _io
                                        provider = get_setting("ai_voice_provider", "edge")
                                        audio_bytes, used = await synthesize(
                                            txt,
                                            provider=provider,
                                            edge_voice=get_setting("ai_voice_name", "fr-FR-DeniseNeural"),
                                            elevenlabs_voice_id=get_setting(
                                                "ai_elevenlabs_voice_id", "XB0fDUnXU5powFXDhCwa"),
                                            elevenlabs_model=get_setting(
                                                "ai_elevenlabs_model", "eleven_multilingual_v2"),
                                        )
                                        audio_file = discord.File(
                                            _io.BytesIO(audio_bytes),
                                            filename=f"tookbot-reply-{used}.mp3",
                                        )
                                        await message.reply(
                                            file=audio_file,
                                            mention_author=False,
                                            allowed_mentions=allowed_m,
                                        )
                                        voice_sent = True
                                    except Exception as _te:
                                        print(f"[ai] TTS fail -> text fallback: {_te!r}")

                                if not voice_sent:
                                    # If the reply mentions someone other than the author
                                    # (case: "tell @SENSIBY to..."), post a standalone message
                                    # instead of a reply that needlessly pings the author.
                                    target_other = False
                                    if mention_map:
                                        for member in mention_map.values():
                                            if member.id != message.author.id and f"<@{member.id}>" in txt:
                                                target_other = True
                                                break
                                    if target_other:
                                        await message.channel.send(txt[:2000],
                                                                   allowed_mentions=allowed_m)
                                    else:
                                        await message.reply(txt[:2000], mention_author=False,
                                                            allowed_mentions=allowed_m)
                                # Log usage
                                try:
                                    from database import ai_usage_add
                                    if isinstance(res, dict):
                                        ai_usage_add(
                                            user_id=message.author.id,
                                            guild_id=message.guild.id,
                                            model=res.get("model"),
                                            prompt_tokens=res.get("prompt_tokens", 0),
                                            completion_tokens=res.get("completion_tokens", 0),
                                            total_tokens=res.get("total_tokens", 0),
                                        )
                                except Exception as _ue:
                                    print(f"[ai] usage log err: {_ue!r}")
                            except Exception as e:
                                print(f"[ai] groq err: {e!r}")
                                await message.reply(
                                    t("runtime.ai.error",
                                      guild_locale(message.guild.id) or "en",
                                      error=type(e).__name__),
                                    mention_author=False)
                    # Stop here: no XP / commands for this message
                    return
            except Exception as _e:
                print(f"[ai] hook err: {_e!r}")

        # Per-guild automatic reactions
        key = (guild_id_str, message.author.id)
        if key in USER_REACTIONS:
            try:
                await message.add_reaction(USER_REACTIONS[key])
            except discord.HTTPException as e:
                print(f"❌ Reaction error: {e}")

        # ===== XP gain (clean rework, June 2026) =====
        # A single canonical helper: add_xp() upserts and returns the level diff.
        # No double read/write. Anti-spam cooldown per (guild, user).
        if (not message.author.bot
                and guild_setting_get(guild_id_str, "xp_enabled", "1") == "1"):
            try:
                cd = max(0, int(guild_setting_get(guild_id_str, "xp_cooldown_seconds", "30") or 30))
            except (TypeError, ValueError):
                cd = 30
            cd_key = (guild_id_str, message.author.id)
            now_ts = _time.time()
            last_ts = _XP_LAST_GAIN.get(cd_key, 0)
            if cd > 0 and (now_ts - last_ts) < cd:
                await bot.process_commands(message)
                return
            _XP_LAST_GAIN[cd_key] = now_ts

            try:
                xp_min = max(0, int(guild_setting_get(guild_id_str, "xp_min", "1") or 1))
                xp_max = max(xp_min, int(guild_setting_get(guild_id_str, "xp_max", "5") or 5))
            except (TypeError, ValueError):
                xp_min, xp_max = 1, 5
            gain = random.randint(xp_min, xp_max)

            # Pass XP boost (multiplier)
            try:
                boost = get_active_xp_boost_multiplier(message.author.id)
                if boost and boost > 1.0:
                    gain = int(gain * boost)
            except Exception:
                pass

            try:
                new_xp, old_level, new_level, leveled_up = add_xp(
                    guild_id_str, message.author.id, gain,
                    username=message.author.name,
                )
            except Exception as e:
                print(f"[xp gain] add_xp fail: {type(e).__name__}: {e}")
                await bot.process_commands(message)
                return

            try:
                _track_pass_quest(message.author.id, "send_messages", 1)
                _track_pass_quest(message.author.id, "earn_xp", gain)
            except Exception:
                pass

            if leveled_up:
                try:
                    _, _, _, percent = get_progress(new_xp, str(message.guild.id))
                    if is_premium_user(message.author.id):
                        try:
                            bg = (get_premium_settings(message.author.id) or {}).get("niveau_background") or "default"
                            image = await render_levelup_card_premium(
                                username=message.author.display_name,
                                avatar_url=str(message.author.display_avatar.url),
                                new_level=new_level,
                                percent=percent,
                                background=bg,
                            )
                        except Exception as e:
                            print(f"[levelup premium render] {e!r} - fallback")
                            image = await generate_levelup_card(message.author, new_level, percent)
                    else:
                        image = await generate_levelup_card(message.author, new_level, percent)
                    await message.channel.send(
                        content=f"🎉 {message.author.mention}",
                        file=discord.File(image, filename=f"levelup-{int(_time.time()*1000)}.png"),
                    )
                except Exception as e:
                    print(f"[levelup notif] {type(e).__name__}: {e}")
        await bot.process_commands(message)


    # ===== SLASH COMMAND ANTI-SPAM =====
    # Limit: max N commands per user within a sliding window
    # (_col is already imported at the top of the module)
    _USER_CMD_TIMES = _col.defaultdict(list)  # user_id -> [timestamp, ...]
    _RATE_LIMIT_N      = 12       # 12 commands
    _RATE_LIMIT_WINDOW = 30.0     # per 30 seconds

    def _is_rate_limited(user_id):
        now = _time.time()
        bucket = _USER_CMD_TIMES[user_id]
        # drop entries outside the window
        cutoff = now - _RATE_LIMIT_WINDOW
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= _RATE_LIMIT_N:
            return True, _RATE_LIMIT_WINDOW - (now - bucket[0])
        bucket.append(now)
        return False, 0.0

    # Capture the interaction_check already in place (feature/boost/mod-perm guard
    # assigned in bot.py) so it can be chained after the rate limit.
    _prev_interaction_check = bot.tree.interaction_check

    async def _global_rate_limit(interaction: discord.Interaction) -> bool:
        """Anti-spam rate limit THEN chain to the feature/boost/mod-perm guard."""
        # Autocomplete: never rate-limited/gated (every keystroke is an interaction;
        # it cannot be answered with a message -> "Failed to load options").
        if interaction.type == discord.InteractionType.autocomplete:
            return True
        # Bypass the rate limit for the bot owner
        is_bot_owner = False
        try:
            is_bot_owner = await bot.is_owner(interaction.user)
        except Exception:
            pass
        if not is_bot_owner:
            limited, retry = _is_rate_limited(interaction.user.id)
            if limited:
                try:
                    await interaction.response.send_message(
                        ti(interaction, "runtime.ratelimit.too_fast",
                           seconds=int(retry) + 1),
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return False
        # Chain to the feature/boost/mod-perm guard
        if callable(_prev_interaction_check):
            try:
                return await _prev_interaction_check(interaction)
            except Exception as e:
                print(f"[guard] chained interaction_check error: {e}", flush=True)
                return True
        return True

    # Global hook: rate limit + guard chained on the slash command tree
    bot.tree.interaction_check = _global_rate_limit

    # Reminders: fire the due reminders every minute
    @tasks.loop(seconds=30)
    async def reminders_dispatch():
        import datetime as _dtmod
        try:
            from database import reminders_due, reminder_mark_fired
            now_iso = _dtmod.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            due = reminders_due(now_iso)
            for r in due:
                try:
                    ch = bot.get_channel(int(r["channel_id"]))
                    if ch is None:
                        # Channel not found, mark as fired so we do not retry
                        reminder_mark_fired(r["id"])
                        continue
                    rem_lang = guild_locale(getattr(getattr(ch, "guild", None), "id", None)) or "en"
                    embed = discord.Embed(
                        title=t("runtime.reminder.title", rem_lang),
                        description=r["text"],
                        color=0xB9F23A,
                    )
                    embed.set_footer(text=t("runtime.reminder.footer", rem_lang,
                                            id=r["id"], created_at=r["created_at"]))
                    await ch.send(content=f"<@{r['user_id']}>", embed=embed)
                    reminder_mark_fired(r["id"])
                except Exception as e:
                    print(f"[reminders] fire err id={r.get('id')}: {type(e).__name__}: {e}")
                    # Mark as fired to avoid spam
                    try:
                        reminder_mark_fired(r["id"])
                    except Exception:
                        pass
        except Exception as e:
            print(f"[reminders] loop err: {type(e).__name__}: {e}")

    @reminders_dispatch.before_loop
    async def _before_reminders_dispatch():
        await bot.wait_until_ready()


    # Expired TookBot+ cleanup: detects expired tookbot_plus grants
    # (trial over, subscription ended and not renewed) then:
    # - DELETE custom_commands WHERE created_by = user
    # - For every guild_bot_profile applied_by user: revert the profile
    #   through a Discord PATCH + DELETE the DB row
    # - DELETE the expired grant (marker that the cleanup was done)
    @tasks.loop(minutes=2)
    async def tookbot_plus_expiry_cleanup():
        try:
            from database import get_db, guild_bot_profile_clear
            from services.bot_personalizer import patch_server_profile
            conn = get_db(); c = conn.cursor()
            # Expired grants not cleaned up yet
            expired = c.execute(
                """SELECT user_id, expires_at, note FROM premium_grants
                   WHERE feature = 'tookbot_plus'
                     AND expires_at IS NOT NULL
                     AND expires_at <= datetime('now')"""
            ).fetchall()
            if not expired:
                conn.close()
                return
            token = os.getenv("DISCORD_TOKEN", "")
            for row in expired:
                uid = row["user_id"]
                # Custom commands: delete every one created by this user
                try:
                    nb = c.execute(
                        "DELETE FROM custom_commands WHERE created_by = ?", (uid,),
                    ).rowcount
                    print(f"[tookbot_plus expiry] user={uid} custom_commands deleted: {nb}")
                except Exception as e:
                    print(f"[tookbot_plus expiry] custom_commands del err uid={uid}: {e!r}")
                # Bot profiles: revert every guild where this user had applied one
                profiles = c.execute(
                    "SELECT guild_id FROM guild_bot_profile WHERE applied_by = ?", (uid,),
                ).fetchall()
                for p in profiles:
                    g_id = p["guild_id"]
                    if token:
                        try:
                            await patch_server_profile(
                                token, g_id, nick="", bio="",
                                clear_avatar=True, clear_banner=True,
                            )
                            print(f"[tookbot_plus expiry] user={uid} bot_profile guild={g_id} reverted")
                        except Exception as e:
                            print(f"[tookbot_plus expiry] revert profile guild={g_id} err: {type(e).__name__}: {e}")
                    try:
                        guild_bot_profile_clear(g_id)
                    except Exception:
                        pass
                # Dashboard notification to inform the user
                try:
                    from database import dash_notif_add
                    note = row["note"] or ""
                    if note.startswith("trial"):
                        dash_notif_add(uid, "trial_expire",
                                       title=t("runtime.plus_expiry.trial_title", "en"),
                                       message=t("runtime.plus_expiry.trial_message", "en"),
                                       link_url="/subscription")
                    else:
                        dash_notif_add(uid, "trial_expire",
                                       title=t("runtime.plus_expiry.sub_title", "en"),
                                       message=t("runtime.plus_expiry.sub_message", "en"),
                                       link_url="/subscription")
                except Exception:
                    pass
                # Delete the expired grant (marker that the cleanup was done).
                # premium_settings.trial_used_at stays -> blocks a new trial.
                try:
                    c.execute(
                        "DELETE FROM premium_grants WHERE user_id = ? AND feature = 'tookbot_plus' AND expires_at <= datetime('now')",
                        (uid,),
                    )
                except Exception as e:
                    print(f"[tookbot_plus expiry] del grant uid={uid} err: {e!r}")
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[tookbot_plus expiry] loop err: {type(e).__name__}: {e}")

    @tookbot_plus_expiry_cleanup.before_loop
    async def _before_tookbot_plus_expiry():
        await bot.wait_until_ready()


    # Auto-disconnect from voice when idle > 60s (nothing playing nor paused).
    # Keeps the bot from sitting in a voice channel forever after the queue ends.
    _VOICE_IDLE_SINCE: dict = {}
    _VOICE_IDLE_TIMEOUT = 60  # seconds

    @tasks.loop(seconds=20)
    async def voice_idle_disconnect():
        now = _time.time()
        for guild in list(bot.guilds):
            vc = guild.voice_client
            if not vc or not vc.is_connected():
                _VOICE_IDLE_SINCE.pop(guild.id, None)
                continue
            if vc.is_playing() or vc.is_paused():
                _VOICE_IDLE_SINCE.pop(guild.id, None)
                continue
            since = _VOICE_IDLE_SINCE.get(guild.id)
            if since is None:
                _VOICE_IDLE_SINCE[guild.id] = now
                continue
            if (now - since) >= _VOICE_IDLE_TIMEOUT:
                try:
                    ch = vc.channel
                    await vc.disconnect(force=True)
                    print(f"[voice idle] guild={guild.id} disconnected after {int(now - since)}s idle in #{getattr(ch, 'name', '?')}")
                except Exception as e:
                    print(f"[voice idle] disconnect error guild={guild.id}: {type(e).__name__}: {e}")
                _VOICE_IDLE_SINCE.pop(guild.id, None)

    @voice_idle_disconnect.before_loop
    async def _before_voice_idle():
        await bot.wait_until_ready()


    @tasks.loop(seconds=30)
    async def rotate_presence():
        """Cycle Discord Activity through 4 messages."""
        statuses = [
            (discord.ActivityType.listening, t("runtime.presence.music", "en")),
            (discord.ActivityType.playing,   t("runtime.presence.commands", "en")),
            (discord.ActivityType.watching,  t("runtime.presence.servers", "en",
                                               count=len(bot.guilds))),
            (discord.ActivityType.playing,   t("runtime.presence.website", "en")),
        ]
        idx = (rotate_presence.current_loop or 0) % len(statuses)
        a_type, a_name = statuses[idx]
        try:
            await bot.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(type=a_type, name=a_name),
            )
        except Exception as e:
            print(f"[presence] error: {e}")

    @rotate_presence.before_loop
    async def _before_presence():
        await bot.wait_until_ready()


    @tasks.loop(seconds=15)
    async def status_writer():
        _write_bot_state()

    @status_writer.before_loop
    async def _before_status_writer():
        await bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def anti_spam_cleanup():
        """Purge empty buckets every 10 min (otherwise the dict grows forever)."""
        now = _time.time()
        cutoff = now - _RATE_LIMIT_WINDOW
        to_del = [uid for uid, ts in _USER_CMD_TIMES.items() if not ts or ts[-1] < cutoff]
        for uid in to_del:
            _USER_CMD_TIMES.pop(uid, None)

    @anti_spam_cleanup.before_loop
    async def _before_spam_cleanup():
        await bot.wait_until_ready()


    # ===== TOP.GG STATS POSTER =====
    # POST the guild count every 30 min to https://top.gg/api/bots/<id>/stats
    # Requires TOPGG_TOKEN in env. Silent when not configured.
    @tasks.loop(minutes=30)
    async def topgg_stats_poster():
        import os as _os
        token = (_os.getenv("TOPGG_TOKEN") or "").strip()
        if not token or not bot.user:
            return
        bot_id = bot.user.id
        guild_count = len(bot.guilds)
        url = f"https://top.gg/api/bots/{bot_id}/stats"
        try:
            import urllib.request as _req
            import json as _json
            body = _json.dumps({"server_count": guild_count}).encode("utf-8")
            req = _req.Request(url, data=body, method="POST", headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "User-Agent": "TookBot/1.0",
            })
            with _req.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 204):
                    print(f"[topgg] stats post status={resp.status}")
        except Exception as e:
            print(f"[topgg] stats post err: {e!r}")

    @tasks.loop(minutes=1)
    async def card_event_drop_loop():
        try:
            from services.card_events import check_due_drops
            n = await check_due_drops(bot)
            if n > 0:
                print(f"[card_event_loop] dropped {n} cards")
        except Exception as e:
            print(f"[card_event_loop] err: {e!r}")

    @card_event_drop_loop.before_loop
    async def _before_card_event_loop():
        await bot.wait_until_ready()

    def _auto_boss_tier(level):
        # Tier calibrated on the AVERAGE level of the server's card guilds (typical
        # strength, not just the whale; HP/ATK are rescaled on the present team).
        if level <= 5:    t = 1
        elif level <= 10: t = 2
        elif level <= 16: t = 3
        elif level <= 23: t = 4
        else:             t = 5
        import random as _r
        if _r.random() < 0.08:   # ~8%: a boss one tier above, better loot
            t = min(5, t + 1)
        return t

    @tasks.loop(minutes=20)
    async def auto_boss_loop():
        """Automatic boss spawn: 1 per eligible server every ~19-24h.
        Eligible = card channel configured, server older than N days, >= N human members."""
        try:
            import time as _t, random as _r, datetime as _dt
            from database import (get_setting, guild_card_config_get, card_boss_guild_has_active,
                                  boss_auto_get_next, boss_auto_set_next, avg_guild_level_for_users,
                                  avg_combat_power_for_users)
            from services.card_boss import spawn_boss
            if get_setting("auto_boss_enabled", "1") != "1":
                return
            min_days = int(get_setting("auto_boss_min_guild_age_days", "10") or 10)
            min_humans = int(get_setting("auto_boss_min_humans", "10") or 10)
            ih = float(get_setting("auto_boss_interval_min_h", "19") or 19)
            ax = float(get_setting("auto_boss_interval_max_h", "24") or 24)
            now = _t.time()
            for guild in list(bot.guilds):
                try:
                    cfg = guild_card_config_get(guild.id) or {}
                    ch_id = cfg.get("channel_id")
                    if not ch_id:
                        continue   # no card channel -> no opt-in
                    age = _dt.datetime.now(_dt.timezone.utc) - guild.created_at
                    if age < _dt.timedelta(days=min_days):
                        continue
                    if sum(1 for m in guild.members if not m.bot) < min_humans:
                        continue
                    if card_boss_guild_has_active(guild.id):
                        continue
                    nxt = boss_auto_get_next(guild.id)
                    if nxt is None:   # first schedule: plan inside the window, not right away
                        boss_auto_set_next(guild.id, now + _r.uniform(ih, ax) * 3600)
                        continue
                    if now < nxt:
                        continue
                    ch = guild.get_channel(int(ch_id))
                    if ch is None:
                        boss_auto_set_next(guild.id, now + _r.uniform(ih, ax) * 3600)
                        continue
                    uids = [str(m.id) for m in guild.members if not m.bot]
                    tier = _auto_boss_tier(avg_guild_level_for_users(uids))
                    # Secret avatar (the hardest) stays locked until the server's REAL
                    # average combat power reaches the threshold: otherwise capped at
                    # mythic. The tier is still driven by the guild levels.
                    secret_min = int(get_setting("auto_boss_secret_min_avg_power", "12000000") or 12000000)
                    avg_pow = avg_combat_power_for_users(uids)
                    mr = None if avg_pow >= secret_min else "mythic"
                    bid = await spawn_boss(bot, guild.id, int(ch_id), tier=tier, max_rarity=mr)
                    if bid:
                        print(f"[auto_boss] spawn guild={guild.id} tier={tier} avg_pow={avg_pow} secret={'yes' if mr is None else 'no(capped mythic)'}")
                    boss_auto_set_next(guild.id, now + _r.uniform(ih, ax) * 3600)
                except Exception as e:
                    print(f"[auto_boss] guild {getattr(guild, 'id', None)} err: {e!r}")
        except Exception as e:
            print(f"[auto_boss] loop err: {e!r}")

    @auto_boss_loop.before_loop
    async def _before_auto_boss_loop():
        await bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def card_render_bake_loop():
        """Dead-link repair: bake the missing local renders
        (cards added by import/suggestion whose image is still external)."""
        try:
            import asyncio as _aio
            from services.cards_overlay import bake_all_cards
            pub = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/") or None
            stats = await _aio.to_thread(bake_all_cards, False, pub, 8)
            if stats.get("updated"):
                print(f"[card_render_bake] {stats['updated']} render(s) baked")
        except Exception as e:
            print(f"[card_render_bake] err: {e!r}")

    @card_render_bake_loop.before_loop
    async def _before_card_render_bake():
        await bot.wait_until_ready()




    @topgg_stats_poster.before_loop
    async def _before_topgg():
        await bot.wait_until_ready()


    # ===== ERROR CAPTURE GLOBAL =====
    @bot.event
    async def on_error(event_method, *args, **kwargs):
        import traceback
        tb = traceback.format_exc()
        print(f"[on_error] event={event_method}\n{tb}")
        BOT_STATE["last_error"]    = f"{event_method}: {tb.splitlines()[-1] if tb else 'unknown'}"[:200]
        BOT_STATE["last_error_at"] = _time.time()

    @bot.tree.error
    async def _slash_cmd_error(interaction: discord.Interaction, error):
        """Global app_commands handler. top.gg requirement: clear error messages
        that name the missing permissions/roles precisely."""
        import traceback
        from discord import app_commands as _ac
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        print(f"[slash error] {tb_str}")
        BOT_STATE["last_error"]    = f"slash: {type(error).__name__}: {error}"[:200]
        BOT_STATE["last_error_at"] = _time.time()

        msg = None
        if isinstance(error, _ac.MissingPermissions):
            perms = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            msg = ti(interaction, "runtime.slash_error.user_missing_perms", perms=perms)
        elif isinstance(error, _ac.BotMissingPermissions):
            perms = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            msg = ti(interaction, "runtime.slash_error.bot_missing_perms", perms=perms)
        elif isinstance(error, _ac.CommandOnCooldown):
            msg = ti(interaction, "runtime.slash_error.cooldown",
                     seconds=int(error.retry_after))
        elif isinstance(error, _ac.MissingRole):
            msg = ti(interaction, "runtime.slash_error.missing_role",
                     role_id=error.missing_role)
        elif isinstance(error, _ac.NoPrivateMessage):
            msg = ti(interaction, "runtime.slash_error.no_private_message")
        elif isinstance(error, _ac.CheckFailure):
            msg = ti(interaction, "runtime.slash_error.check_failure")
        elif isinstance(error, _ac.CommandInvokeError):
            inner = error.original
            if isinstance(inner, discord.Forbidden):
                msg = ti(interaction, "runtime.slash_error.forbidden")
            elif isinstance(inner, discord.NotFound):
                msg = ti(interaction, "runtime.slash_error.not_found")
            else:
                msg = ti(interaction, "runtime.slash_error.internal",
                         error=type(inner).__name__)
        else:
            msg = ti(interaction, "runtime.slash_error.generic",
                     error=type(error).__name__)

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg[:1900], ephemeral=True)
            else:
                await interaction.response.send_message(msg[:1900], ephemeral=True)
        except Exception:
            pass


    # ===== WORKER: web commands -> bot (polling every 1.5s) =====
    @tasks.loop(seconds=1.5)
    async def process_bot_commands():
        pending = bot_command_fetch_pending(limit=10)
        if not pending:
            return
        for cmd in pending:
            try:
                await _dispatch_bot_command(cmd)
                bot_command_finish(cmd["id"], "done")
            except Exception as e:
                print(f"[bot_commands] error on {cmd['cmd']}: {e}")
                bot_command_finish(cmd["id"], "error", str(e)[:300])

    async def _dispatch_bot_command(cmd):
        gid = cmd["guild_id"]
        name = cmd["cmd"]
        payload = cmd.get("payload") or {}

        # NOTE: the "dm_send" command (sending a DM from the dashboard) was
        # removed on purpose (privacy reasons).

        # Handlers registered by the cogs (e.g. cards.py "post_trade")
        from services.bot_command_hooks import get as _get_hook
        _hook = _get_hook(name)
        if _hook:
            await _hook(bot, gid, payload)
            return

        guild = bot.get_guild(int(gid))
        if not guild:
            raise RuntimeError(f"guild {gid} not found (is the bot in that server?)")
        vc = guild.voice_client

        if name == "music_play":
            # payload: {query, voice_channel_id (optional)}
            if not _ensure_opus():
                raise RuntimeError("libopus is not loaded on the server")
            query = payload.get("query")
            if not query:
                raise ValueError("missing query")
            if not vc:
                ch_id = payload.get("voice_channel_id")
                if ch_id:
                    channel = guild.get_channel(int(ch_id))
                    if channel and isinstance(channel, discord.VoiceChannel):
                        vc = await channel.connect()
                        music_state_set(gid, voice_channel_id=str(channel.id), voice_channel_name=channel.name)
                    else:
                        raise ValueError("voice channel not found")
                else:
                    vchan = next((c for c in guild.voice_channels), None)
                    if not vchan:
                        raise ValueError("no voice channel available")
                    vc = await vchan.connect()
                    music_state_set(gid, voice_channel_id=str(vchan.id), voice_channel_name=vchan.name)
            # Multi-source detection: YouTube playlist, Spotify, or single track
            q_low = (query or "").lower()
            is_yt_playlist = ("youtube.com/playlist" in q_low or
                              ("list=" in q_low and ("youtube.com" in q_low or "youtu.be" in q_low)))
            is_spotify = "open.spotify.com/" in q_low

            if is_yt_playlist:
                from bot import get_playlist_info
                pl = await get_playlist_info(query, max_items=200)
                entries = pl.get("entries") or []
                for e in entries:
                    music_queue_add(gid,
                                    title=e["title"], url=e["url"],
                                    source_url=e.get("source_url"),
                                    duration=e.get("duration"),
                                    thumbnail=e.get("thumbnail"),
                                    requested_by="web")
            elif is_spotify:
                from services.spotify_resolver import resolve_spotify_url
                import asyncio as _aio
                sp = await _aio.to_thread(resolve_spotify_url, query, 50)
                for tm in (sp.get("tracks") or []):
                    if not tm.get("query"):
                        continue
                    try:
                        info = await get_audio_info(tm["query"])
                    except Exception as e:
                        print(f"[music web] spotify track fail {tm['query']!r}: {e}")
                        continue
                    music_queue_add(gid,
                                    title=info["title"], url=info["url"],
                                    source_url=info.get("source_url"),
                                    duration=info.get("duration"),
                                    thumbnail=info.get("thumbnail"),
                                    requested_by="web")
            else:
                info = await get_audio_info(query)
                music_queue_add(gid,
                                title=info["title"], url=info["url"],
                                source_url=info.get("source_url"),
                                duration=info.get("duration"),
                                thumbnail=info.get("thumbnail"),
                                requested_by="web")
            if not vc.is_playing():
                await play_next(vc, None, int(gid))

        elif name == "music_skip":
            if vc and vc.is_playing():
                vc.stop()

        elif name == "music_stop":
            music_queue_clear(gid)
            if vc:
                vc.stop()
            music_state_clear_current(gid)

        elif name == "music_pause":
            if vc and vc.is_playing():
                vc.pause()
                music_state_set(gid, is_paused=1, is_playing=0)

        elif name == "music_resume":
            if vc and vc.is_paused():
                vc.resume()
                music_state_set(gid, is_paused=0, is_playing=1)

        elif name == "music_join":
            ch_id = payload.get("voice_channel_id")
            if not ch_id:
                raise ValueError("missing voice_channel_id")
            channel = guild.get_channel(int(ch_id))
            if not channel:
                raise ValueError("voice channel not found")
            if vc:
                await vc.move_to(channel)
            else:
                await channel.connect()
            music_state_set(gid, voice_channel_id=str(channel.id), voice_channel_name=channel.name)

        elif name == "music_leave":
            if vc:
                music_queue_clear(gid)
                await vc.disconnect()
                music_state_disconnect(gid)

        elif name == "music_remove_track":
            from database import music_queue_remove
            track_id = payload.get("track_id")
            if track_id is not None:
                music_queue_remove(gid, track_id)

        elif name == "music_clear":
            music_queue_clear(gid)

        elif name == "music_volume":
            try:
                vol = max(0, min(200, int(payload.get("volume", 100))))
            except (TypeError, ValueError):
                vol = 100
            # Persisted through guild_setting (read on the next play)
            try:
                from database import guild_setting_set
                guild_setting_set(gid, "music_volume", str(vol / 100.0))
            except Exception:
                pass
            # Applied live when there is a voice client + a PCMVolumeTransformer source
            if vc and vc.source and hasattr(vc.source, "volume"):
                try:
                    vc.source.volume = vol / 100.0
                except Exception as e:
                    print(f"[music_volume] live apply err: {e}")

        elif name == "music_jump":
            from database import music_queue_list, music_queue_move_to_front, music_queue_pop_next
            try:
                pos = max(1, int(payload.get("position", 1)))
            except (TypeError, ValueError):
                pos = 1
            q = music_queue_list(gid) or []
            if pos > len(q):
                raise ValueError(f"position {pos} out of range")
            target = q[pos - 1]
            music_queue_move_to_front(gid, target["id"])
            if vc and vc.is_playing():
                vc.stop()  # play_next pops the new head of the queue

        elif name == "music_join":
            ch_id = payload.get("voice_channel_id")
            if not ch_id:
                raise ValueError("voice_channel_id is required")
            channel = guild.get_channel(int(ch_id))
            if not isinstance(channel, discord.VoiceChannel):
                raise ValueError("voice channel not found")
            from commandes.music_voice import connect_to_voice
            await connect_to_voice(bot, guild, channel)
            music_state_set(gid, voice_channel_id=str(channel.id),
                            voice_channel_name=channel.name)

        elif name in ("mod_kick", "mod_ban", "mod_timeout", "mod_unban"):
            target_id = payload.get("user_id")
            reason    = (payload.get("reason") or "Action from the dashboard").strip()
            if not target_id:
                raise ValueError("user_id is required")
            if name == "mod_unban":
                try:
                    user = await bot.fetch_user(int(target_id))
                    await guild.unban(user, reason=reason)
                except discord.NotFound:
                    raise RuntimeError("user is not banned or is unknown")
                return
            member = guild.get_member(int(target_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(target_id))
                except Exception:
                    raise RuntimeError("member not found in this server")
            duration_sec = None
            if name == "mod_kick":
                await member.kick(reason=reason)
                action_type = "kick"
            elif name == "mod_ban":
                delete_seconds = int(payload.get("delete_seconds", 0) or 0)
                await guild.ban(member, reason=reason, delete_message_seconds=delete_seconds)
                action_type = "ban"
            elif name == "mod_timeout":
                duration_min = int(payload.get("duration_minutes", 10) or 10)
                until = discord.utils.utcnow() + _dt.timedelta(minutes=duration_min)
                await member.timeout(until, reason=reason)
                action_type  = "timeout"
                duration_sec = duration_min * 60
            else:
                action_type = None
            # Bot-side log (general logs)
            add_log(guild.id, f"action_{name}",
                    user_id=target_id, username=str(member) if 'member' in dir() and member else target_id,
                    content=reason,
                    meta={"by": "dashboard"})
            # Store the sanction in mod_actions (modlogs history)
            if action_type:
                try:
                    from database import mod_action_add as _mod_add, mod_action_get as _mod_get, mod_config_get as _mod_cfg
                    aid = _mod_add(
                        guild.id, target_id, action_type,
                        reason=reason,
                        moderator_id=payload.get("moderator_id"),
                        duration_sec=duration_sec,
                    )
                    # Post in the modlog channel when configured
                    cfg = _mod_cfg(guild.id)
                    ch_id = cfg.get("modlog_channel_id")
                    if ch_id:
                        ch = guild.get_channel(int(ch_id))
                        if ch:
                            ad = _mod_get(aid) or {}
                            from commandes.moderation_pro import _build_action_embed as _bea
                            try:
                                await ch.send(embed=_bea(ad, member=member))
                            except Exception:
                                pass
                except Exception as _e:
                    print(f"[mod/dashboard-log] {type(_e).__name__}: {_e}")
            return

        elif name == "giveaway_post":
            from database import giveaway_get as _gw_get, giveaway_set_message_id as _gw_setmsg
            from commandes.giveaway import make_giveaway_embed, GiveawayJoinView
            gid_ = int(payload.get("giveaway_id") or 0)
            gw = _gw_get(gid_)
            if not gw:
                return
            ch = guild.get_channel(int(gw["channel_id"]))
            if not ch:
                raise RuntimeError("giveaway channel not found")
            embed = make_giveaway_embed(gw, participants_count=0)
            msg = await ch.send(embed=embed, view=GiveawayJoinView())
            _gw_setmsg(gid_, msg.id)
            return

        elif name == "giveaway_cancel_post":
            from database import giveaway_get as _gw_get
            from commandes.giveaway import GiveawayJoinView
            gid_ = int(payload.get("giveaway_id") or 0)
            gw = _gw_get(gid_)
            if not gw or not gw.get("message_id"):
                return
            ch = guild.get_channel(int(gw["channel_id"]))
            if not ch:
                return
            try:
                msg = await ch.fetch_message(int(gw["message_id"]))
                emb = msg.embeds[0] if msg.embeds else discord.Embed()
                emb.title = t("runtime.giveaway.cancelled_title",
                              guild_locale(guild.id) or "en", prize=gw["prize"])
                emb.color = 0xE74C3C
                view = GiveawayJoinView()
                for child in view.children:
                    child.disabled = True
                await msg.edit(embed=emb, view=view)
            except Exception as e:
                print(f"[giveaway/cancel-post] {type(e).__name__}: {e}")
            return

        elif name == "giveaway_reroll":
            from commandes.giveaway import reroll_giveaway
            gid_ = int(payload.get("giveaway_id") or 0)
            await reroll_giveaway(bot, gid_)
            return

        elif name == "mod_warn_followup":
            # The warn is already in the DB; here we DM the member + post the modlog + auto-timeout on threshold
            from database import (mod_action_get as _mod_get, mod_config_get as _mod_cfg,
                                  mod_action_count_active as _mod_count,
                                  mod_action_add as _mod_add)
            aid     = int(payload.get("action_id") or 0)
            uid     = str(payload.get("user_id") or "")
            mod_id  = str(payload.get("moderator_id") or "") or None
            reason  = payload.get("reason") or ""
            if not aid or not uid:
                return
            ad = _mod_get(aid) or {}
            member = guild.get_member(int(uid))
            if not member:
                try:
                    member = await guild.fetch_member(int(uid))
                except Exception:
                    member = None
            active = _mod_count(guild.id, uid, "warn")
            warn_lang = guild_locale(guild.id) or "en"
            # Modlog embed
            try:
                from commandes.moderation_pro import _build_action_embed as _bea
                embed = _bea(ad, member=member)
                cfg = _mod_cfg(guild.id)
                ch_id = cfg.get("modlog_channel_id")
                if ch_id:
                    ch = guild.get_channel(int(ch_id))
                    if ch:
                        embed.set_footer(text=t("runtime.modwarn.modlog_footer",
                                                warn_lang, count=active))
                        try: await ch.send(embed=embed)
                        except Exception: pass
            except Exception as _e:
                print(f"[mod/dashboard-warn] modlog err: {type(_e).__name__}")
            # DM the user
            if member:
                try:
                    dm = discord.Embed(
                        title=t("runtime.modwarn.dm_title", warn_lang, guild=guild.name),
                        description=t("runtime.modwarn.dm_desc", warn_lang,
                                      reason=reason or t("runtime.modwarn.no_reason", warn_lang),
                                      count=active),
                        color=0xF1C40F,
                    )
                    await member.send(embed=dm)
                except Exception:
                    pass
                # Auto-timeout
                cfg = _mod_cfg(guild.id)
                threshold = int(cfg.get("autotimeout_threshold") or 0)
                if threshold > 0 and active >= threshold:
                    duration_sec = int(cfg.get("autotimeout_duration") or 600)
                    try:
                        until = discord.utils.utcnow() + _dt.timedelta(seconds=duration_sec)
                        await member.timeout(until, reason=t(
                            "runtime.modwarn.autotimeout_reason", warn_lang,
                            count=active, threshold=threshold))
                        _mod_add(guild.id, uid, "timeout",
                                 reason=t("runtime.modwarn.autotimeout_log_reason",
                                          warn_lang, count=active),
                                 moderator_id=mod_id,
                                 duration_sec=duration_sec)
                    except Exception as _e:
                        print(f"[mod/dashboard-warn] auto-timeout err: {type(_e).__name__}")
            return

        elif name == "poll_send":
            # payload: {channel_id, question, options[], duration_hours}
            ch_id = payload.get("channel_id")
            question = (payload.get("question") or "").strip()
            options = [str(o).strip() for o in (payload.get("options") or []) if str(o).strip()]
            try:
                duration_h = max(1, min(168, int(payload.get("duration_hours", 24))))
            except (TypeError, ValueError):
                duration_h = 24
            if not ch_id or not question or len(options) < 2:
                raise ValueError("invalid poll payload")
            channel = guild.get_channel(int(ch_id))
            if not channel:
                raise ValueError("channel not found")
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError("the channel is not a text channel")
            poll = discord.Poll(question=question, duration=_dt.timedelta(hours=duration_h))
            for opt in options[:10]:
                poll.add_answer(text=opt[:55])
            await channel.send(poll=poll)
            return

        elif name == "bot_say":
            # payload: {channel_id, content, embed?}
            ch_id = payload.get("channel_id")
            content = (payload.get("content") or "").strip()
            embed_data = payload.get("embed")
            if not ch_id:
                raise ValueError("channel_id is required")
            if not content and not embed_data:
                raise ValueError("content or embed is required")
            channel = guild.get_channel(int(ch_id))
            if not channel:
                raise ValueError("channel not found")
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError("the channel is not a text channel")
            if len(content) > 2000:
                content = content[:1997] + "..."

            embed_obj = None
            if embed_data and isinstance(embed_data, dict):
                try:
                    color_hex = (embed_data.get("color") or "").lstrip("#")
                    color_int = int(color_hex, 16) if color_hex else None
                except ValueError:
                    color_int = None
                embed_obj = discord.Embed(
                    title=(embed_data.get("title") or None),
                    description=(embed_data.get("description") or None),
                    url=(embed_data.get("url") or None),
                    color=color_int,
                )
                author_name = embed_data.get("author_name")
                if author_name:
                    embed_obj.set_author(
                        name=author_name,
                        url=embed_data.get("author_url") or None,
                        icon_url=embed_data.get("author_icon") or None,
                    )
                footer_text = embed_data.get("footer_text")
                if footer_text:
                    embed_obj.set_footer(
                        text=footer_text,
                        icon_url=embed_data.get("footer_icon") or None,
                    )
                if embed_data.get("image"):
                    embed_obj.set_image(url=embed_data["image"])
                if embed_data.get("thumbnail"):
                    embed_obj.set_thumbnail(url=embed_data["thumbnail"])
                for f in (embed_data.get("fields") or [])[:25]:
                    fname  = (f.get("name")  or "").strip()
                    fvalue = (f.get("value") or "").strip()
                    if fname and fvalue:
                        embed_obj.add_field(
                            name=fname[:256],
                            value=fvalue[:1024],
                            inline=bool(f.get("inline")),
                        )
                if embed_data.get("timestamp"):
                    embed_obj.timestamp = discord.utils.utcnow()

            await channel.send(content=content or None, embed=embed_obj)

        elif name == "rolereaction_post":
            # payload: {channel_id, titre, description, mode, delivery, style, mappings:[{emoji_key, role_id, label}]}
            rr_lang  = guild_locale(guild.id) or "en"
            ch_id    = payload.get("channel_id")
            titre    = (payload.get("titre")
                        or t("runtime.rolereaction.default_title", rr_lang)).strip()
            descp    = (payload.get("description") or "").strip()
            mode     = payload.get("mode") or "toggle"
            delivery = payload.get("delivery") or "reaction"
            style    = payload.get("style") or "embed"
            mapps    = payload.get("mappings") or []
            # Normalize emojis coming from the web: strip zero-width + reroute
            for m in mapps:
                ek = m.get("emoji_key", "")
                ek_clean = _parse_emoji_input(ek, guild) if (guild and ek) else (ek or "").strip()
                if ek_clean:
                    m["emoji_key"] = ek_clean
            if not ch_id:
                raise ValueError("channel_id is required")
            if mode not in ("toggle", "add_only", "unique"):
                raise ValueError("invalid mode")
            if delivery not in ("reaction", "button"):
                raise ValueError("invalid delivery")
            if style not in ("embed", "text"):
                raise ValueError("invalid style")
            if not mapps:
                raise ValueError("at least 1 mapping is required")
            channel = guild.get_channel(int(ch_id))
            if not channel or not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError("text channel not found")

            use_buttons = delivery == "button"

            # Hierarchy check on every role
            me = guild.me
            too_high = []
            for m in mapps:
                r = guild.get_role(int(m["role_id"]))
                if not r:
                    raise ValueError(f"Role {m['role_id']} not found. A resync is required.")
                if r >= me.top_role:
                    too_high.append(r.name)
            if too_high:
                names = ", ".join(f"@{n}" for n in too_high)
                raise ValueError(
                    f"Hierarchy: the bot cannot grant these roles because they are "
                    f"above its own: {names}. "
                    f"Fix: go to Server Settings > Roles and drag the bot role "
                    f"ABOVE those roles."
                )

            color_int = 0xC8F050
            color_raw = payload.get("color")
            if color_raw:
                try:
                    color_int = (int(color_raw.replace("#", ""), 16)
                                 if isinstance(color_raw, str) else int(color_raw))
                except Exception:
                    pass

            footer = (t("runtime.rolereaction.footer_unique", rr_lang)
                      if mode == "unique"
                      else (t("runtime.rolereaction.footer_button", rr_lang)
                            if use_buttons
                            else t("runtime.rolereaction.footer_reaction", rr_lang)))

            def _line(m):
                ek = m.get("emoji_key") or ""
                label = (m.get("label") or "").strip()
                role_ref = f"<@&{m['role_id']}>"
                if label:
                    return f"{ek} **{label}** — {role_ref}".strip()
                return f"{ek} → {role_ref}".strip(" →") if not ek else f"{ek} → {role_ref}"

            # ----- content / embed -----
            content = None
            embed = None
            if style == "embed":
                embed = discord.Embed(title=titre, description=descp, color=color_int)
                if not use_buttons:
                    embed.add_field(name=t("runtime.rolereaction.field_reactions", rr_lang),
                                    value="\n".join(_line(m) for m in mapps), inline=False)
                embed.set_footer(text=footer)
            else:
                parts = []
                if titre:
                    parts.append(f"**{titre}**")
                if descp:
                    parts.append(descp)
                if not use_buttons:
                    parts.append("\n".join(_line(m) for m in mapps))
                parts.append(f"_{footer}_")
                content = "\n\n".join(p for p in parts if p)

            # ----- Button view -----
            view = None
            if use_buttons:
                view = discord.ui.View(timeout=None)
                for m in mapps:
                    ek = m.get("emoji_key") or ""
                    try:
                        emoji_obj = (discord.PartialEmoji.from_str(ek)
                                     if ek.startswith("<") else (ek or None))
                    except Exception:
                        emoji_obj = None
                    r = guild.get_role(int(m["role_id"]))
                    lbl = (m.get("label") or (r.name if r else
                           t("runtime.rolereaction.default_role_label", rr_lang)))[:80]
                    view.add_item(discord.ui.Button(
                        label=lbl, emoji=emoji_obj,
                        style=discord.ButtonStyle.secondary,
                        custom_id=f"rr:{m['role_id']}",
                    ))

            msg = await channel.send(content=content, embed=embed, view=view)

            failed_dispatch = []
            if not use_buttons:
                async def _try_add(emoji_str):
                    if emoji_str.startswith("<"):
                        await msg.add_reaction(discord.PartialEmoji.from_str(emoji_str))
                        return
                    base = emoji_str.replace("️", "")
                    seen = set()
                    last_err = None
                    for v in [emoji_str, base, base + "️"]:
                        if not v or v in seen:
                            continue
                        seen.add(v)
                        try:
                            await msg.add_reaction(v)
                            return
                        except discord.HTTPException as e:
                            last_err = e
                    if last_err:
                        raise last_err
                    raise RuntimeError("no emoji variant was tried")

                for m in mapps:
                    ek = m["emoji_key"]
                    try:
                        await _try_add(ek)
                    except Exception as e:
                        print(f"[rolereaction] dispatch add_reaction {ek!r} err: {e!r}")
                        failed_dispatch.append((ek, str(e)))
                    await asyncio.sleep(0.35)

            group_key = f"msg_{msg.id}" if mode == "unique" else None
            for idx, m in enumerate(mapps):
                # In button mode without an emoji, synthetic key to satisfy the PK
                ek = m.get("emoji_key") or f"btn_{m['role_id']}"
                db_rr_add(
                    guild.id, msg.id, channel.id, ek, m["role_id"],
                    mode=mode, group_key=group_key, created_by=payload.get("by"),
                    label=(m.get("label") or None), position=idx,
                    delivery=delivery, style=style,
                )
            if failed_dispatch:
                details = "; ".join(f"{ek}: {er}" for ek, er in failed_dispatch)
                raise RuntimeError(
                    f"Message posted (id {msg.id}), but {len(failed_dispatch)} reaction(s) "
                    f"could not be added: {details}."
                )
            return

        elif name == "custom_cmd_sync":
            # Resync the guild's custom slash commands (after a save/delete from the dashboard)
            from commandes.custom_cmd import sync_custom_commands_for_guild
            n = await sync_custom_commands_for_guild(bot, gid)
            print(f"[custom_cmd] resync {gid}: {n} command(s)")

        elif name == "guild_boost_activated_notify":
            # payload: {user_id} - notifies the admin channel that Guild Boost + was activated
            channel = _resolve_setup_channel(guild, "admin")
            if not channel:
                print(f"[gb-notify] {gid}: no writable channel found, skipping")
                return
            gb_lang = guild_locale(guild.id) or "en"
            uid = payload.get("user_id")
            mention = (f"<@{uid}>" if uid
                       else t("runtime.guildboost.member_fallback", gb_lang))
            embed = discord.Embed(
                title=t("runtime.guildboost.title", gb_lang),
                description=t("runtime.guildboost.description", gb_lang, mention=mention),
                color=0xB9F23A,
            )
            embed.set_footer(text=t("runtime.guildboost.footer", gb_lang))
            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"[gb-notify] send failed {gid}: {e}")

        elif name == "kofi_donation_notify":
            # payload: {donor_name, amount, currency, message, is_subscription, tier_name}
            # Notifies the #owner channel. The VIP/Super VIP role assignment is
            # handled directly by Ko-fi; the thank-you message in #soutien is
            # triggered by on_member_update when the role is added.
            kofi_lang = guild_locale(gid) or "en"
            donor = payload.get("donor_name") or t("runtime.kofi.anonymous", kofi_lang)
            amount = float(payload.get("amount") or 0)
            currency = payload.get("currency") or "EUR"
            don_msg = (payload.get("message") or "").strip()
            is_sub = bool(payload.get("is_subscription"))

            owner_chan_id = os.getenv("OWNER_NOTIFY_CHANNEL_ID", "1510454813492773046")
            owner_chan = None
            if owner_chan_id:
                try:
                    owner_chan = bot.get_channel(int(owner_chan_id))
                except (TypeError, ValueError):
                    owner_chan = None
            if owner_chan:
                kind = t("runtime.kofi.kind_subscription", kofi_lang) if is_sub \
                    else t("runtime.kofi.kind_donation", kofi_lang)
                desc = t("runtime.kofi.description", kofi_lang, donor=donor, kind=kind,
                         amount=f"{amount:.2f}", currency=currency)
                if don_msg:
                    desc += f"\n\n> {don_msg[:500]}"
                emb = discord.Embed(title=t("runtime.kofi.title", kofi_lang),
                                    description=desc, color=0xB9F23A)
                try:
                    await owner_chan.send(embed=emb)
                except Exception as e:
                    print(f"[kofi] owner notif failed: {e}")
            else:
                print("[kofi] OWNER_NOTIFY_CHANNEL_ID not configured or not found")

        elif name == "card_event_drop":
            from services.card_events import trigger_event_drop
            channel_id = payload.get("channel_id")
            min_rarity = (payload.get("min_rarity") or "rare").strip().lower()
            exact_rarity = bool(payload.get("exact_rarity"))
            card_id = payload.get("card_id") or None
            if not channel_id:
                raise ValueError("channel_id is required")
            result = await trigger_event_drop(
                bot, int(gid), int(channel_id),
                min_rarity=min_rarity, exact_rarity=exact_rarity,
                card_id=card_id, triggered_by="manual")
            if not result:
                raise RuntimeError("drop failed (card or channel not found)")
            return

        elif name == "list_guild_channels":
            # Retourne via bot_command_finish result_data
            channels = []
            for ch in guild.text_channels:
                try:
                    perm = ch.permissions_for(guild.me)
                    if perm.send_messages and perm.view_channel:
                        channels.append({
                            "id": str(ch.id), "name": ch.name,
                            "category": ch.category.name if ch.category else None,
                        })
                except Exception:
                    pass
            # Store in guild_channels_cache (cache table)
            from database import get_db
            conn = get_db(); c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS guild_channels_cache ("
                      "guild_id TEXT PRIMARY KEY, channels_json TEXT, "
                      "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            import json as _json
            c.execute("INSERT INTO guild_channels_cache (guild_id, channels_json, updated_at) "
                      "VALUES (?, ?, CURRENT_TIMESTAMP) "
                      "ON CONFLICT(guild_id) DO UPDATE SET "
                      "channels_json = excluded.channels_json, "
                      "updated_at = CURRENT_TIMESTAMP",
                      (str(gid), _json.dumps(channels)))
            conn.commit(); conn.close()
            return

        elif name == "boss_spawn":
            from services.card_boss import spawn_boss
            channel_id = payload.get("channel_id")
            try:
                tier = max(1, min(5, int(payload.get("tier") or 1)))
            except (ValueError, TypeError):
                tier = 1
            element = (payload.get("element") or "").strip() or None
            rarity = (payload.get("rarity") or "").strip().lower() or None
            if not channel_id:
                raise ValueError("channel_id is required")
            bid = await spawn_boss(bot, int(gid), int(channel_id), tier=tier,
                                   element=element, rarity=rarity)
            if not bid:
                raise RuntimeError("spawn failed (channel not found or no cards)")
            return

        elif name == "suggestion_resolved":
            # Approved/rejected reaction under the suggestion message (support channel)
            # + DM to the requester on rejection (forwarded message + reason).
            channel_id = payload.get("channel_id")
            message_id = payload.get("message_id")
            status = payload.get("status")
            suggester_id = payload.get("suggester_id")
            reason = payload.get("reason")
            msg = None
            if channel_id and message_id:
                ch = bot.get_channel(int(channel_id))
                if ch:
                    try:
                        msg = await ch.fetch_message(int(message_id))
                    except Exception as e:
                        print(f"[suggestion_resolved] fetch err: {e}")
            if msg:
                if status == "approved":
                    emo = discord.utils.get(bot.emojis, name="valide") or "✅"
                else:
                    emo = discord.utils.get(bot.emojis, name="refuse") or "❌"
                try:
                    await msg.add_reaction(emo)
                except Exception as e:
                    print(f"[suggestion_resolved] react err: {e}")
            if status == "rejected" and suggester_id and payload.get("dm", True):
                try:
                    sug_lang = guild_locale(gid) or "en"
                    user = bot.get_user(int(suggester_id)) or await bot.fetch_user(int(suggester_id))
                    if user:
                        txt = t("runtime.suggestion.rejected_one", sug_lang)
                        if reason:
                            txt += t("runtime.suggestion.reason_line", sug_lang, reason=reason)
                        embeds = msg.embeds[:1] if (msg and msg.embeds) else []
                        await user.send(content=txt, embeds=embeds)
                except Exception as e:
                    print(f"[suggestion_resolved] DM err: {e}")
            return

        elif name == "suggestion_bulk_dm":
            # A single DM when several suggestions from the same requester are rejected.
            suggester_id = payload.get("suggester_id")
            count = int(payload.get("count") or 0)
            reason = payload.get("reason")
            if not suggester_id or count <= 0:
                return
            try:
                bulk_lang = guild_locale(gid) or "en"
                user = bot.get_user(int(suggester_id)) or await bot.fetch_user(int(suggester_id))
                if user:
                    if count == 1:
                        txt = t("runtime.suggestion.rejected_one", bulk_lang)
                    else:
                        txt = t("runtime.suggestion.rejected_many", bulk_lang, count=count)
                    if reason:
                        txt += t("runtime.suggestion.reason_line", bulk_lang, reason=reason)
                    await user.send(content=txt)
            except Exception as e:
                print(f"[suggestion_bulk_dm] DM err: {e}")
            return

        elif name == "fake_drop":
            # Normal drop (image + code) but flagged fake_troll: on claim the bot
            # mocks the user and gives NOTHING (see handle_message_claim).
            from services.card_events import trigger_event_drop
            channel_id = payload.get("channel_id")
            card_id = payload.get("card_id")
            if not channel_id or not card_id:
                raise ValueError("channel_id + card_id are required")
            result = await trigger_event_drop(
                bot, int(gid), int(channel_id),
                card_id=int(card_id), triggered_by="fake_troll")
            if not result:
                raise RuntimeError("fake drop failed (channel/card not found)")
            return

        elif name == "simulate_roll":
            # Fake test roll: posts the /roll layout + pings the wishers, WITHOUT
            # adding the card to a collection (pure wishlist notification test).
            from commandes.cards import build_roll_embed
            from database import card_get, wishlist_users_for_card
            channel_id = payload.get("channel_id")
            card_id = payload.get("card_id")
            if not channel_id or not card_id:
                raise ValueError("channel_id + card_id are required")
            card = card_get(int(card_id))
            if not card:
                raise RuntimeError("card not found")
            channel = guild.get_channel(int(channel_id))
            if not channel:
                raise RuntimeError("channel not found")
            sim_lang = guild_locale(guild.id) or "en"
            embed, img_file, view = build_roll_embed(
                bot, card, t("runtime.simulate_roll.label", sim_lang))
            if img_file:
                await channel.send(embed=embed, file=img_file, view=view)
            else:
                await channel.send(embed=embed, view=view)
            try:
                wishers = wishlist_users_for_card(int(card_id)) or []
                mentions = [m.mention for m in
                            (guild.get_member(int(w)) for w in wishers[:50]) if m]
                if mentions:
                    await channel.send(t(
                        "runtime.simulate_roll.wishlist_ping", sim_lang,
                        mentions=" ".join(mentions), card=card["name"]))
            except Exception as e:
                print(f"[simulate_roll wish] {e}")
            return

        else:
            raise ValueError(f"unknown command: {name}")
