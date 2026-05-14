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
            title="📋 Liste des commandes",
            description="Toutes les commandes disponibles, par catégorie.",
            color=discord.Color.blue()
        )

        moderation = (
            "**/clear <nombre>** (supprime les N derniers messages)\n"
            "**/kick <membre> [raison]** (expulse un membre du serveur)\n"
            "**/ban <membre> [raison]** (bannit un membre du serveur)\n"
            "**/poll <question> <option1> <option2> [option3] [option4]** (crée un sondage avec réactions)\n"
            "**/setwelcome [salon]** (builder : salon et message de bienvenue)"
        )
        embed.add_field(name="🛡️ Modération", value=moderation, inline=False)

        outils = (
            "**/reaction_add <membre> <emoji>** (réaction auto sur un membre, ce serveur uniquement)\n"
            "**/reaction_remove <membre>** (supprime la réaction auto d'un membre)\n"
            "**/reaction_list** (liste des réactions auto sur ce serveur)\n"
            "**/rolereaction create** (builder : salon, embed, plusieurs emojis/rôles)\n"
            "**/socialalert add <plateforme> <pseudo> <salon> [msg]** (alertes Twitch/YT/Reddit)\n"
            "**/socialalert list** (liste des alertes actives)\n"
            "**/socialalert remove <id>** (supprime une alerte)\n"
            "**/ticket** (builder : salon, rôle support, catégorie, titre/desc, bouton custom)"
        )
        embed.add_field(name="🧰 Outils serveur", value=outils, inline=False)
        embed.add_field(name="​", value="​", inline=False)

        fun = (
            "**/8ball <question>** (la boule magique répond à ta question)\n"
            "**/dé [faces]** (lance un dé, 6 faces par défaut)\n"
            "**/coinflip** (pile ou face)\n"
            "**/blague** (raconte une blague aléatoire)\n"
            "**/ship <membre1> <membre2>** (calcule le taux de compatibilité entre deux membres)\n"
            "**/choix <options>** (le bot choisit une option parmi celles que tu donnes, séparées par |)\n"
            "**/random <min> <max>** (tire un nombre aléatoire entre deux bornes)\n"
            "**/qui <question>** (le bot désigne un membre du serveur au hasard)\n"
            "**/clap <texte>** (insère 👏 entre 👏 chaque 👏 mot)\n"
            "**/rate <truc>** (le bot note quelque chose sur 10)\n"
            "**/citation** (affiche une citation au hasard)\n"
            "**/zgeg** (mesure ton zgeg, réaction selon le résultat)"
        )
        embed.add_field(name="🎉 Fun", value=fun, inline=False)
        embed.add_field(name="​", value="​", inline=False)

        xp = (
            "**/niveau [membre]** (affiche ton niveau et XP, ou celui d'un membre)\n"
            "**/leaderboard** (top 10 XP de ce serveur)"
        )
        embed.add_field(name="⭐ Niveaux & XP", value=xp, inline=False)
        embed.add_field(name="​", value="​", inline=False)

        duel = (
            "**/duel fight <adversaire>** (défie un membre en duel de sabres)\n"
            "**/duel fight <adversaire> nerf:True** (duel équilibré, ignore niveaux et stats)\n"
            "**/duel info** (envoie en MP le guide complet du système de duel)\n"
            "**/profil [membre]** (affiche le profil duel d'un joueur)\n"
            "**/statpoint <stat>** (attribue un point de stat : force, agilite, defense, endurance, chance)\n"
            "**/sabre** (menu unifié : sabre équipé, collection, boutique avec navigation par boutons)\n"
            "**/historique [membre]** (affiche l'historique des duels)"
        )
        embed.add_field(name="⚔️ Duel", value=duel, inline=False)
        embed.add_field(name="​", value="​", inline=False)

        music = (
            "**/join** (rejoint ton salon vocal actuel)\n"
            "**/play <titre ou lien>** (joue une musique ou l'ajoute à la file)\n"
            "**/queue** (affiche la file d'attente musicale)\n"
            "**/skip** (passe à la musique suivante)\n"
            "**/stop** (stoppe la lecture et vide la file)\n"
            "**/leave** (déconnecte le bot du salon vocal)"
        )
        embed.add_field(name="🎵 Musique", value=music, inline=False)
        embed.add_field(name="​", value="​", inline=False)

        utils = (
            "**/avatar [membre]** (affiche l'avatar d'un membre)\n"
            "**/userinfo [membre]** (informations détaillées sur un membre)\n"
            "**/serverinfo** (informations détaillées sur le serveur)\n"
            "**/ping** (affiche la latence du bot)\n"
            "**/commandes** (envoie cette liste en message privé)"
        )
        embed.add_field(name="🔧 Utilitaires", value=utils, inline=False)

        embed.set_footer(text="Tip : tape / dans le chat pour voir l'autocomplete Discord.")

        # Tente l'envoi en MP, fallback ephemere si DMs fermés
        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message(
                "📩 La liste des commandes vient de t'être envoyée en message privé.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Impossible d'envoyer un MP : tu as peut-être désactivé les MP de ce serveur.\n"
                "Active-les dans les paramètres Discord puis relance la commande.",
                ephemeral=True
            )
        except discord.HTTPException as e:
            # Embed trop long ou autre 400 - fallback texte ephemere
            print(f"[commandes] embed send failed: {e!r}")
            try:
                await interaction.response.send_message(
                    "❌ Erreur d'envoi de la liste. Le dev a été pinged dans les logs.",
                    ephemeral=True,
                )
            except Exception:
                pass
