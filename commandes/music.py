import os
import asyncio
import datetime as _dt

import discord
from discord import app_commands

import wavelink


def setup_music_commands(bot, deps):
    globals().update(deps)
    # ===== MUSIQUE (Lavalink + wavelink) =====
    # Le moteur audio est Lavalink (serveur Java). wavelink est le client Python.
    # On garde la file et l'etat en DB SQLite pour que le dashboard web reste a jour.

    LAVALINK_HOST = os.getenv("LAVALINK_HOST", "127.0.0.1")
    LAVALINK_PORT = os.getenv("LAVALINK_PORT", "2333")
    LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
    LAVALINK_URI = f"http://{LAVALINK_HOST}:{LAVALINK_PORT}"

    def _node_connected():
        """True si au moins un node wavelink est en etat CONNECTED."""
        try:
            for node in wavelink.Pool.nodes.values():
                if node.status == wavelink.NodeStatus.CONNECTED:
                    return True
        except Exception:
            pass
        return False

    async def _connect_node():
        """Connecte le bot au node Lavalink et ATTEND qu'il soit CONNECTED.

        Pool.connect() retourne avant que la connexion websocket soit etablie ;
        on poll le status jusqu'a CONNECTED (sinon Playable.search leve
        InvalidNodeException : node pas pret).
        """
        if _node_connected():
            return True
        # Lance la connexion si aucun node n'existe encore dans le pool
        try:
            if not wavelink.Pool.nodes:
                node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
                await wavelink.Pool.connect(nodes=[node], client=bot)
        except Exception as e:
            print(f"[wavelink] Pool.connect erreur: {type(e).__name__}: {e}")
        # Attend la connexion effective (max ~15s)
        for _ in range(30):
            if _node_connected():
                bot._wavelink_ready = True
                print(f"[wavelink] node connecte ({LAVALINK_URI})")
                return True
            await asyncio.sleep(0.5)
        print("[wavelink] node pas CONNECTED apres attente. Lavalink lance ? Mot de passe correct ?")
        return False

    def _fmt_track(track):
        """Normalise une Playable wavelink vers le dict attendu par la DB."""
        return {
            "title":      track.title or "(sans titre)",
            "url":        track.uri or "",
            "source_url": track.uri or "",
            "duration":   int(track.length / 1000) if track.length else None,
            "thumbnail":  getattr(track, "artwork", None),
        }

    async def _get_player(guild, channel) -> wavelink.Player:
        """Retourne le Player wavelink pour cette guild, en se connectant au besoin."""
        vc = guild.voice_client
        if vc and isinstance(vc, wavelink.Player) and vc.connected:
            if vc.channel and vc.channel.id != channel.id:
                await vc.move_to(channel)
            return vc
        if vc:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        return await channel.connect(cls=wavelink.Player, self_deaf=True)

    async def _play_db_next(player: wavelink.Player, text_channel, guild_id):
        """Pop la prochaine piste de la file DB et la joue via Lavalink."""
        if not player or not player.connected:
            print(f"[music] _play_db_next: player non connecte (guild={guild_id})")
            return
        track_row = music_queue_pop_next(str(guild_id))
        if not track_row:
            music_state_clear_current(str(guild_id))
            if text_channel:
                try:
                    await text_channel.send("✅ File d'attente terminée !")
                except Exception:
                    pass
            return
        # Re-resout la piste via Lavalink (URL YouTube stockee en DB)
        try:
            results = await wavelink.Playable.search(track_row["url"])
            if not results:
                raise RuntimeError("introuvable via Lavalink")
            track = results[0] if isinstance(results, list) else results
            # Memorise le salon texte pour les notifs de fin de piste
            player._took_text_channel = text_channel
            await player.play(track)
            music_state_set(
                str(guild_id),
                current_title=track_row["title"],
                current_url=track_row["url"],
                current_thumbnail=track_row.get("thumbnail"),
                current_duration=track_row.get("duration"),
                is_playing=1, is_paused=0,
                started_at=_dt.datetime.utcnow().isoformat(timespec="seconds"),
            )
            if text_channel:
                try:
                    await text_channel.send(f"🎵 En cours : **{track_row['title']}**")
                except Exception:
                    pass
        except Exception as e:
            print(f"[music] _play_db_next error: {e}")
            # Piste KO : on tente la suivante
            await _play_db_next(player, text_channel, guild_id)

    # ----- Event : fin de piste -> jouer la suivante depuis la DB -----
    @bot.listen()
    async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
        player = payload.player
        reason = getattr(payload, "reason", "?")
        print(f"[wavelink] track_end reason={reason}")
        if not player:
            return
        guild_id = player.guild.id if player.guild else None
        if guild_id is None:
            return
        text_channel = getattr(player, "_took_text_channel", None)
        await _play_db_next(player, text_channel, guild_id)

    # ----- Event : exception piste (load/play KO) -----
    @bot.listen()
    async def on_wavelink_track_exception(payload):
        print(f"[wavelink] track_exception: {getattr(payload, 'exception', payload)!r}")

    @bot.listen()
    async def on_wavelink_track_stuck(payload):
        print(f"[wavelink] track_stuck: {payload!r}")

    @bot.listen()
    async def on_wavelink_node_ready(payload):
        bot._wavelink_ready = True
        print(f"[wavelink] node ready: {getattr(payload.node, 'uri', '?')}")

    # Expose les helpers au worker dashboard (tasks/runtime.py _dispatch_bot_command)
    bot._wl_connect_node = _connect_node
    bot._wl_get_player = _get_player
    bot._wl_play_db_next = _play_db_next

    # ===================== COMMANDES =====================

    @bot.tree.command(name="join", description="Rejoindre ton salon vocal")
    async def join(interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()
        if not await _connect_node():
            await interaction.followup.send("❌ Le serveur audio (Lavalink) est indisponible. Réessaie dans un instant.")
            return
        try:
            channel = interaction.user.voice.channel
            await _get_player(interaction.guild, channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(channel.id),
                            voice_channel_name=channel.name)
            await interaction.followup.send(f"✅ Connecté à **{channel.name}** !")
        except Exception as e:
            import traceback
            print(f"[music /join] error: {e}")
            traceback.print_exc()
            await interaction.followup.send(f"❌ Erreur connexion vocal : {type(e).__name__} — {e}")

    @bot.tree.command(name="play", description="Jouer une musique")
    @app_commands.describe(query="Titre ou lien YouTube")
    async def play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
            return
        await interaction.response.defer()
        if not await _connect_node():
            await interaction.followup.send("❌ Le serveur audio (Lavalink) est indisponible. Réessaie dans un instant.")
            return
        try:
            voice_channel = interaction.user.voice.channel
            player = await _get_player(interaction.guild, voice_channel)
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(voice_channel.id),
                            voice_channel_name=voice_channel.name)
            gid = str(interaction.guild.id)
            await interaction.followup.send(f"🔍 Recherche de **{query}**...")

            # Recherche via Lavalink. Si pas une URL, recherche YouTube.
            # Pas de prefix "ytsearch:" : wavelink 3.x prefix automatiquement
            # avec son default (ytmsearch). Ajouter "ytsearch:" produit
            # "ytmsearch:ytsearch:..." qui ne matche rien.
            search_query = query
            try:
                results = await wavelink.Playable.search(search_query)
            except Exception as e:
                print(f"[music] wavelink search error: {e}")
                await interaction.followup.send(f"❌ Erreur lors de la recherche : {e}")
                return
            if not results:
                await interaction.followup.send("❌ Aucun résultat trouvé.")
                return

            # Playlist -> on prend la 1ere piste (comportement actuel : noplaylist)
            track = results[0] if isinstance(results, list) else results.tracks[0]
            info = _fmt_track(track)
            music_queue_add(gid,
                            title=info["title"], url=info["url"],
                            source_url=info.get("source_url"),
                            duration=info.get("duration"),
                            thumbnail=info.get("thumbnail"),
                            requested_by=interaction.user.id)
            await interaction.followup.send(f"✅ Ajouté à la file : **{info['title']}**")
            if player.connected and not player.playing:
                await _play_db_next(player, interaction.channel, interaction.guild.id)
        except Exception as e:
            import traceback
            print(f"[music /play] error: {e}")
            traceback.print_exc()
            try:
                await interaction.followup.send(f"❌ Erreur interne : {type(e).__name__} — {e}")
            except Exception:
                pass

    @bot.tree.command(name="skip", description="Passer à la musique suivante")
    async def skip(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and isinstance(vc, wavelink.Player) and vc.playing:
            await vc.stop()  # declenche on_wavelink_track_end -> piste suivante
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
        vc = interaction.guild.voice_client
        if vc and isinstance(vc, wavelink.Player):
            await vc.stop()
        music_state_clear_current(gid)
        await interaction.response.send_message("⏹️ Musique stoppée et file vidée !")

    @bot.tree.command(name="leave", description="Quitter le salon vocal")
    async def leave(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and isinstance(vc, wavelink.Player):
            gid = str(interaction.guild.id)
            music_queue_clear(gid)
            await vc.disconnect()
            music_state_disconnect(gid)
            await interaction.response.send_message("👋 Déconnecté du salon vocal !")
        else:
            await interaction.response.send_message("❌ Je ne suis pas dans un salon vocal !", ephemeral=True)

    async def _resume_music():
        """Au boot : connecte le node Lavalink, rejoint les vocals connus,
        relance la file si non vide. Lit music_state pour chaque guild."""
        await _connect_node()
        for guild in bot.guilds:
            gid = str(guild.id)
            st = music_state_get(gid)
            if not st:
                continue
            ch_id = st.get("voice_channel_id")
            if not ch_id:
                continue
            channel = guild.get_channel(int(ch_id))
            if not channel or not isinstance(channel, discord.VoiceChannel):
                music_state_disconnect(gid)
                continue
            try:
                player = await _get_player(guild, channel)
                print(f"[resume] {guild.name} : reconnecté à {channel.name}")
                if music_queue_list(gid):
                    await _play_db_next(player, None, guild.id)
            except Exception as e:
                print(f"[resume] {guild.name} : échec reconnexion {channel.name}: {e}")
                music_state_disconnect(gid)

    return _resume_music
