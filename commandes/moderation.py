import discord
from discord import app_commands


def setup_moderation_commands(bot):
    @bot.tree.command(name="kick", description="Expulser un membre")
    @app_commands.describe(membre="Le membre a expulser", raison="La raison")
    @app_commands.default_permissions(kick_members=True)
    async def kick(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison fournie"):
        await membre.kick(reason=raison)
        await interaction.response.send_message(f"**{membre.name}** a ete expulse. Raison : {raison}")

    @bot.tree.command(name="ban", description="Bannir un membre")
    @app_commands.describe(membre="Le membre a bannir", raison="La raison")
    @app_commands.default_permissions(ban_members=True)
    async def ban(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison fournie"):
        await membre.ban(reason=raison)
        await interaction.response.send_message(f"**{membre.name}** a ete banni. Raison : {raison}")

    @bot.tree.command(name="clear", description="Supprimer des messages")
    @app_commands.describe(nombre="Nombre de messages a supprimer")
    @app_commands.default_permissions(manage_messages=True)
    async def clear(interaction: discord.Interaction, nombre: int):
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.purge(limit=nombre)
        await interaction.followup.send(f"**{nombre}** messages supprimes.", ephemeral=True)

    @bot.tree.command(name="poll", description="Creer un sondage")
    @app_commands.describe(question="La question", options="Options separees par des virgules")
    async def poll(interaction: discord.Interaction, question: str, options: str):
        option_list = [o.strip() for o in options.split(",")]
        if len(option_list) < 2:
            await interaction.response.send_message("Donne au moins 2 options separees par des virgules.", ephemeral=True)
            return
        if len(option_list) > 9:
            await interaction.response.send_message("Maximum 9 options.", ephemeral=True)
            return
        emojis = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
        description = "\n".join([f"{emojis[i]}. {opt}" for i, opt in enumerate(option_list)])
        embed = discord.Embed(title=question, description=description, color=discord.Color.gold())
        embed.set_footer(text=f"Sondage cree par {interaction.user.name}")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        for i in range(len(option_list)):
            await msg.add_reaction(f"{emojis[i]}\ufe0f\u20e3")
