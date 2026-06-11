import os
import discord
from discord import app_commands


def setup_utility_commands(bot):
    @bot.tree.command(name="ping", description="Voir la latence du bot")
    async def ping(interaction: discord.Interaction):
        latence = round(bot.latency * 1000)
        await interaction.response.send_message(f"Pong ! Latence : **{latence}ms**")

    @bot.tree.command(name="vote", description="Voter pour TookBot sur top.gg")
    async def vote(interaction: discord.Interaction):
        bot_id = (os.getenv("DISCORD_BOT_ID") or "").strip()
        if not bot_id and bot.user:
            bot_id = str(bot.user.id)
        url = f"https://top.gg/bot/{bot_id}/vote" if bot_id else "https://top.gg/"
        embed = discord.Embed(
            title="❤️ Vote pour TookBot",
            description=(f"Soutiens le bot en votant sur top.gg.\n\n"
                          f"[**Cliquer pour voter**]({url})\n\n"
                          f"Vote toutes les 12h. Aucune obligation, c'est gratuit."),
            color=0xff3d57,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="invite", description="Lien pour inviter TookBot sur ton serveur")
    async def invite(interaction: discord.Interaction):
        bot_id = (os.getenv("DISCORD_BOT_ID") or "").strip()
        if not bot_id and bot.user:
            bot_id = str(bot.user.id)
        # Permissions integer : View Channels + Send Messages + Embed Links +
        # Attach Files + Read History + Manage Roles + Manage Channels +
        # Manage Messages + Kick + Ban + Connect + Speak + Move Members +
        # Add Reactions + External Emojis + View Audit Log + Use Slash Commands
        perms = "1099780115008"
        url = (f"https://discord.com/oauth2/authorize?client_id={bot_id}"
                f"&permissions={perms}&scope=bot+applications.commands")
        embed = discord.Embed(
            title="➕ Inviter TookBot",
            description=(f"[**Ajouter TookBot a ton serveur**]({url})\n\n"
                          f"Les permissions demandees correspondent precisement "
                          f"aux fonctionnalites du bot. Tu peux les ajuster apres "
                          f"l'invitation depuis les parametres du serveur."),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        embed.add_field(name="ID", value=f"`{serveur.id}`", inline=False)
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
            "**/poll** — ouvre un builder (question + 2-10 options + duree 1-168h, sondage natif Discord)\n"
            "**/setwelcome `[salon]`** — builder message de bienvenue\n"
            "**/warn `<membre>` `<raison>`** — avertit + auto-timeout si seuil\n"
            "**/modlogs `<membre>`** — historique des sanctions\n"
            "**/clearwarns `<membre>` `[raison]`** — révoque tous warns\n"
            "**/note `<membre>` `<texte>`** — note interne mod (pas de DM)\n"
            "**Automod** (TookBot+) — filtres mots interdits, anti-pub, anti-spam mentions, anti-raid configurables depuis le dashboard"
        ),
        inline=False,
    )
    p1.add_field(
        name="🔊 Salons vocaux temporaires",
        value=(
            "**/tempvoice setup `<lobby>` `[categorie]` `[nom_par_defaut]`** — admin : configure le salon lobby\n"
            "**/tempvoice info** — admin : voir la config actuelle\n"
            "**/tempvoice disable** — admin : desactive la feature\n"
            "**/voc rename `<nom>`** — renomme ton salon temp\n"
            "**/voc limit `<nombre>`** — limite le nombre de membres\n"
            "**/voc lock / unlock** — verrouille / re-ouvre le salon\n"
            "**/voc kick `<membre>`** — vire un membre du salon\n"
            "**/voc transfer `<membre>`** — transfere la propriete"
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
            "**/cmd `<nom>`** — exécute une commande custom (TookBot+, builder dashboard)\n"
            "**Analytics serveur** — page dashboard avec stats live, heatmap, top commandes / top users"
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
            "**/join** **/leave** — rejoindre / quitter ton vocal\n"
            "**/play `<titre|lien>`** — YouTube, SoundCloud, Bandcamp, Spotify (track/album/playlist)\n"
            "**/search `<query>`** — choisir parmi 5 resultats YouTube\n"
            "**/queue** **/nowplaying** — file complete / piste en cours\n"
            "**/skip `[position]`** **/remove `<position>`** — passer / retirer une piste\n"
            "**/volume `<0-200>`** **/pause** **/resume** **/stop**\n"
            "**/musicstats `[periode]`** — top tracks et top auditeurs"
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
            "**/tweet `<message_id>`** — image carte style tweet du message\n"
            "**/remind** — builder rappel (date, message, salon)\n"
            "**/reminders** — liste tes rappels actifs\n"
            "**/unremind `<id>`** — annule un rappel\n"
            "**/vote** — vote TookBot sur top.gg\n"
            "**/invite** — lien invitation officiel\n"
            "**/commandes** — réaffiche cette aide en MP"
        ),
        inline=False,
    )
    pages.append(p3)

    # Page 4 : Cartes — collection & économie
    p4 = discord.Embed(
        title="📋 Commandes · 🃏 Cartes — collection & économie",
        color=_PAGE_COLOR,
    )
    p4.add_field(
        name="🃏 Collection",
        value=(
            "**/roll `[univers]`** — tire 1 carte aléatoire. Cooldown **global 1h** "
            "(30 min = 2/h sur le serveur support). Donne des Essences ✨ selon la rareté\n"
            "**/cardcollec `[membre]` `[rareté]`** — ta collection (✨ = cosmétique, ⭐ = fusion)\n"
            "**/card `<nom>`** — détails d'une carte (autocomplete sur 19k+)\n"
            "**/show `<carte>`** — montre une de tes cartes avec sa bordure + étoiles"
        ),
        inline=False,
    )
    p4.add_field(
        name="✨ Essences (monnaie)",
        value=(
            "**/essences `[membre]`** — ton solde d'Essences ✨\n"
            "**/daily** — récompense quotidienne (TookCoins + Essences, streak)\n"
            "**/cardshop** — boutique : achète cartes & cosmétiques avec tes Essences\n"
            "**/cardrecycle `<carte>` `[qté]`** — recycle tes doublons en Essences"
        ),
        inline=False,
    )
    p4.add_field(
        name="⭐ Fusion & cosmétiques",
        value=(
            "**/cardfuse `<carte>`** — fusionne des doublons pour ajouter une étoile (max 5). "
            "La carte fusionnée devient non-échangeable\n"
            "**/cardcustom `<carte>` `<bordure>`** — applique une bordure (consommée)\n"
            "**/cardinventory `[membre]`** — tes cosmétiques en stock"
        ),
        inline=False,
    )
    pages.append(p4)

    # Page 5 : Cartes — social, profil, classements
    p5 = discord.Embed(
        title="📋 Commandes · 🃏 Cartes — social & profil",
        color=_PAGE_COLOR,
    )
    p5.add_field(
        name="🪪 Profil & classements",
        value=(
            "**/cardprofile `[membre]`** — profil de cartes (stats + image de tes 3 cartes vedettes)\n"
            "**/cardprofile** `setup_gauche/milieu/droite` — définis tes 3 cartes vedettes\n"
            "**/cardtop `<catégorie>`** — classements globaux (valeur, mythiques, essences, fusions, chance)"
        ),
        inline=False,
    )
    p5.add_field(
        name="💖 Wishlist",
        value=(
            "**/cardwish `<carte>`** — ajoute/retire de ta wishlist (3 max, **6 sur le support**). "
            "Tu es ping quand quelqu'un la tire\n"
            "**/cardwishlist `[membre]`** — voir une wishlist (boutons 🗑 pour retirer)"
        ),
        inline=False,
    )
    p5.add_field(
        name="🔄 Échange & suggestion",
        value=(
            "**/cardtrade `<joueur>`** — échange multi-cartes (Accepter / Refuser / Contre-offre)\n"
            "**/cardsuggest** — propose un perso à ajouter (serveur support uniquement)"
        ),
        inline=False,
    )
    p5.add_field(
        name="⚙️ Setup serveur (admin)",
        value=(
            "**/cardsetup `<salon>`** — restreint les commandes cartes à ce salon\n"
            "**/cardsetup_disable** — retire la restriction\n"
            "**/cardhelp** — guide complet du système de cartes"
        ),
        inline=False,
    )
    pages.append(p5)

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
