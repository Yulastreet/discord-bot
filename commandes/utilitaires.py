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

    @bot.tree.command(name="commandes", description="Recevoir la liste des commandes en MP (navigation par boutons)")
    async def commandes(interaction: discord.Interaction):
        pages = _build_command_pages()
        view  = CommandesPaginatorView(pages, owner_id=interaction.user.id)
        try:
            await interaction.user.send(embed=pages[0], view=view)
            await interaction.response.send_message(
                "📩 La liste des commandes vient de t'être envoyée en message privé !",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Impossible de t'envoyer un MP : tu as désactivé les MP de ce serveur.\n"
                "Active-les dans **Paramètres utilisateur → Confidentialité** puis relance la commande.",
                ephemeral=True,
            )
        except Exception as e:
            print(f"[commandes] DM send err: {type(e).__name__}: {e}")
            await interaction.response.send_message(
                "❌ Erreur d'envoi. Réessaie plus tard.", ephemeral=True,
            )


_PAGE_COLOR = 0x3498DB


def _build_command_pages() -> list:
    """Construit la liste d'embeds (3 pages denses)."""
    pages = []

    # Page 1 : Modération + Outils serveur
    p1 = discord.Embed(
        title="📋 Commandes · 🛡️ Modération & 🧰 Outils serveur",
        color=_PAGE_COLOR,
    )
    p1.add_field(
        name="🛡️ Modération",
        value=(
            "**/clear `<n>`** — supprime les N derniers messages\n"
            "**/kick `<membre>` `[raison]`** — expulse un membre\n"
            "**/ban `<membre>` `[raison]`** — bannit un membre\n"
            "**/poll `<question>` `<options>`** — sondage avec réactions\n"
            "**/setwelcome `[salon]`** — builder message de bienvenue\n"
            "**/warn `<membre>` `<raison>`** — avertit + auto-timeout si seuil\n"
            "**/modlogs `<membre>`** — historique des sanctions\n"
            "**/clearwarns `<membre>` `[raison]`** — révoque tous warns\n"
            "**/note `<membre>` `<texte>`** — note interne mod (pas de DM)"
        ),
        inline=False,
    )
    p1.add_field(
        name="🧰 Outils serveur",
        value=(
            "**/reaction_add / remove / list** — réactions auto sur membre\n"
            "**/rolereaction create** — builder rôles-réactions\n"
            "**/socialalert add / list / remove** — alertes Twitch/YT/Reddit\n"
            "**/ticket** — builder panneau de tickets\n"
            "**/giveaway create / list / reroll / cancel** — tirages au sort\n"
            "**/cmd `<nom>`** — exécute une commande custom (builder sur dashboard)"
        ),
        inline=False,
    )
    pages.append(p1)

    # Page 2 : Fun + XP + Duel + Musique
    p2 = discord.Embed(
        title="📋 Commandes · 🎉 Fun · ⭐ XP · ⚔️ Duel · 🎵 Musique",
        color=_PAGE_COLOR,
    )
    p2.add_field(
        name="🎉 Fun",
        value=(
            "**/8ball** **/dé** **/coinflip** **/blague** **/ship** **/choix** "
            "**/random** **/qui** **/clap** **/rate** **/citation** **/zgeg**"
        ),
        inline=False,
    )
    p2.add_field(
        name="⭐ Niveaux & XP",
        value=(
            "**/niveau `[membre]`** — affiche niveau + XP\n"
            "**/leaderboard** — top 10 XP du serveur"
        ),
        inline=False,
    )
    p2.add_field(
        name="⚔️ Duel",
        value=(
            "**/duel fight `<adversaire>` `[nerf]`** — défi sabres laser (mindgame défense)\n"
            "**/duel info** — guide complet du système\n"
            "**/profil `[membre]`** — profil duel\n"
            "**/statpoint `<stat>`** — attribuer un point\n"
            "**/sabre** — menu sabres (équipé / collection / boutique)\n"
            "**/historique `[membre]`** — historique duels"
        ),
        inline=False,
    )
    p2.add_field(
        name="🎵 Musique",
        value=(
            "**/join** **/play `<titre|lien>`** **/queue** **/skip** **/stop** **/leave**"
        ),
        inline=False,
    )
    pages.append(p2)

    # Page 3 : CS2 + Utilitaires
    p3 = discord.Embed(
        title="📋 Commandes · 🎮 Counter-Strike 2 & 🔧 Utilitaires",
        color=_PAGE_COLOR,
    )
    p3.add_field(
        name="🎮 Counter-Strike 2",
        value=(
            "**/cs link `<plateforme>` `<id>`** — lie compte Steam ou Faceit\n"
            "**/cs unlink `<plateforme>`** — retire un lien\n"
            "**/cs stats `[membre]`** — stats Steam / Faceit\n"
            "**/cs setrank `<elo>`** — déclare ton Premier ELO + rank role\n"
            "**/cs rankrole on|off** — admin : auto-attribution rank role\n"
            "**/cs price `<arme>` `<skin>` `[usure]` `[stattrak]`** — prix Steam + Skinport\n"
            "**/cs inventory `[membre|steamid]`** — inventaire CS2 + valeur €\n"
            "**/cs queue** — voice channel temporaire 5 slots\n"
            "**/cs map** — ban/pick maps entre membres du vocal\n"
            "**/cs loadout** — loadout aléatoire"
        ),
        inline=False,
    )
    p3.add_field(
        name="🔧 Utilitaires",
        value=(
            "**/avatar `[membre]`** — avatar d'un membre\n"
            "**/userinfo `[membre]`** — infos détaillées membre\n"
            "**/serverinfo** — infos serveur\n"
            "**/ping** — latence du bot\n"
            "**/commandes** — réaffiche cette aide en MP"
        ),
        inline=False,
    )
    pages.append(p3)

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

    @discord.ui.button(label="1 / 3", style=discord.ButtonStyle.secondary, custom_id="cmds:counter", disabled=True)
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
