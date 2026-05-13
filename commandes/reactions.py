import discord
from discord import app_commands

from database import remove_reaction, set_reaction


def setup_reaction_commands(bot, user_reactions):
    @bot.tree.command(name="reaction_add", description="Ajouter une reaction automatique a un membre")
    @app_commands.describe(membre="Le membre cible", emoji="L'emoji a utiliser")
    @app_commands.checks.has_permissions(administrator=True)
    async def reaction_add(interaction: discord.Interaction, membre: discord.Member, emoji: str):
        gid = str(interaction.guild.id)
        user_reactions[(gid, membre.id)] = emoji
        set_reaction(gid, membre.id, emoji)
        await interaction.response.send_message(
            f"Le bot reagira avec {emoji} aux messages de **{membre.name}** sur ce serveur."
        )

    @reaction_add.error
    async def reaction_add_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("Permission administrateur requise.", ephemeral=True)

    @bot.tree.command(name="reaction_remove", description="Supprimer la reaction automatique d'un membre")
    @app_commands.describe(membre="Le membre dont supprimer la reaction")
    @app_commands.checks.has_permissions(administrator=True)
    async def reaction_remove(interaction: discord.Interaction, membre: discord.Member):
        gid = str(interaction.guild.id)
        key = (gid, membre.id)
        if key in user_reactions:
            del user_reactions[key]
            remove_reaction(gid, membre.id)
            await interaction.response.send_message(
                f"Reaction supprimee pour **{membre.name}** sur ce serveur."
            )
        else:
            await interaction.response.send_message(
                f"Aucune reaction configuree pour **{membre.name}** sur ce serveur.",
                ephemeral=True,
            )

    @reaction_remove.error
    async def reaction_remove_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("Permission administrateur requise.", ephemeral=True)

    @bot.tree.command(name="reaction_list", description="Voir les reactions automatiques actives")
    async def reaction_list(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        guild_reactions = {uid: emo for (g, uid), emo in user_reactions.items() if g == gid}
        if not guild_reactions:
            await interaction.response.send_message(
                "Aucune reaction automatique configuree sur ce serveur.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(title="Reactions automatiques actives", color=discord.Color.orange())
        for user_id, emoji in guild_reactions.items():
            membre = interaction.guild.get_member(user_id)
            nom = membre.name if membre else f"Inconnu ({user_id})"
            embed.add_field(name=nom, value=emoji, inline=True)
        await interaction.response.send_message(embed=embed)
