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

    @bot.tree.command(name="commandes", description="Affiche la liste des commandes (navigation par boutons)")
    async def commandes(interaction: discord.Interaction):
        pages = _build_command_pages()
        view = CommandesPaginatorView(pages, owner_id=interaction.user.id)
        await interaction.response.send_message(
            embed=pages[0], view=view, ephemeral=True,
        )


# ============================================================================
# Pages + view de pagination /commandes
# ============================================================================

_PAGE_COLOR = 0x3498DB


def _build_command_pages() -> list:
    """Construit une liste d'embeds, un par section."""
    pages = []

    # Page 1 : Modération
    p1 = discord.Embed(
        title="📋 Commandes · 🛡️ Modération",
        color=_PAGE_COLOR,
        description=(
            "**/clear `<n>`** — supprime les N derniers messages\n"
            "**/kick `<membre>` `[raison]`** — expulse un membre\n"
            "**/ban `<membre>` `[raison]`** — bannit un membre\n"
            "**/poll `<question>` `<options>`** — sondage avec réactions\n"
            "**/setwelcome `[salon]`** — builder message de bienvenue\n"
            "**/warn `<membre>` `<raison>`** — avertit un membre + auto-timeout si seuil atteint\n"
            "**/modlogs `<membre>`** — historique des sanctions d'un membre\n"
            "**/clearwarns `<membre>` `[raison]`** — révoque tous les warns actifs\n"
            "**/note `<membre>` `<texte>`** — note interne mod (pas de DM user)"
        ),
    )
    pages.append(p1)

    # Page 2 : Outils serveur
    p2 = discord.Embed(
        title="📋 Commandes · 🧰 Outils serveur",
        color=_PAGE_COLOR,
        description=(
            "**/reaction_add `<membre>` `<emoji>`** — réaction auto sur un membre\n"
            "**/reaction_remove `<membre>`** — supprime la réaction auto\n"
            "**/reaction_list** — liste des réactions auto\n"
            "**/rolereaction create** — builder rôles-réactions\n"
            "**/socialalert add `<plateforme>` `<lien>` `<salon>`** — alertes Twitch/YT/Reddit\n"
            "**/socialalert list / remove `<id>`** — gestion des alertes\n"
            "**/ticket** — builder panneau de tickets\n"
            "**/giveaway create `<durée>` `<gagnants>` `<prix>` `[salon]`** — tirage au sort\n"
            "**/giveaway list / reroll / cancel** — gestion des giveaways\n"
            "**/cmd `<nom>`** — exécute une commande custom (builder sur dashboard)"
        ),
    )
    pages.append(p2)

    # Page 3 : Fun + XP
    p3 = discord.Embed(
        title="📋 Commandes · 🎉 Fun & ⭐ XP",
        color=_PAGE_COLOR,
        description=(
            "**/8ball `<question>`** — la boule magique répond\n"
            "**/dé `[faces]`** — lance un dé\n"
            "**/coinflip** — pile ou face\n"
            "**/blague** — blague aléatoire\n"
            "**/ship `<m1>` `<m2>`** — compatibilité entre deux membres\n"
            "**/choix `<options>`** — pick aléatoire (séparé par `|`)\n"
            "**/random `<min>` `<max>`** — nombre aléatoire\n"
            "**/qui `<question>`** — désigne un membre au hasard\n"
            "**/clap `<texte>`** — insère 👏 entre chaque 👏 mot\n"
            "**/rate `<truc>`** — note sur 10\n"
            "**/citation** — citation aléatoire\n"
            "**/zgeg** — mesure ton zgeg\n"
            "\n**⭐ Niveaux & XP**\n"
            "**/niveau `[membre]`** — niveau + XP\n"
            "**/leaderboard** — top 10 XP du serveur"
        ),
    )
    pages.append(p3)

    # Page 4 : Duel + Musique
    p4 = discord.Embed(
        title="📋 Commandes · ⚔️ Duel & 🎵 Musique",
        color=_PAGE_COLOR,
        description=(
            "**⚔️ Duel**\n"
            "**/duel fight `<adversaire>` `[nerf]`** — défi sabres laser (mindgame défense incluse)\n"
            "**/duel info** — guide complet du système de duel\n"
            "**/profil `[membre]`** — profil duel\n"
            "**/statpoint `<stat>`** — attribuer un point (force/agilité/défense/endurance/chance)\n"
            "**/sabre** — menu unifié sabres (équipé/collection/boutique)\n"
            "**/historique `[membre]`** — historique des duels\n"
            "\n**🎵 Musique**\n"
            "**/join** — rejoint ton salon vocal\n"
            "**/play `<titre|lien>`** — joue ou queue\n"
            "**/queue** — file d'attente\n"
            "**/skip** — chanson suivante\n"
            "**/stop** — stop + vide file\n"
            "**/leave** — déconnecte le bot"
        ),
    )
    pages.append(p4)

    # Page 5 : CS2 + Utilitaires
    p5 = discord.Embed(
        title="📋 Commandes · 🎮 CS2 & 🔧 Utilitaires",
        color=_PAGE_COLOR,
        description=(
            "**🎮 Counter-Strike 2**\n"
            "**/cs link `<plateforme>` `<id>`** — lie compte Steam ou Faceit\n"
            "**/cs unlink `<plateforme>`** — retire un lien\n"
            "**/cs stats `[membre]`** — stats Steam / Faceit\n"
            "**/cs setrank `<elo>`** — déclare ton Premier ELO + rank role\n"
            "**/cs rankrole on|off** — admin : auto-attribution rank role\n"
            "**/cs price `<arme>` `<skin>` `[usure]` `[stattrak]`** — prix Steam + Skinport\n"
            "**/cs inventory `[membre|steamid]`** — inventaire CS2 + valeur €\n"
            "**/cs queue** — voice channel temporaire 5 slots\n"
            "**/cs map** — ban/pick maps entre membres du vocal\n"
            "**/cs loadout** — loadout aléatoire\n"
            "\n**🔧 Utilitaires**\n"
            "**/avatar `[membre]`** — avatar d'un membre\n"
            "**/userinfo `[membre]`** — infos détaillées membre\n"
            "**/serverinfo** — infos serveur\n"
            "**/ping** — latence du bot\n"
            "**/commandes** — affiche cette navigation"
        ),
    )
    pages.append(p5)

    # Numerote les pages dans le footer
    for i, e in enumerate(pages, start=1):
        e.set_footer(text=f"Page {i}/{len(pages)} · Tip : tape / pour voir l'autocomplete Discord.")
    return pages


