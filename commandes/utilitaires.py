import discord
from discord import app_commands


def setup_utility_commands(bot):
    @bot.tree.command(name="ping", description="Voir la latence du bot")
    async def ping(interaction: discord.Interaction):
        latence = round(bot.latency * 1000)
        await interaction.response.send_message(f"Pong ! Latence : **{latence}ms**")

    @bot.tree.command(name="userinfo", description="Infos sur un membre")
    @app_commands.describe(membre="Le membre dont tu veux voir les infos")
    async def userinfo(interaction: discord.Interaction, membre: discord.Member = None):
        membre = membre or interaction.user
        embed = discord.Embed(title=f"Infos de {membre.name}", color=membre.color)
        embed.set_thumbnail(url=membre.display_avatar.url)
        embed.add_field(name="Nom", value=membre.name)
        embed.add_field(name="ID", value=membre.id)
        embed.add_field(name="Compte cree le", value=membre.created_at.strftime("%d/%m/%Y"))
        embed.add_field(name="A rejoint le", value=membre.joined_at.strftime("%d/%m/%Y"))
        embed.add_field(name="Role principal", value=membre.top_role.mention)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="serverinfo", description="Infos sur le serveur")
    async def serverinfo(interaction: discord.Interaction):
        serveur = interaction.guild
        embed = discord.Embed(title=f"Infos de {serveur.name}", color=discord.Color.blue())
        embed.set_thumbnail(url=serveur.icon.url if serveur.icon else None)
        embed.add_field(name="Proprietaire", value=serveur.owner)
        embed.add_field(name="Membres", value=serveur.member_count)
        embed.add_field(name="Cree le", value=serveur.created_at.strftime("%d/%m/%Y"))
        embed.add_field(name="Salons", value=len(serveur.channels))
        embed.add_field(name="Roles", value=len(serveur.roles))
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="avatar", description="Afficher l'avatar d'un membre")
    @app_commands.describe(membre="Le membre dont tu veux voir l'avatar")
    async def avatar(interaction: discord.Interaction, membre: discord.Member = None):
        membre = membre or interaction.user
        embed = discord.Embed(title=f"Avatar de {membre.name}", color=discord.Color.blue())
        embed.set_image(url=membre.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="commandes", description="Recevoir la liste des commandes en MP")
    async def commandes(interaction: discord.Interaction):
        embed = discord.Embed(
            title="Liste des commandes",
            description="Toutes les commandes disponibles, par categorie.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Moderation",
            value=(
                "**/clear <nombre>**\n"
                "**/kick <membre> [raison]**\n"
                "**/ban <membre> [raison]**\n"
                "**/poll <question> <option1> <option2>**\n"
                "**/setwelcome [salon]**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Outils serveur",
            value=(
                "**/reaction_add <membre> <emoji>**\n"
                "**/reaction_remove <membre>**\n"
                "**/reaction_list**\n"
                "**/rolereaction create**\n"
                "**/socialalert add/list/remove**\n"
                "**/ticket**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Fun",
            value=(
                "**/8ball**, **/de**, **/coinflip**, **/blague**, **/ship**, "
                "**/choix**, **/random**, **/qui**, **/clap**, **/rate**, "
                "**/citation**, **/zgeg**"
            ),
            inline=False,
        )
        embed.add_field(name="Niveaux & XP", value="**/niveau [membre]**\n**/leaderboard**", inline=False)
        embed.add_field(name="Musique", value="**/join**, **/play**, **/queue**, **/skip**, **/stop**, **/leave**", inline=False)
        embed.add_field(name="Utilitaires", value="**/avatar**, **/userinfo**, **/serverinfo**, **/ping**, **/commandes**", inline=False)
        embed.set_footer(text="Tape / dans le chat pour voir l'autocomplete Discord.")

        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message(
                "Liste des commandes envoyee en message prive.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Impossible d'envoyer un MP. Active les MP de ce serveur puis relance la commande.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            print(f"[commandes] embed send failed: {e!r}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Erreur d'envoi de la liste.", ephemeral=True)
