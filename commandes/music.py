import asyncio
import discord
from discord import app_commands

import yt_dlp

from .music_voice import connect_to_voice


def _is_playlist_url(query):
    """True si l'URL est une playlist YouTube (contient list=...)."""
    if not isinstance(query, str) or not query.startswith("http"):
        return False
    q = query.lower()
    return ("youtube.com/playlist" in q or
            ("list=" in q and ("youtube.com" in q or "youtu.be" in q)))

# Message friendly pour tous les soucis musique. Les vraies erreurs sont
# loggees pour debug owner. L'utilisateur final voit juste un message
# rassurant + pas de stack trace cryptique.
MUSIC_TROUBLE_MESSAGE = (
    "⚠️ **Cette fonctionnalité a quelques soucis pour l'instant.**\n"
    "On s'en occupe ! Réessaie dans quelques minutes ou tente une autre vidéo."
)


def setup_music_commands(bot, deps):
    globals().update(deps)
    # ===== MUSIQUE =====

    def _ensure_opus():
        """Ensure libopus is loaded. Re-tries load if not. Returns True on success."""
        if discord.opus.is_loaded():
            return True
        return _load_opus()

    @bot.tree.command(name="join", description="Rejoindre ton salon vocal")
    async def join(interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if not _ensure_opus():
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                print("[music /join] libopus non charge")
                return
            channel = interaction.user.voice.channel
            await connect_to_voice(bot, interaction.guild, channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(channel.id),
                            voice_channel_name=channel.name)
            await interaction.followup.send(f"✅ Connecté à **{channel.name}** !")
        except Exception as e:
            import traceback
            print(f"[music /join] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)

    @bot.tree.command(name="play", description="Jouer une musique (lien ou titre, playlist YouTube supportee)")
    @app_commands.describe(query="Titre, lien vidéo ou lien playlist YouTube")
    async def play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if not _ensure_opus():
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                print("[music /play] libopus non charge")
                return
            voice_channel = interaction.user.voice.channel
            vc = await connect_to_voice(bot, interaction.guild, voice_channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(voice_channel.id),
                            voice_channel_name=voice_channel.name)
            gid = str(interaction.guild.id)

            # --- Playlist YouTube : on ajoute toutes les pistes ---
            if _is_playlist_url(query):
                await interaction.followup.send(f"🔍 Chargement de la playlist...")
                try:
                    pl = await get_playlist_info(query, max_items=50)
                except Exception as e:
                    print(f"[music] playlist error: {e}")
                    await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                    return
                entries = pl.get("entries") or []
                if not entries:
                    await interaction.followup.send("❌ Playlist vide ou inaccessible.")
                    return
                for entry in entries:
                    music_queue_add(gid,
                                    title=entry["title"],
                                    url=entry["url"],
                                    source_url=entry.get("source_url"),
                                    duration=entry.get("duration"),
                                    thumbnail=entry.get("thumbnail"),
                                    requested_by=interaction.user.id)
                await interaction.followup.send(
                    f"📋 **{len(entries)}** pistes ajoutées depuis **{pl.get('playlist_title','Playlist')}**"
                )
                if vc.is_connected() and not vc.is_playing():
                    await play_next(vc, interaction.channel, interaction.guild.id)
                return

            # --- Track unique (titre ou lien vidéo) ---
            await interaction.followup.send(f"🔍 Recherche de **{query}**...")
            try:
                info = await get_audio_info(query)
            except Exception as e:
                print(f"[music] yt-dlp error: {e}")
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
                return
            music_queue_add(gid,
                            title=info["title"], url=info["url"],
                            source_url=info.get("source_url"),
                            duration=info.get("duration"),
                            thumbnail=info.get("thumbnail"),
                            requested_by=interaction.user.id)
            await interaction.followup.send(f"✅ Ajouté à la file : **{info['title']}**")
            if vc.is_connected() and not vc.is_playing():
                await play_next(vc, interaction.channel, interaction.guild.id)
        except Exception as e:
            import traceback
            print(f"[music /play] error: {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
            except Exception:
                pass


    def _fmt_duration(seconds):
        if not seconds or seconds <= 0:
            return "—"
        s = int(seconds)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h}h{m:02d}m{sec:02d}s"
        return f"{m}:{sec:02d}"


    @bot.tree.command(name="nowplaying", description="Voir la musique en cours")
    async def nowplaying(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        st = music_state_get(gid) or {}
        title = st.get("current_title")
        if not title:
            await interaction.response.send_message("🔇 Aucune musique en cours.", ephemeral=True)
            return

        url = st.get("current_url") or ""
        duration = st.get("current_duration")
        thumb = st.get("current_thumbnail")
        started_at = st.get("started_at")
        is_paused = bool(st.get("is_paused"))
        voice_chan_name = st.get("voice_channel_name") or "?"

        # Calcule position si started_at present
        position_str = "—"
        progress_bar = ""
        if started_at:
            try:
                import datetime as _dt
                if "T" in started_at:
                    start_dt = _dt.datetime.fromisoformat(started_at)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=_dt.timezone.utc)
                    now = _dt.datetime.now(_dt.timezone.utc)
                    elapsed = int((now - start_dt).total_seconds())
                    if elapsed >= 0 and duration:
                        elapsed = min(elapsed, int(duration))
                        position_str = f"{_fmt_duration(elapsed)} / {_fmt_duration(duration)}"
                        # Progress bar 20 chars
                        ratio = elapsed / max(1, int(duration))
                        filled = int(ratio * 20)
                        progress_bar = "▰" * filled + "▱" * (20 - filled)
                    elif duration:
                        position_str = f"— / {_fmt_duration(duration)}"
            except Exception:
                pass
        if position_str == "—" and duration:
            position_str = _fmt_duration(duration)

        embed = discord.Embed(
            title=("⏸️ " if is_paused else "🎵 ") + title[:240],
            url=url if url.startswith("http") else None,
            color=discord.Color.blurple() if not is_paused else discord.Color.orange(),
        )
        if thumb:
            embed.set_thumbnail(url=thumb)
        embed.add_field(name="Durée", value=position_str, inline=True)
        embed.add_field(name="Salon vocal", value=f"🔊 {voice_chan_name}", inline=True)
        if progress_bar:
            embed.add_field(name="​", value=f"`{progress_bar}`", inline=False)

        # File suivante (3 premieres)
        try:
            q = music_queue_list(gid) or []
        except Exception:
            q = []
        if q:
            next_lines = []
            for i, t in enumerate(q[:3], 1):
                dur = _fmt_duration(t.get("duration")) if t.get("duration") else "—"
                next_lines.append(f"`{i}.` {t['title'][:60]} · `{dur}`")
            embed.add_field(
                name=f"🎶 À suivre ({len(q)} en file)",
                value="\n".join(next_lines) + (f"\n_… +{len(q)-3} autres_" if len(q) > 3 else ""),
                inline=False,
            )

        embed.set_footer(text="TookBot · /queue pour la file complète · /skip pour passer")
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="skip", description="Passer à la musique suivante")
    async def skip(interaction: discord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("⏭️ Musique passée !")
        else:
            await interaction.response.send_message("❌ Aucune musique en cours !", ephemeral=True)

    @bot.tree.command(name="queue", description="Voir la file d'attente musicale")
    async def queue_cmd(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        q = music_queue_list(gid)
        if not q:
            await interaction.response.send_message("📭 La file d'attente est vide !")
            return
        embed = discord.Embed(title="🎵 File d'attente", color=discord.Color.blurple())
        description = ""
        for i, t in enumerate(q):
            description += f"**{i+1}.** {t['title']}\n"
        embed.description = description
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="stop", description="Stopper la musique et vider la file")
    async def stop(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        music_queue_clear(gid)
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
        music_state_clear_current(gid)
        await interaction.response.send_message("⏹️ Musique stoppée et file vidée !")

    @bot.tree.command(name="leave", description="Quitter le salon vocal")
    async def leave(interaction: discord.Interaction):
        if interaction.guild.voice_client:
            gid = str(interaction.guild.id)
            music_queue_clear(gid)
            await interaction.guild.voice_client.disconnect()
            music_state_disconnect(gid)
            await interaction.response.send_message("👋 Déconnecté du salon vocal !")
        else:
            await interaction.response.send_message("❌ Je ne suis pas dans un salon vocal !", ephemeral=True)


    async def _resume_music():
        """Au boot, rejoindre les vocals connus + relancer la queue si elle n'est pas vide.
        Lit music_state pour chaque guild où le bot est présent."""
        from database import music_state_get, music_state_disconnect, music_queue_list
        for guild in bot.guilds:
            gid = str(guild.id)
            st = music_state_get(gid)
            if not st:
                continue
            ch_id = st.get("voice_channel_id")
            if not ch_id:
                continue
            # Le voice channel existe-t-il toujours ?
            channel = guild.get_channel(int(ch_id))
            if not channel or not isinstance(channel, discord.VoiceChannel):
                music_state_disconnect(gid)

                continue
            try:
                vc = await connect_to_voice(bot, guild, channel)
                print(f"[resume] {guild.name} : reconnecté à {channel.name}")
                # Relancer la queue si non vide
                if music_queue_list(gid):
                    await play_next(vc, None, guild.id)
            except Exception as e:
                print(f"[resume] {guild.name} : échec reconnexion {channel.name}: {e}")
                music_state_disconnect(gid)

    return _resume_music
