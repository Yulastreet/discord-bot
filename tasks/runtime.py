import asyncio
import collections as _col
import datetime as _dt
import os
import time as _time

import discord
from discord import app_commands
from discord.ext import tasks


# Cooldown anti-spam XP : (guild_id, user_id) -> ts du dernier gain
_XP_LAST_GAIN: dict = {}


def setup_runtime(bot, deps):
    globals().update(deps)
    @bot.event
    async def on_ready():
        print(f"✅ Bot connecté en tant que {bot.user}")
        # Enregistrer chaque guild où le bot est présent + sync ses channels
        for guild in bot.guilds:
            upsert_guild(
                guild.id, guild.name,
                icon_url=str(guild.icon.url) if guild.icon else None,
                member_count=guild.member_count or 0
            )
            try:
                _sync_guild_channels(guild)
            except Exception as e:
                print(f"[channels] sync {guild.name} échoué : {e}")
            try:
                _sync_guild_roles(guild)
            except Exception as e:
                print(f"[roles] sync {guild.name} échoué : {e}")
            try:
                _sync_guild_members(guild)
            except Exception as e:
                print(f"[members] sync {guild.name} échoué : {e}")
        print(f"👀 {len(USER_REACTIONS)} réaction(s) chargée(s) sur {len(bot.guilds)} serveur(s)")
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
        # CS2 queue sweep (filet de securite si on_voice_state_update manque un event)
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
        # Sync entitlements existants (achats faits avant que le bot soit en ligne)
        try:
            count = 0
            async for ent in bot.entitlements(exclude_ended=True):
                upsert_entitlement(_entitlement_to_dict(ent))
                count += 1
            print(f"[entitlement] sync boot : {count} actif(s) charge(s)")
        except Exception as e:
            print(f"[entitlement] sync boot error : {e!r}")
        # Purge les commandes per-guild orphelines (duplication possible si
        # une ancienne version syncait global + per-guild sans copy_global_to,
        # ce qui faisait fire chaque handler 2 fois).
        for guild in bot.guilds:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
            except Exception as e:
                print(f"[sync] clear guild {guild.name} fail : {e}")
        await bot.tree.sync()
        print("✅ Slash commands synchronisées globalement")

        # Enregistre les commandes custom (per-guild slash commands) apres
        # le sync global, sinon le clear ci-dessus les efface.
        from commandes.custom_cmd import sync_custom_commands_for_guild
        for guild in bot.guilds:
            try:
                n = await sync_custom_commands_for_guild(bot, guild.id)
                if n:
                    print(f"[custom_cmd] {guild.name}: {n} commandes custom enregistrées")
            except Exception as e:
                print(f"[custom_cmd] sync boot {guild.name} fail : {e}")

    def _sync_guild_roles(guild):
        """Pousse les roles d'une guild dans la table guild_roles (cache pour
        les pickers du dashboard)."""
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

    def _sync_guild_channels(guild):
        """Pousse les salons d'un guild dans la table guild_channels."""
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
                     member_count=guild.member_count or 0)
        _sync_guild_channels(guild)
        # Sync global uniquement (per-guild sync sans copy_global_to creait
        # un double jeu de commandes -> handlers fire 2 fois)
        try:
            await bot.tree.sync()
        except Exception:
            pass

        # DM presentation au membre qui a invite le bot (fallback : owner)
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

        if inviter is not None and not inviter.bot:
            embed = discord.Embed(
                title="👋 Merci de m'avoir invité !",
                description=(
                    f"Salut **{inviter.display_name}** ! Je suis maintenant sur **{guild.name}**.\n\n"
                    "Je propose un système de **niveaux/XP**, des **duels** PvP fun, "
                    "des outils de **modération**, des **giveaways**, des **commandes custom**, "
                    "de l'intégration **CS2** (stats, inventaire, prix, queue), et plus encore.\n\n"
                    "🛠️ **Pour découvrir toutes les commandes** : `/commandes`\n"
                    "⚔️ **Pour de l'aide sur les duels** : `/duel info`\n\n"
                    "⭐ Le **système d'XP** est **activé par défaut** sur ton serveur. "
                    "Si tu préfères ne pas l'utiliser, désactive-le avec `/xp off` "
                    "ou depuis le dashboard web (les niveaux acquis restent conservés). "
                    "Réactive avec `/xp on`.\n\n"
                    "Pense à régler les permissions du bot et à configurer "
                    "les options dans le **dashboard web** si tu en as un."
                ),
                color=0xB9F23A,
            )
            embed.set_footer(text=f"Serveur : {guild.name} • {guild.member_count or 0} membre(s)")
            if guild.icon:
                embed.set_thumbnail(url=str(guild.icon.url))
            try:
                await inviter.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

            # === Second message TRES VISIBLE pour forcer le /setup ===
            embed_setup = discord.Embed(
                title="⚠️ NE PAS SKIP — Initialisation requise",
                description=(
                    "**Avant d'utiliser le bot**, un **admin ou modérateur** du serveur "
                    "doit faire la commande :\n\n"
                    "## `/setup`\n\n"
                    "Cette commande ouvre un **builder de configuration** où tu choisis "
                    "les 4 salons essentiels du bot :\n\n"
                    "📥 **Bienvenue** — messages d'arrivée des nouveaux membres\n"
                    "📜 **Logs** — historique d'activité (commandes, modération)\n"
                    "🔴 **Alertes** — notifications Twitch / YouTube / Reddit\n"
                    "🛡️ **Admin/Modo** — notifications internes du staff\n\n"
                    "Sans cette étape, certaines fonctionnalités ne pourront pas "
                    "envoyer leurs messages au bon endroit.\n\n"
                    "💡 *Tu peux refaire `/setup` à tout moment pour modifier la config.*"
                ),
                color=0xFF6B35,
            )
            embed_setup.set_footer(text="Étape obligatoire — Configuration initiale")
            try:
                await inviter.send(embed=embed_setup)
            except (discord.Forbidden, discord.HTTPException):
                # Fallback : essaye d'envoyer dans le premier channel writable
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
                        await target.send(embed=embed_setup)
                except Exception:
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
            print(f"[roles] sync on create : {e}")


    @bot.event
    async def on_guild_role_update(before, after):
        try:
            _sync_guild_roles(after.guild)
        except Exception as e:
            print(f"[roles] sync on update : {e}")


    @bot.event
    async def on_guild_role_delete(role):
        try:
            _sync_guild_roles(role.guild)
        except Exception as e:
            print(f"[roles] sync on delete : {e}")


    # ===== LOGS — capture des events =====
    @bot.event
    async def on_app_command_completion(interaction: discord.Interaction, command):
        if not interaction.guild:
            return
        # Reconstruire la liste d'arguments lisibles
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
            content=message.content or "(message vide ou attachement seul)",
        )

    @bot.event
    async def on_message_edit(before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot:
            return
        if (before.content or "") == (after.content or ""):
            return  # juste un embed/preview qui se met a jour
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
        if before.channel == after.channel:
            return  # mute/deaf etc., pas de move
        if before.channel is None and after.channel is not None:
            # join
            add_log(member.guild.id, "action_voice_join",
                    user_id=member.id, username=str(member),
                    channel_id=after.channel.id, channel_name=after.channel.name)
        elif before.channel is not None and after.channel is None:
            # leave
            add_log(member.guild.id, "action_voice_leave",
                    user_id=member.id, username=str(member),
                    channel_id=before.channel.id, channel_name=before.channel.name)
        else:
            # move
            add_log(member.guild.id, "action_voice_move",
                    user_id=member.id, username=str(member),
                    channel_id=after.channel.id, channel_name=after.channel.name,
                    meta={"from": before.channel.name, "to": after.channel.name})

    @bot.event
    async def on_member_remove(member):
        if not member.guild:
            return
        add_log(member.guild.id, "action_member_leave",
                user_id=member.id, username=str(member),
                content=f"a quitté **{member.guild.name}**")
        try:
            remove_member(member.guild.id, member.id)
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

    @bot.tree.command(name="sync", description="sync les slash commands manuellement (owner uniquement)")
    @commands.is_owner()
    async def sync_commands(ctx):
        """Resync les slash commands manuellement (owner uniquement)."""
        # Purge per-guild orphelines puis sync global
        for guild in bot.guilds:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
            except Exception:
                pass
        await bot.tree.sync()
        await ctx.send("✅ Slash commands resynchronisées !")

    @tasks.loop(seconds=5)
    async def reload_reactions():
        USER_REACTIONS.clear()
        USER_REACTIONS.update(get_all_reactions_index())

    _PLATFORM_DEFAULT_MSG = {
        "youtube": "📺 **{author}** a publié une nouvelle vidéo : **{title}**\n{url}",
        "reddit":  "🟠 Nouveau post de **{target}** : **{title}**\n{url}",
        "twitch":  "🔴 **{target}** est en LIVE — *{title}*\n🎮 {game} · 👀 {viewers} viewers\n{url}",
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
                            custom_template: str | None) -> tuple[str | None, discord.Embed]:
        """Construit (content, embed) selon plateforme. Si l'user a defini un
        message custom, on l'utilise comme content au-dessus de l'embed (ping-friendly)."""
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
            embed.title = title or f"{target_label} est en live !"
            embed.url = url
            embed.description = f"🔴 **{target_label}** est en LIVE !"
            if item.get("game"):
                embed.add_field(name="🎮 Jeu", value=str(item["game"]), inline=True)
            if item.get("viewers") is not None:
                embed.add_field(name="👀 Viewers", value=f"{item['viewers']:,}".replace(",", " "), inline=True)
            thumb = item.get("thumb")
            if thumb:
                embed.set_image(url=thumb)
            embed.set_author(name=f"{target_label} · Twitch",
                             url=f"https://twitch.tv/{target_label}",
                             icon_url=_PLATFORM_ICON["twitch"])

        elif platform == "youtube":
            embed.title = title or "Nouvelle vidéo"
            embed.url = url
            embed.description = f"📺 **{author}** a publié une nouvelle vidéo."
            # Miniature YouTube depuis videoId
            vid = item.get("id")
            if vid:
                embed.set_image(url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
            embed.set_author(name=f"{author} · YouTube",
                             url=url, icon_url=_PLATFORM_ICON["youtube"])

        elif platform == "reddit":
            embed.title = title or "Nouveau post Reddit"
            embed.url = url
            embed.description = f"🟠 Nouveau post de **{target_label}**."
            embed.set_author(name=f"{target_label} · Reddit",
                             url=url, icon_url=_PLATFORM_ICON["reddit"])

        else:
            embed.title = title or "Nouveau contenu"
            embed.url = url
            embed.description = f"Nouveau contenu de **{target_label}**."

        embed.timestamp = _dt.datetime.now(_dt.timezone.utc)
        return content, embed


    @tasks.loop(minutes=5)
    async def social_alerts_poll():
        """Poll toutes les alertes sociales actives. Compare avec last_seen_id ;
        poste les nouveautes dans le salon configure."""
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
                    # Premiere fois -> ecrit le marqueur pour le prochain poll
                    if not alert.get("last_seen_id") and alert["platform"] != "twitch":
                        # Tente une lecture pour seed last_seen_id
                        seed = await social.check_platform(
                            alert["platform"], alert["target_id"], "__seed__",
                        )
                        # check_platform avec last_seen_id non vide retourne [] vu qu'il
                        # cherche le marqueur. On prend la 1ere video courante via
                        # un seed manuel : recharge la page brute
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

                template = alert.get("message_template")  # peut etre None
                target_label = alert.get("target_label") or alert["target_id"]
                for item in new_items:
                    if item.get("_silent"):
                        # Twitch passe offline : on update juste le marqueur
                        social_alert_update_seen(alert["id"], item["id"])
                        continue

                    content, embed = _build_social_embed(
                        alert["platform"], target_label, item, template,
                    )
                    sent_ok = False
                    try:
                        await channel.send(content=content, embed=embed)
                        sent_ok = True
                    except discord.Forbidden:
                        print(f"[social] forbidden post #{alert['channel_id']} alert={alert['id']} — re-tente au prochain poll")
                    except Exception as e:
                        print(f"[social] send err alert={alert['id']}: {e!r} — re-tente au prochain poll")
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
        # Fallback : refait la requete brute pour recuperer la 1ere entry
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
        """Rotation Battle Pass (toutes les 6h) :
        - Genere les BG saisonniers du mois courant si absents
        - A partir du 25 du mois, pre-genere ceux du mois suivant
        - Cree la saison du mois courant via get_or_create_current_season()
          (qui seed pass_rewards + sabres auto)
        """
        import datetime as _dt
        import os as _os, subprocess as _sp
        try:
            # Saison courante (creee si manquante, seed auto)
            season = get_or_create_current_season()
            mk = season["month_key"]

            # Genere les BG saisonniers du mois courant si manquants
            seasonal_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                         "assets", "niveau_bg", "seasonal", mk)
            if not _os.path.isdir(seasonal_dir) or len(_os.listdir(seasonal_dir)) < 5:
                script = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                       "scripts", "generate_seasonal_backgrounds.py")
                if _os.path.exists(script):
                    _sp.run(["python3", script, mk], check=False)
                    print(f"[pass rotation] BGs generated for {mk}")

            # A partir du 25 du mois, pre-genere ceux du mois suivant pour anticiper
            now = _dt.datetime.utcnow()
            if now.day >= 25:
                if now.month == 12:
                    next_mk = f"{now.year + 1}-01"
                else:
                    next_mk = f"{now.year}-{now.month + 1:02d}"
                next_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                         "assets", "niveau_bg", "seasonal", next_mk)
                if not _os.path.isdir(next_dir) or len(_os.listdir(next_dir)) < 5:
                    script = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                           "scripts", "generate_seasonal_backgrounds.py")
                    if _os.path.exists(script):
                        _sp.run(["python3", script, next_mk], check=False)
                        print(f"[pass rotation] BGs pre-generated for {next_mk}")
        except Exception as e:
            print(f"[pass rotation] error: {e!r}")


    @pass_rotation_loop.before_loop
    async def _before_pass_rotation():
        await bot.wait_until_ready()


    @tasks.loop(hours=24)
    async def daily_logs_purge():
        """Purge quotidienne: > 90 jours OU > 5000 logs par guild. Recupere espace disque via VACUUM."""
        try:
            keep = max(100, int(get_setting("log_keep_per_guild") or "5000"))
            age  = max(7,   int(get_setting("log_retention_days")  or "90"))
            res = prune_logs_global(keep_per_guild=keep, max_age_days=age)
            if res["by_age"] or res["by_count"]:
                print(f"[purge] logs : -{res['by_age']} (age) -{res['by_count']} (count) + VACUUM")
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
                content=f"a rejoint **{member.guild.name}**")
        # Update member cache
        try:
            upsert_member(member.guild.id, member.id, str(member),
                          avatar_url=str(member.display_avatar.url) if member.display_avatar else None,
                          is_bot=member.bot, joined_at=member.joined_at)
        except Exception:
            pass

        data = get_welcome(member.guild.id)
        if not data:
            return
        channel = bot.get_channel(data["channel_id"])
        if channel:
            template = data.get("message") or guild_setting_get(str(member.guild.id), "welcome_template", DEFAULT_WELCOME_MESSAGE)
            try:
                send_kwargs = build_welcome_send_kwargs(template, member)
            except Exception:
                send_kwargs = {"content": f"Bienvenue {member.mention} !"}
            await channel.send(**send_kwargs)
            return
    # ===== MONETIZATION : entitlements Discord =====

    def _entitlement_to_dict(ent) -> dict:
        """Convertit un objet Entitlement (discord.py) en dict propre pour la DB."""
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
        """Convertit un emoji de RawReactionActionEvent en string canonique :
        - Unicode emoji  -> caractere brut (ex: '🟢')
        - Custom emoji   -> '<:name:id>' ou '<a:name:id>' si anime
        """
        e = payload_emoji
        if e.id:
            prefix = "a" if e.animated else ""
            return f"<{prefix}:{e.name}:{e.id}>"
        return e.name


    @bot.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == bot.user.id:
            return
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
            # Mode 'unique' : on retire les autres roles du meme group_key avant
            if mapping.get("mode") == "unique" and mapping.get("group_key"):
                others = db_rr_list_unique(payload.guild_id, payload.message_id, mapping["group_key"])
                for o in others:
                    if o["emoji"] == emoji_key:
                        continue
                    other_role = guild.get_role(int(o["role_id"]))
                    if other_role and other_role in member.roles:
                        await member.remove_roles(other_role, reason="ReactionRole unique group")
                    # Retire aussi sa reaction sur le msg
                    try:
                        channel = guild.get_channel(payload.channel_id)
                        msg = await channel.fetch_message(payload.message_id)
                        await msg.remove_reaction(o["emoji"], member)
                    except Exception:
                        pass
            if role not in member.roles:
                await member.add_roles(role, reason=f"ReactionRole {emoji_key}")
        except discord.Forbidden:
            print(f"[rolereaction] Forbidden : pas la perm pour role {role.id} sur {guild.id}")
        except Exception as e:
            print(f"[rolereaction] add error: {e!r}")


    @bot.event
    async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id == bot.user.id:
            return
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
            print(f"[rolereaction] Forbidden : pas la perm pour role {role.id} sur {guild.id}")
        except Exception as e:
            print(f"[rolereaction] remove error: {e!r}")


    @bot.event
    async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command):
        """Slash command terminee avec succes -> +1 use_commands quete Pass."""
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


    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        if message.guild is None:
            # DM (user -> bot) : on ne stocke plus rien (raison vie privee).
            # On laisse juste passer process_commands au cas ou.
            await bot.process_commands(message)
            return
        guild_id_str = str(message.guild.id)

        # Réactions automatiques per-guild
        key = (guild_id_str, message.author.id)
        if key in USER_REACTIONS:
            try:
                await message.add_reaction(USER_REACTIONS[key])
            except discord.HTTPException as e:
                print(f"❌ Erreur réaction : {e}")

        # XP per-guild (skip si admin du serveur a desactive via /xp off ou dashboard)
        if not message.author.bot and guild_setting_get(guild_id_str, "xp_enabled", "1") == "1":
            # Cooldown anti-spam : evite que spammer un salon ne grind l'XP
            try:
                cooldown_sec = max(0, int(guild_setting_get(guild_id_str, "xp_cooldown_seconds", "30")))
            except (TypeError, ValueError):
                cooldown_sec = 30
            cd_key = (guild_id_str, message.author.id)
            now_ts = _time.time()
            last_ts = _XP_LAST_GAIN.get(cd_key, 0)
            if cooldown_sec > 0 and (now_ts - last_ts) < cooldown_sec:
                await bot.process_commands(message)
                return
            _XP_LAST_GAIN[cd_key] = now_ts

            xp = get_xp(guild_id_str, message.author.id)
            old_level = get_level(xp)
            try:
                xp_min = max(0, int(guild_setting_get(guild_id_str, "xp_min", "1")))
                xp_max = max(xp_min, int(guild_setting_get(guild_id_str, "xp_max", "5")))
            except (TypeError, ValueError):
                xp_min, xp_max = 1, 5
            xp_gain = random.randint(xp_min, xp_max)
            # Boost XP Pass (recompense palier) — multiplicateur s'applique sur le gain
            try:
                boost = get_active_xp_boost_multiplier(message.author.id)
                if boost > 1.0:
                    xp_gain = int(xp_gain * boost)
            except Exception:
                pass
            xp += xp_gain
            set_xp(guild_id_str, message.author.id, xp, username=message.author.name)
            new_level = get_level(xp)
            try:
                _track_pass_quest(message.author.id, "send_messages", 1)
                _track_pass_quest(message.author.id, "earn_xp", xp_gain)
            except Exception:
                pass

            if new_level > old_level:
                level, progress_xp, needed_xp, percent = get_progress(xp)
                # Premium ? Carte HD avec son background choisi
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
                        print(f"[levelup premium] render error: {e!r} — fallback")
                        image = await generate_levelup_card(message.author, new_level, percent)
                else:
                    image = await generate_levelup_card(message.author, new_level, percent)
                await message.channel.send(
                    content=f"🎉 {message.author.mention}",
                    file=discord.File(image, filename="levelup.png")
                )
        await bot.process_commands(message)


    # ===== ANTI-SPAM SLASH COMMANDS =====
    # Limite : max N commandes par user dans une fenetre glissante
    import collections as _col
    _USER_CMD_TIMES = _col.defaultdict(list)  # user_id -> [timestamp, ...]
    _RATE_LIMIT_N      = 6        # 6 commandes
    _RATE_LIMIT_WINDOW = 30.0     # par 30 secondes

    def _is_rate_limited(user_id):
        now = _time.time()
        bucket = _USER_CMD_TIMES[user_id]
        # purge entries hors fenetre
        cutoff = now - _RATE_LIMIT_WINDOW
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= _RATE_LIMIT_N:
            return True, _RATE_LIMIT_WINDOW - (now - bucket[0])
        bucket.append(now)
        return False, 0.0

    async def _global_rate_limit(interaction: discord.Interaction) -> bool:
        """Refuse les interactions si l'user spam les slash commands."""
        # Bypass pour le proprietaire (toujours autorise)
        try:
            if await bot.is_owner(interaction.user):
                return True
        except Exception:
            pass
        limited, retry = _is_rate_limited(interaction.user.id)
        if limited:
            try:
                await interaction.response.send_message(
                    f"⏱️ Tu envoies des commandes trop vite. Réessaie dans **{int(retry) + 1}s**.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    # Hook global rate-limit sur l'arbre des slash commands
    bot.tree.interaction_check = _global_rate_limit

    @tasks.loop(seconds=30)
    async def rotate_presence():
        """Cycle Discord Activity through 4 messages."""
        statuses = [
            (discord.ActivityType.listening, "/play 🎵"),
            (discord.ActivityType.playing,   "/commandes pour la liste"),
            (discord.ActivityType.watching,  f"{len(bot.guilds)} serveur(s)"),
            (discord.ActivityType.playing,   "tookbot.click"),
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
        """Purge les buckets vides toutes les 10 min (sinon dict grossit indefiniment)."""
        now = _time.time()
        cutoff = now - _RATE_LIMIT_WINDOW
        to_del = [uid for uid, ts in _USER_CMD_TIMES.items() if not ts or ts[-1] < cutoff]
        for uid in to_del:
            _USER_CMD_TIMES.pop(uid, None)

    @anti_spam_cleanup.before_loop
    async def _before_spam_cleanup():
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
        import traceback
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        print(f"[slash error] {tb_str}")
        BOT_STATE["last_error"]    = f"slash: {type(error).__name__}: {error}"[:200]
        BOT_STATE["last_error_at"] = _time.time()
        try:
            msg = f"❌ Erreur : {type(error).__name__}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


    # ===== WORKER : commandes web -> bot (polling 1.5s) =====
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

        # NOTE : la commande "dm_send" (envoi de DM via dashboard) a ete
        # retiree volontairement (raison vie privee).

        guild = bot.get_guild(int(gid))
        if not guild:
            raise RuntimeError(f"guild {gid} introuvable (bot pas dans ce serveur ?)")
        vc = guild.voice_client

        if name == "music_play":
            # payload: {query, voice_channel_id (optional)}
            if not _ensure_opus():
                raise RuntimeError("libopus pas chargee sur le serveur")
            query = payload.get("query")
            if not query:
                raise ValueError("query manquant")
            if not vc:
                ch_id = payload.get("voice_channel_id")
                if ch_id:
                    channel = guild.get_channel(int(ch_id))
                    if channel and isinstance(channel, discord.VoiceChannel):
                        vc = await channel.connect()
                        music_state_set(gid, voice_channel_id=str(channel.id), voice_channel_name=channel.name)
                    else:
                        raise ValueError("salon vocal introuvable")
                else:
                    # Premier salon vocal disponible
                    vchan = next((c for c in guild.voice_channels), None)
                    if not vchan:
                        raise ValueError("aucun salon vocal disponible")
                    vc = await vchan.connect()
                    music_state_set(gid, voice_channel_id=str(vchan.id), voice_channel_name=vchan.name)
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
                vc.stop()  # triggers play_next via after callback

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
                raise ValueError("voice_channel_id manquant")
            channel = guild.get_channel(int(ch_id))
            if not channel:
                raise ValueError("salon vocal introuvable")
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

        elif name in ("mod_kick", "mod_ban", "mod_timeout", "mod_unban"):
            target_id = payload.get("user_id")
            reason    = (payload.get("reason") or "Action depuis le dashboard").strip()
            if not target_id:
                raise ValueError("user_id requis")
            if name == "mod_unban":
                try:
                    user = await bot.fetch_user(int(target_id))
                    await guild.unban(user, reason=reason)
                except discord.NotFound:
                    raise RuntimeError("user non banni ou inconnu")
                return
            member = guild.get_member(int(target_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(target_id))
                except Exception:
                    raise RuntimeError("membre introuvable sur ce serveur")
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
            # Log côté bot (logs generaux)
            add_log(guild.id, f"action_{name}",
                    user_id=target_id, username=str(member) if 'member' in dir() and member else target_id,
                    content=reason,
                    meta={"by": "dashboard"})
            # Enregistre la sanction dans mod_actions (historique modlogs)
            if action_type:
                try:
                    from database import mod_action_add as _mod_add, mod_action_get as _mod_get, mod_config_get as _mod_cfg
                    aid = _mod_add(
                        guild.id, target_id, action_type,
                        reason=reason,
                        moderator_id=payload.get("moderator_id"),
                        duration_sec=duration_sec,
                    )
                    # Post dans le salon modlog si configure
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
                raise RuntimeError("salon giveaway introuvable")
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
                emb.title = f"❌ Giveaway annulé · {gw['prize']}"
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
            # Le warn est deja en DB, ici on DM le membre + post modlog + auto-timeout si seuil
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
            # Modlog embed
            try:
                from commandes.moderation_pro import _build_action_embed as _bea
                embed = _bea(ad, member=member)
                cfg = _mod_cfg(guild.id)
                ch_id = cfg.get("modlog_channel_id")
                if ch_id:
                    ch = guild.get_channel(int(ch_id))
                    if ch:
                        embed.set_footer(text=f"Warns actifs : {active}")
                        try: await ch.send(embed=embed)
                        except Exception: pass
            except Exception as _e:
                print(f"[mod/dashboard-warn] modlog err: {type(_e).__name__}")
            # DM utilisateur
            if member:
                try:
                    dm = discord.Embed(
                        title=f"⚠️ Avertissement reçu sur **{guild.name}**",
                        description=f"**Raison :** {reason or 'sans raison'}\n\nTu as **{active}** warn(s) actif(s).",
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
                        await member.timeout(until, reason=f"Auto-timeout : {active} warns (seuil {threshold})")
                        _mod_add(guild.id, uid, "timeout",
                                 reason=f"Auto-timeout après {active} warns",
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
                raise ValueError("payload poll invalide")
            channel = guild.get_channel(int(ch_id))
            if not channel:
                raise ValueError("salon introuvable")
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError("le salon n'est pas textuel")
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
                raise ValueError("channel_id requis")
            if not content and not embed_data:
                raise ValueError("content ou embed requis")
            channel = guild.get_channel(int(ch_id))
            if not channel:
                raise ValueError("salon introuvable")
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError("le salon n'est pas textuel")
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
            # payload: {channel_id, titre, description, mode, mappings:[{emoji_key, role_id}]}
            ch_id   = payload.get("channel_id")
            titre   = (payload.get("titre") or "Choisis ton rôle").strip()
            descp   = (payload.get("description") or "").strip()
            mode    = payload.get("mode") or "toggle"
            mapps   = payload.get("mappings") or []
            # Normalise les emojis recus du web : strip zero-width + reroute
            # via _parse_emoji_input pour gerer aussi ":name:" -> custom emoji.
            for m in mapps:
                ek = m.get("emoji_key", "")
                ek_clean = _parse_emoji_input(ek, guild) if guild else ek.strip()
                if ek_clean:
                    m["emoji_key"] = ek_clean
            if not ch_id:
                raise ValueError("channel_id requis")
            if mode not in ("toggle", "add_only", "unique"):
                raise ValueError("mode invalide")
            if not mapps:
                raise ValueError("au moins 1 mapping requis")
            channel = guild.get_channel(int(ch_id))
            if not channel or not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError("salon textuel introuvable")

            # Verif hierarchie sur tous les roles
            me = guild.me
            too_high = []
            for m in mapps:
                r = guild.get_role(int(m["role_id"]))
                if not r:
                    raise ValueError(f"Rôle {m['role_id']} introuvable. Resync nécessaire.")
                if r >= me.top_role:
                    too_high.append(r.name)
            if too_high:
                names = ", ".join(f"@{n}" for n in too_high)
                raise ValueError(
                    f"Hiérarchie : le bot ne peut pas attribuer ces rôles car ils sont "
                    f"au-dessus du sien : {names}. "
                    f"Solution : va dans Paramètres du serveur → Rôles et glisse "
                    f"le rôle du bot AU-DESSUS de ces rôles."
                )

            # Couleur custom (defaut: vert lime)
            color_int = 0xC8F050
            color_raw = payload.get("color")
            if color_raw:
                try:
                    if isinstance(color_raw, str):
                        color_int = int(color_raw.replace("#", ""), 16)
                    else:
                        color_int = int(color_raw)
                except Exception:
                    pass
            embed = discord.Embed(title=titre, description=descp, color=color_int)
            lines = []
            for m in mapps:
                ek = m["emoji_key"]
                label = (m.get("label") or "").strip()
                role_ref = f"<@&{m['role_id']}>"
                if label:
                    lines.append(f"{ek} **{label}** — {role_ref}")
                else:
                    lines.append(f"{ek} → {role_ref}")
            embed.add_field(name="Réactions disponibles", value="\n".join(lines), inline=False)
            embed.set_footer(text=(
                "Tu ne peux choisir qu'UN seul rôle parmi ceux-ci."
                if mode == "unique" else
                "Réagis pour recevoir le rôle correspondant."
            ))

            msg = await channel.send(embed=embed)
            # Reactions avec retry VS-16. Cette fois on RAISE en cas d'echec
            # final pour que le caller catche et logue / surface l'erreur.
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
                raise RuntimeError("aucune variante d'emoji testee")

            failed_dispatch = []
            for m in mapps:
                ek = m["emoji_key"]
                # Log codepoints pour diagnostic 'Unknown Emoji'
                cps = " ".join(f"U+{ord(c):04X}" for c in ek)
                print(f"[rolereaction] add_reaction emoji={ek!r} codepoints=[{cps}]")
                try:
                    await _try_add(ek)
                except Exception as e:
                    print(f"[rolereaction] dispatch add_reaction {ek!r} err: {e!r}")
                    failed_dispatch.append((ek, str(e)))
                await asyncio.sleep(0.35)

            group_key = f"msg_{msg.id}" if mode == "unique" else None
            for idx, m in enumerate(mapps):
                db_rr_add(
                    guild.id, msg.id, channel.id, m["emoji_key"], m["role_id"],
                    mode=mode, group_key=group_key, created_by=payload.get("by"),
                    label=(m.get("label") or None), position=idx,
                )
            if failed_dispatch:
                details = "; ".join(f"{ek}: {er}" for ek, er in failed_dispatch)
                raise RuntimeError(
                    f"Message posté (id {msg.id}), mais {len(failed_dispatch)} réaction(s) "
                    f"n'ont pas pu être ajoutées : {details}. "
                    f"Vérifie que le bot a la permission « Ajouter des réactions » dans ce salon, "
                    f"et que les emojis sont bien des emojis Discord standards (pas des emojis "
                    f"d'un autre serveur où le bot n'est pas)."
                )
            return

        elif name == "custom_cmd_sync":
            # Resync les commandes custom slash de la guild (apres save/delete depuis dashboard)
            from commandes.custom_cmd import sync_custom_commands_for_guild
            n = await sync_custom_commands_for_guild(bot, gid)
            print(f"[custom_cmd] resync {gid}: {n} commandes")

        elif name == "guild_boost_activated_notify":
            # payload: {user_id} — notifie dans le salon admin que Guild Boost + a ete active
            admin_ch_id = guild_setting_get(gid, "setup_admin_channel_id", "")
            if not admin_ch_id:
                print(f"[gb-notify] {gid}: pas de salon admin configure, skip")
                return
            try:
                channel = guild.get_channel(int(admin_ch_id))
            except (TypeError, ValueError):
                channel = None
            if not channel:
                print(f"[gb-notify] {gid}: salon admin introuvable")
                return
            uid = payload.get("user_id")
            mention = f"<@{uid}>" if uid else "Un membre"
            embed = discord.Embed(
                title="🛡️ Guild Boost + activé sur ce serveur",
                description=(
                    f"{mention} vient d'activer **Guild Boost +** sur ce serveur. "
                    "Les fonctionnalités suivantes sont maintenant **débloquées** :\n\n"
                    "**⚙️ Commandes custom** — `/<nom>` directes\n"
                    "Crée tes commandes depuis le dashboard : "
                    "`dashboard.tookbot.click/custom-commands`\n\n"
                    "**🔔 Alertes Twitch / YouTube / Reddit**\n"
                    "Configure les alertes par salon avec : `/socialalert add`\n"
                    "Salon par défaut : celui configuré via `/setup` (Alertes).\n\n"
                    "**🎟️ Système de tickets**\n"
                    "Crée un panneau de tickets avec : `/ticket`\n"
                    "Gestion complète : claim, transcripts, modlog dédié.\n\n"
                    "**📜 Logs étendus**\n"
                    "Consulte l'historique du serveur sur le dashboard : "
                    "`dashboard.tookbot.click/logs`\n\n"
                    "💡 *Pour gérer/désactiver une fonctionnalité : "
                    "`dashboard.tookbot.click/features`*"
                ),
                color=0xB9F23A,
            )
            embed.set_footer(text="Guild Boost + — Merci de soutenir TookBot !")
            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"[gb-notify] envoi fail {gid}: {e}")

        else:
            raise ValueError(f"commande inconnue: {name}")
