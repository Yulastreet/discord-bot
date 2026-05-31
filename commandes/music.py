"""Module musique en MAINTENANCE.

Toutes les commandes musique repondent avec un message de maintenance pendant
qu'on migre l'infra audio. Pas d'import wavelink/yt-dlp ici pour eviter les
crashs si les libs/Lavalink ne sont pas operationnels.

Quand on remet la musique en service :
- restaure depuis git history la version Lavalink/wavelink ou ffmpeg/yt-dlp
- ou edit ce fichier pour reactiver les vraies commandes
"""
import discord
from discord import app_commands


MAINTENANCE_MESSAGE = (
    "🛠️ **Fonctionnalité en maintenance**\n"
    "La musique est temporairement indisponible le temps qu'on retravaille l'infra audio.\n"
    "On revient vite, promis !"
)


def setup_music_commands(bot, deps):
    globals().update(deps)

    async def _maintenance(interaction: discord.Interaction):
        try:
            await interaction.response.send_message(MAINTENANCE_MESSAGE, ephemeral=True)
        except discord.InteractionResponded:
            try:
                await interaction.followup.send(MAINTENANCE_MESSAGE, ephemeral=True)
            except Exception:
                pass

    @bot.tree.command(name="join", description="(Maintenance) Rejoindre ton salon vocal")
    async def join(interaction: discord.Interaction):
        await _maintenance(interaction)

    @bot.tree.command(name="play", description="(Maintenance) Jouer une musique")
    @app_commands.describe(query="Titre ou lien YouTube")
    async def play(interaction: discord.Interaction, query: str):
        await _maintenance(interaction)

    @bot.tree.command(name="skip", description="(Maintenance) Passer a la musique suivante")
    async def skip(interaction: discord.Interaction):
        await _maintenance(interaction)

    @bot.tree.command(name="queue", description="(Maintenance) Voir la file d'attente musicale")
    async def queue_cmd(interaction: discord.Interaction):
        await _maintenance(interaction)

    @bot.tree.command(name="stop", description="(Maintenance) Stopper la musique et vider la file")
    async def stop(interaction: discord.Interaction):
        await _maintenance(interaction)

    @bot.tree.command(name="leave", description="(Maintenance) Quitter le salon vocal")
    async def leave(interaction: discord.Interaction):
        await _maintenance(interaction)

    async def _resume_music():
        """No-op pendant la maintenance."""
        print("[music] mode maintenance : pas de reprise de la file au boot")

    return _resume_music
