"""Slash /setup : builder de configuration initiale.

Configure les 4 salons essentiels du bot (welcome/logs/alerts/admin).
Si le user est le PROPRIO du serveur et que la config mod n'a jamais ete faite,
cree un salon temporaire prive avec un builder pour choisir le role modo + les
permissions accordees aux modos.

Permissions : manage_guild requise.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import guild_setting_set, guild_setting_get


_SETUP_FIELDS = [
    ("welcome", "Salon de bienvenue",
     "📥 Messages d'arrivée des nouveaux membres."),
    ("logs",    "Salon de logs",
     "📜 Historique d'activité (commandes, modération)."),
    ("alerts",  "Salon des alertes",
     "🔴 Notifications Twitch / YouTube / Reddit."),
    ("admin",   "Salon admin/modo",
     "🛡️ Notifications internes (Guild Boost +, alertes staff)."),
]


# ===== Liste des permissions toggleables pour modos =====
# (key, label affiche, description, kind)
MOD_PERMS_REGISTRY = [
    ("warn",            "/warn",          "Avertir un membre",                      "slash"),
    ("kick",            "/kick",          "Expulser un membre",                     "slash"),
    ("ban",             "/ban",           "Bannir un membre",                       "slash"),
    ("clear",           "/clear",         "Supprimer des messages",                 "slash"),
    ("ticket",          "/ticket",        "Panneau de tickets",                     "slash"),
    ("giveaway",        "/giveaway",      "Créer des giveaways",                    "slash"),
    ("poll",            "/poll",          "Créer des sondages",                     "slash"),
    ("rolereaction",    "/rolereaction",  "Rôles-réaction",                         "slash"),
    ("socialalert",     "/socialalert",   "Alertes Twitch/YT/Reddit",               "slash"),
    ("setwelcome",      "/setwelcome",    "Builder message de bienvenue",           "slash"),
    ("reaction",        "/reaction_*",    "Auto-réactions",                         "slash"),
    ("modlogs",         "/modlogs",       "Consulter / config modlog",              "slash"),
    ("setup",           "/setup",         "Refaire le setup salons",                "slash"),
    ("note",            "/note",          "Ajouter une note sur un membre",         "slash"),
    ("logs",            "Logs",           "Page Logs du dashboard",                 "dashboard"),
    ("custom_commands", "Commandes custom","Page Commandes custom",                 "dashboard"),
    ("music",           "Musique",        "Page Musique",                           "dashboard"),
    ("features",        "Fonctionnalités","Page Fonctionnalités",                   "dashboard"),
    ("settings",        "Réglages serveur","Page Réglages serveur",                 "dashboard"),
]


class ModPermsView(discord.ui.View):
    """View dans le salon temporaire : RoleSelect + 2 multi-selects perms + Save/Close."""

    def __init__(self, guild_id: int, owner_user_id: int, temp_channel_id: int):
        super().__init__(timeout=3600)
        self.guild_id = int(guild_id)
        self.owner_user_id = int(owner_user_id)
        self.temp_channel_id = int(temp_channel_id)
        self.selected_role: int | None = None
        self.selected_slash: set[str] = set()
        self.selected_dash: set[str] = set()

        # Populate les options des deux multi-selects dynamiquement
        slash_perms = [p for p in MOD_PERMS_REGISTRY if p[3] == "slash"]
        dash_perms  = [p for p in MOD_PERMS_REGISTRY if p[3] == "dashboard"]

        self.slash_select.options = [
            discord.SelectOption(label=lbl, value=key, description=desc[:100])
            for key, lbl, desc, _ in slash_perms
        ]
        self.slash_select.max_values = len(slash_perms)
        self.dash_select.options = [
            discord.SelectOption(label=lbl, value=key, description=desc[:100])
            for key, lbl, desc, _ in dash_perms
        ]
        self.dash_select.max_values = len(dash_perms)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Seul le proprio du serveur peut interagir
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message(
                "⛔ Ce builder est réservé au propriétaire du serveur.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="🎭 Choisis le rôle des modérateurs",
        min_values=1, max_values=1, row=0,
    )
    async def role_select(self, interaction: discord.Interaction, select):
        self.selected_role = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="🛠️ Slash commands autorisées pour les modos",
        min_values=0, row=1,
    )
    async def slash_select(self, interaction: discord.Interaction, select):
        self.selected_slash = set(select.values)
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="🖥️ Pages dashboard autorisées pour les modos",
        min_values=0, row=2,
    )
    async def dash_select(self, interaction: discord.Interaction, select):
        self.selected_dash = set(select.values)
        await interaction.response.defer()

    @discord.ui.button(label="Sauvegarder", emoji="✅",
                       style=discord.ButtonStyle.success, row=3)
    async def btn_save(self, interaction: discord.Interaction, button):
        if not self.selected_role:
            await interaction.response.send_message(
                "❌ Sélectionne d'abord un rôle modérateur.", ephemeral=True,
            )
            return
        # Save role
        guild_setting_set(self.guild_id, "mod_role_id", str(self.selected_role))
        # Save perms (set 1 pour ceux selectionnes, 0 pour les autres)
        all_keys = {p[0] for p in MOD_PERMS_REGISTRY}
        granted = self.selected_slash | self.selected_dash
        for k in all_keys:
            guild_setting_set(self.guild_id, f"mod_perm_{k}",
                              "1" if k in granted else "0")
        guild_setting_set(self.guild_id, "mod_access_configured", "1")

        # Disable view
        for c in self.children:
            c.disabled = True

        embed = discord.Embed(
            title="✅ Permissions modérateurs enregistrées",
            description=(
                f"**Rôle modérateur :** <@&{self.selected_role}>\n"
                f"**Slash commands accordées :** {len(self.selected_slash)} / "
                f"{sum(1 for p in MOD_PERMS_REGISTRY if p[3]=='slash')}\n"
                f"**Pages dashboard accordées :** {len(self.selected_dash)} / "
                f"{sum(1 for p in MOD_PERMS_REGISTRY if p[3]=='dashboard')}\n\n"
                "💡 *Tu peux modifier ces permissions à tout moment via le dashboard :*\n"
                "`dashboard.tookbot.click/mod-config`\n\n"
                "Ce salon temporaire sera **supprimé dans 30 secondes**."
            ),
            color=0xB9F23A,
        )
        await interaction.response.edit_message(embed=embed, view=self)

        # Auto-delete temp channel after 30s
        import asyncio
        await asyncio.sleep(30)
        try:
            ch = interaction.guild.get_channel(self.temp_channel_id)
            if ch:
                await ch.delete(reason="Setup mod perms terminé")
        except Exception as e:
            print(f"[setup] temp channel delete fail: {e}")

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, row=3)
    async def btn_cancel(self, interaction: discord.Interaction, button):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content="❌ Configuration annulée. Le salon sera supprimé dans 10 secondes.\n"
                    "Tu peux relancer `/setup` ou aller sur `dashboard.tookbot.click/mod-config`.",
            embed=None, view=self,
        )
        import asyncio
        await asyncio.sleep(10)
        try:
            ch = interaction.guild.get_channel(self.temp_channel_id)
            if ch:
                await ch.delete(reason="Setup mod perms annulé")
        except Exception:
            pass


async def _create_mod_perms_temp_channel(guild: discord.Guild, server_owner: discord.Member):
    """Cree un salon temporaire prive (owner + bot) avec un builder de perms modos."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me:           discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True),
        server_owner:       discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True),
    }
    try:
        channel = await guild.create_text_channel(
            name="🔐-config-modos",
            overwrites=overwrites,
            reason=f"Setup mod perms pour {server_owner}",
        )
    except discord.HTTPException as e:
        print(f"[setup] create temp channel fail: {e}")
        return None

    embed = discord.Embed(
        title="🔐 Configure les permissions des modérateurs",
        description=(
            f"Salut **{server_owner.mention}** !\n\n"
            "Ce salon est **privé** (seul toi et le bot y avez accès) et sera **supprimé** "
            "automatiquement après la configuration.\n\n"
            "**1.** Choisis le rôle Discord qui correspond aux **modérateurs** de ton serveur.\n"
            "**2.** Sélectionne les **slash commands** que les modos peuvent utiliser.\n"
            "**3.** Sélectionne les **pages du dashboard** auxquelles ils ont accès.\n"
            "**4.** Clique **Sauvegarder**.\n\n"
            "💡 *Tu pourras modifier ces droits à tout moment via `dashboard.tookbot.click/mod-config`.*"
        ),
        color=0xB9F23A,
    )
    embed.set_footer(text="Configuration permission modérateurs — seul le proprio peut interagir")

    view = ModPermsView(guild.id, server_owner.id, channel.id)
    try:
        await channel.send(content=server_owner.mention, embed=embed, view=view)
    except discord.HTTPException as e:
        print(f"[setup] send temp channel fail: {e}")
    return channel