class CommandesPaginatorView(discord.ui.View):
    def __init__(self, pages: list, owner_id: int):
        super().__init__(timeout=180)
        self.pages    = pages
        self.idx      = 0
        self.owner_id = owner_id
        self._refresh_state()

    def _refresh_state(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "cmds:prev":
                    child.disabled = (self.idx == 0)
                elif child.custom_id == "cmds:next":
                    child.disabled = (self.idx >= len(self.pages) - 1)
                elif child.custom_id == "cmds:counter":
                    child.label = f"{self.idx + 1} / {len(self.pages)}"

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message(
                    "❌ Ce menu n'est pas pour toi. Fais `/commandes` toi-même.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="cmds:prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        if self.idx > 0:
            self.idx -= 1
        self._refresh_state()
        await interaction.response.edit_message(embed=self.pages[self.idx], view=self)

    @discord.ui.button(label="1 / 5", style=discord.ButtonStyle.secondary, custom_id="cmds:counter", disabled=True)
    async def counter_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # disabled, juste pour l'affichage

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="cmds:next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        if self.idx < len(self.pages) - 1:
            self.idx += 1
        self._refresh_state()
        await interaction.response.edit_message(embed=self.pages[self.idx], view=self)

    @discord.ui.button(label="✖ Fermer", style=discord.ButtonStyle.danger, custom_id="cmds:close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        try:
            await interaction.response.edit_message(
                content="_Menu fermé._", embed=None, view=None,
            )
        except Exception:
            pass
