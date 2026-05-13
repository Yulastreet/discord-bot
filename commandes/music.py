import asyncio
import discord
from discord import app_commands

import yt_dlp

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
                await interaction.followup.send("❌ libopus introuvable sur le serveur. Installe-la (`apt install libopus0`) et redémarre le bot.")
                return
            channel = interaction.user.voice.channel
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.move_to(channel)
            else:
                await channel.connect()
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
        try:
            if not _ensure_opus():
                await interaction.followup.send("❌ libopus introuvable sur le serveur.")
                return
            if not interaction.guild.voice_client:
                await interaction.user.voice.channel.connect()
                music_state_set(str(interaction.guild.id),
                                voice_channel_id=str(interaction.user.voice.channel.id),
                                voice_channel_name=interaction.user.voice.channel.name)
            gid = str(interaction.guild.id)
            await interaction.followup.send(f"🔍 Recherche de **{query}**...")
            try:
                info = await get_audio_info(query)
            except Exception as e:
                print(f"[music] yt-dlp error: {e}")
                await interaction.followup.send(f"❌ Erreur lors de la recherche : {e}")
                return
            music_queue_add(gid,
                            title=info["title"], url=info["url"],
                            source_url=info.get("source_url"),
                            duration=info.get("duration"),
                            thumbnail=info.get("thumbnail"),
                            requested_by=interaction.user.id)
            await interaction.followup.send(f"✅ Ajouté à la file : **{info['title']}**")
            if not interaction.guild.voice_client.is_playing():
                await play_next(interaction.guild.voice_client, interaction.channel, interaction.guild.id)
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
                vc = await channel.connect()
                print(f"[resume] {guild.name} : reconnecté à {channel.name}")
                # Relancer la queue si non vide
                if music_queue_list(gid):
                    await play_next(vc, None, guild.id)
            except Exception as e:
                print(f"[resume] {guild.name} : échec reconnexion {channel.name}: {e}")
                music_state_disconnect(gid)

    return _resume_music