class SetupView(discord.ui.View):
    """View principale /setup : 4 ChannelSelect + Save/Cancel."""

    def __init__(self, guild_id: int, *, current: dict[str, str] | None = None,
                 server_owner: discord.Member | None = None):
        super().__init__(timeout=900)
        self.guild_id = int(guild_id)
        self.server_owner = server_owner
        self.selections: dict[str, int] = {}
        if current:
            for k, v in current.items():
                if v:
                    try:
                        self.selections[k] = int(v)
                    except (TypeError, ValueError):
                        pass

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📥 Salon de bienvenue",
        min_values=1, max_values=1, row=0,
    )
    async def s_welcome(self, interaction, select):
        self.selections["welcome"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📜 Salon de logs",
        min_values=1, max_values=1, row=1,
    )
    async def s_logs(self, interaction, select):
        self.selections["logs"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="🔴 Salon des alertes (Twitch / YouTube / Reddit)",
        min_values=1, max_values=1, row=2,
    )
    async def s_alerts(self, interaction, select):
        self.selections["alerts"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="🛡️ Salon admin/modo (notifications internes)",
        min_values=1, max_values=1, row=3,
    )
    async def s_admin(self, interaction, select):
        self.selections["admin"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.button(label="Sauvegarder la configuration",
                       style=discord.ButtonStyle.success, emoji="✅", row=4)
    async def btn_save(self, interaction: discord.Interaction, button):
        if not self.selections:
            await interaction.response.send_message(
                "❌ Sélectionne au moins un salon avant de sauvegarder.",
                ephemeral=True,
            )
            return
        for key, cid in self.selections.items():
            guild_setting_set(self.guild_id, f"setup_{key}_channel_id", str(cid))
        guild_setting_set(self.guild_id, "setup_completed", "1")

        embed = discord.Embed(
            title="✅ Configuration enregistrée",
            description="Les salons suivants ont été configurés :",
            color=0xB9F23A,
        )
        for key, label, _ in _SETUP_FIELDS:
            cid = self.selections.get(key) or guild_setting_get(self.guild_id, f"setup_{key}_channel_id", "")
            if cid:
                embed.add_field(name=label, value=f"<#{cid}>", inline=False)
            else:
                embed.add_field(name=label, value="*(non configuré)*", inline=False)

        # Si user EST le server owner et mod_access_configured=0 -> deuxieme etape
        mod_configured = guild_setting_get(self.guild_id, "mod_access_configured", "0") == "1"
        is_owner_target = (interaction.guild.owner_id == interaction.user.id)
        next_step_msg = ""
        if is_owner_target and not mod_configured:
            next_step_msg = "\n\n🔐 **Étape suivante :** Je viens de créer un salon privé `#🔐-config-modos` pour que tu choisisses les permissions des modérateurs. Va y faire un tour !"
        else:
            next_step_msg = "\n\nTu peux refaire `/setup` à tout moment pour modifier."
        embed.description = embed.description + next_step_msg

        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

        # Cree le salon temp si proprio + config mod jamais faite
        if is_owner_target and not mod_configured and self.server_owner:
            try:
                await _create_mod_perms_temp_channel(interaction.guild, self.server_owner)
            except Exception as e:
                print(f"[setup] mod perms channel create err: {e}")

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, row=4)
    async def btn_cancel(self, interaction: discord.Interaction, button):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content="❌ Configuration annulée. Relance `/setup` quand tu veux.",
            embed=None, view=self,
        )


