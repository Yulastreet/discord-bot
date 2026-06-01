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


def _is_soundcloud_url(query):
    if not isinstance(query, str):
        return False
    return "soundcloud.com/" in query.lower()


def _is_spotify_url(query):
    if not isinstance(query, str):
        return False
    return "open.spotify.com/" in query.lower()

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

            # --- Spotify (track / album / playlist) : resolve via API puis search YT ---
            if _is_spotify_url(query):
                await interaction.followup.send("🎧 Resolution Spotify en cours...")
                try:
                    from services.spotify_resolver import resolve_spotify_url
                    sp = await asyncio.to_thread(resolve_spotify_url, query, 50)
                except Exception as e:
                    print(f"[music spotify] error: {type(e).__name__}: {e}")
                    await interaction.followup.send(
                        "⚠️ Spotify n'est pas configure (clefs API manquantes) ou URL invalide.\n"
                        "Tu peux deja jouer SoundCloud, YouTube, Bandcamp via leur lien direct."
                    )
                    return
                tracks_meta = sp.get("tracks") or []
                if not tracks_meta:
                    await interaction.followup.send("❌ Aucune piste trouvee dans cette ressource Spotify.")
                    return
                added = 0
                for tm in tracks_meta:
                    q = tm.get("query")
                    if not q:
                        continue
                    try:
                        info = await get_audio_info(q)
                    except Exception as e:
                        print(f"[music spotify] yt search fail for {q!r}: {e}")
                        continue
                    music_queue_add(gid,
                                    title=info["title"], url=info["url"],
                                    source_url=info.get("source_url"),
                                    duration=info.get("duration"),
                                    thumbnail=info.get("thumbnail"),
                                    requested_by=interaction.user.id)
                    added += 1
                if added == 0:
                    await interaction.followup.send("❌ Aucune piste Spotify n'a pu etre matchee sur YouTube.")
                    return
                await interaction.followup.send(
                    f"🎧 **{added}** piste(s) ajoutee(s) depuis Spotify : **{sp.get('title','?')}**"
                )
                if vc.is_connected() and not vc.is_playing():
                    await play_next(vc, interaction.channel, interaction.guild.id)
                return

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

    @bot.tree.command(name="skip", description="Passer la musique en cours (ou jusqu'a une position de la file)")
    @app_commands.describe(position="Numero dans la file vers lequel sauter (1 = la prochaine). Vide = juste skip la courante.")
    async def skip(interaction: discord.Interaction, position: int = None):
        vc = interaction.guild.voice_client
        gid = str(interaction.guild.id)

        # Skip simple
        if position is None:
            if vc and vc.is_playing():
                vc.stop()
                await interaction.response.send_message("⏭️ Musique passée !")
            else:
                await interaction.response.send_message("❌ Aucune musique en cours !", ephemeral=True)
            return

        # Skip vers position N : pop (N-1) tracks silencieusement, puis stop la courante
        if position < 1:
            await interaction.response.send_message("❌ La position doit être >= 1.", ephemeral=True)
            return

        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message("❌ La file est vide.", ephemeral=True)
            return

        skipped_count = min(position - 1, len(q))
        # Pop silencieusement les tracks 1 a (position-1)
        for _ in range(skipped_count):
            music_queue_pop_next(gid)

        # Stop la courante pour declencher play_next sur ce qu'il reste
        was_playing = bool(vc and vc.is_playing())
        if vc and vc.is_playing():
            vc.stop()

        total_skipped = skipped_count + (1 if was_playing else 0)
        target_msg = ""
        if position > len(q):
            target_msg = f" (position {position} dépassait la file de {len(q)}, file vidée)"

        await interaction.response.send_message(
            f"⏭️ **{total_skipped}** musique(s) passée(s){target_msg}"
        )

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

    @bot.tree.command(name="volume", description="Regler le volume musique (0-200, default 100)")
    @app_commands.describe(niveau="Volume en pourcentage (0 = muet, 100 = normal, 200 = max)")
    async def volume(interaction: discord.Interaction, niveau: int):
        if niveau < 0 or niveau > 200:
            await interaction.response.send_message("❌ Volume entre 0 et 200.", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        vol_f = niveau / 100.0
        try:
            from database import guild_setting_set
            guild_setting_set(gid, "music_volume", str(vol_f))
        except Exception as e:
            print(f"[music /volume] persist error: {e}")
        # Applique a la lecture en cours si possible
        vc = interaction.guild.voice_client
        applied_live = False
        if vc and vc.source and hasattr(vc.source, "volume"):
            try:
                vc.source.volume = vol_f
                applied_live = True
            except Exception as e:
                print(f"[music /volume] live apply error: {e}")
        suffix = " (applique en direct)" if applied_live else " (prochaine piste)"
        await interaction.response.send_message(f"🔊 Volume : **{niveau}%**{suffix}")

    @bot.tree.command(name="remove", description="Retirer une piste de la file par sa position")
    @app_commands.describe(position="Position dans la file (1 = la prochaine)")
    async def remove(interaction: discord.Interaction, position: int):
        if position < 1:
            await interaction.response.send_message("❌ La position doit être >= 1.", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        q = music_queue_list(gid) or []
        if not q:
            await interaction.response.send_message("❌ La file est vide.", ephemeral=True)
            return
        if position > len(q):
            await interaction.response.send_message(
                f"❌ Position {position} hors limites (file = {len(q)} piste(s)).",
                ephemeral=True,
            )
            return
        track = q[position - 1]
        try:
            from database import music_queue_remove
            music_queue_remove(gid, track["id"])
        except Exception as e:
            print(f"[music /remove] error: {e}")
            await interaction.response.send_message(MUSIC_TROUBLE_MESSAGE, ephemeral=True)
            return
        await interaction.response.send_message(
            f"🗑️ Retire de la file : **{track['title'][:120]}** (position {position})"
        )

    @bot.tree.command(name="search", description="Chercher sur YouTube et choisir parmi 5 resultats")
    @app_commands.describe(query="Titre, artiste, ou mots-cles a chercher")
    async def search(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            results = await search_youtube(query, max_results=5)
        except Exception as e:
            print(f"[music /search] error: {type(e).__name__}: {e}")
            await interaction.followup.send(MUSIC_TROUBLE_MESSAGE)
            return
        if not results:
            await interaction.followup.send("❌ Aucun resultat YouTube.")
            return

        # Select menu Discord
        options = []
        for i, r in enumerate(results):
            label = (r.get("title") or "(sans titre)")[:95]
            dur = _fmt_duration(r.get("duration")) if r.get("duration") else "?"
            uploader = (r.get("uploader") or "")[:50]
            desc = f"{uploader} · {dur}"[:95]
            options.append(discord.SelectOption(label=label, value=str(i), description=desc))

        class SearchView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.chosen = False

            @discord.ui.select(placeholder="Choisis une piste...", options=options)
            async def pick(self, sel_inter: discord.Interaction, sel: discord.ui.Select):
                if sel_inter.user.id != interaction.user.id:
                    await sel_inter.response.send_message("❌ Seul l'auteur de /search peut choisir.", ephemeral=True)
                    return
                self.chosen = True
                idx = int(sel.values[0])
                chosen = results[idx]
                await sel_inter.response.defer()
                # Reuse logique /play
                try:
                    if not _ensure_opus():
                        await sel_inter.followup.send(MUSIC_TROUBLE_MESSAGE)
                        return
                    voice_channel = interaction.user.voice.channel if interaction.user.voice else None
                    if not voice_channel:
                        await sel_inter.followup.send("❌ Tu n'es plus dans un vocal.")
                        return
                    vc = await connect_to_voice(bot, interaction.guild, voice_channel)
                    gid = str(interaction.guild.id)
                    music_state_set(gid,
                                    voice_channel_id=str(voice_channel.id),
                                    voice_channel_name=voice_channel.name)
                    info = await get_audio_info(chosen["url"])
                    music_queue_add(gid,
                                    title=info["title"], url=info["url"],
                                    source_url=info.get("source_url"),
                                    duration=info.get("duration"),
                                    thumbnail=info.get("thumbnail"),
                                    requested_by=interaction.user.id)
                    await sel_inter.followup.send(f"✅ Ajoute : **{info['title']}**")
                    if vc.is_connected() and not vc.is_playing():
                        await play_next(vc, interaction.channel, interaction.guild.id)
                except Exception as e:
                    print(f"[music /search pick] error: {type(e).__name__}: {e}")
                    await sel_inter.followup.send(MUSIC_TROUBLE_MESSAGE)
                self.stop()

            async def on_timeout(self):
                if not self.chosen:
                    try:
                        await interaction.edit_original_response(
                            content="⌛ Choix expire (60s). Relance `/search`.",
                            view=None,
                        )
                    except Exception:
                        pass

        embed = discord.Embed(
            title=f"🔍 Resultats YouTube pour : {query[:80]}",
            color=discord.Color.blurple(),
        )
        for i, r in enumerate(results, 1):
            dur = _fmt_duration(r.get("duration")) if r.get("duration") else "?"
            embed.add_field(
                name=f"`{i}.` {(r.get('title') or '(sans titre)')[:80]}",
                value=f"{(r.get('uploader') or 'Inconnu')[:40]} · `{dur}`",
                inline=False,
            )
        await interaction.followup.send(embed=embed, view=SearchView())

    @bot.tree.command(name="pause", description="Mettre la musique en pause")
    async def pause(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.response.send_message("❌ Aucune musique en cours.", ephemeral=True)
            return
        vc.pause()
        music_state_set(str(interaction.guild.id), is_paused=1, is_playing=0)
        await interaction.response.send_message("⏸️ Musique en pause.")

    @bot.tree.command(name="resume", description="Reprendre la musique en pause")
    async def resume(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused():
            await interaction.response.send_message("❌ Aucune musique en pause.", ephemeral=True)
            return
        vc.resume()
        music_state_set(str(interaction.guild.id), is_paused=0, is_playing=1)
        await interaction.response.send_message("▶️ Lecture reprise.")

    @bot.tree.command(name="musicstats", description="Top tracks / top auditeurs de ce serveur")
    @app_commands.describe(periode="Periode en jours (1-365, default 30)")
    async def musicstats(interaction: discord.Interaction, periode: int = 30):
        if periode < 1 or periode > 365:
            await interaction.response.send_message("❌ Periode entre 1 et 365 jours.", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        try:
            from database import (music_stats_summary, music_stats_top_tracks,
                                  music_stats_top_requesters)
            summary = music_stats_summary(gid, periode)
            tops    = music_stats_top_tracks(gid, periode, 10)
            users   = music_stats_top_requesters(gid, periode, 5)
        except Exception as e:
            print(f"[music /musicstats] error: {e}")
            await interaction.response.send_message(MUSIC_TROUBLE_MESSAGE, ephemeral=True)
            return
        if not summary or not summary.get("total_plays"):
            await interaction.response.send_message(
                f"📊 Aucune lecture musicale sur les {periode} derniers jours."
            )
            return
        total_s = int(summary.get("total_seconds") or 0)
        h = total_s // 3600
        m = (total_s % 3600) // 60
        embed = discord.Embed(
            title=f"📊 Stats musique ({periode}j)",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Lectures", value=str(summary["total_plays"]), inline=True)
        embed.add_field(name="Pistes uniques", value=str(summary["unique_tracks"]), inline=True)
        embed.add_field(name="Auditeurs", value=str(summary["unique_users"]), inline=True)
        embed.add_field(name="Temps total", value=f"{h}h{m:02d}m", inline=True)
        by_src = summary.get("by_source") or []
        if by_src:
            embed.add_field(
                name="Par source",
                value="\n".join(f"`{s['source']}` : {s['plays']}" for s in by_src),
                inline=True,
            )
        if tops:
            top_lines = []
            for i, t in enumerate(tops, 1):
                title = (t["track_title"] or "?")[:60]
                top_lines.append(f"`{i}.` {title} · **{t['plays']}**")
            embed.add_field(name="🎵 Top pistes", value="\n".join(top_lines), inline=False)
        if users:
            user_lines = []
            for i, u in enumerate(users, 1):
                user_lines.append(f"`{i}.` <@{u['user_id']}> · **{u['plays']}**")
            embed.add_field(name="👤 Top auditeurs", value="\n".join(user_lines), inline=False)
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