def setup_setup_commands(bot: commands.Bot):

    @bot.tree.command(name="setup",
                      description="⚠️ Configure les salons + permissions modos du bot (admin/modo)")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_cmd(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Indisponible en message privé, utilise cette commande dans un serveur.", ephemeral=True)
            return

        current = {}
        for key, _, _ in _SETUP_FIELDS:
            current[key] = guild_setting_get(interaction.guild.id, f"setup_{key}_channel_id", "")

        lines = []
        for key, label, hint in _SETUP_FIELDS:
            cid = current.get(key)
            cur_str = f"<#{cid}>" if cid else "*(non configuré)*"
            lines.append(f"**{label}** — {hint}\n→ Actuel : {cur_str}")
        body = "\n\n".join(lines)

        embed = discord.Embed(
            title="⚠️ IMPORTANT — Configuration initiale du bot",
            description=(
                "Sélectionne les salons ci-dessous puis clique **Sauvegarder**.\n"
                "Ces salons sont **essentiels** pour le bon fonctionnement du bot.\n\n"
                + body
            ),
            color=0xFF6B35,
        )

        # Si l'user est le proprio + jamais config mod, previent qu'une 2e etape arrivera
        is_owner_target = (interaction.guild.owner_id == interaction.user.id)
        mod_configured = guild_setting_get(interaction.guild.id, "mod_access_configured", "0") == "1"
        if is_owner_target and not mod_configured:
            embed.add_field(
                name="🔐 Étape suivante (toi uniquement)",
                value=(
                    "Tu es le propriétaire de ce serveur. Après avoir sauvegardé ici, "
                    "un **salon temporaire privé** sera créé automatiquement pour configurer "
                    "les permissions des modérateurs."
                ),
                inline=False,
            )

        embed.set_footer(text="Tu peux relancer /setup à tout moment.")

        # Récupère le Member du proprio pour les overwrites du temp channel
        owner_member = interaction.guild.owner

        view = SetupView(interaction.guild.id, current=current, server_owner=owner_member)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
