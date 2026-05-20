"""Slash /setup : builder de configuration initiale.

Configure les 4 salons essentiels du bot :
- Bienvenue
- Logs
- Alertes Twitch/YouTube/Reddit
- Notifications admin/modo (Guild Boost +, alerts internes)

Permissions : manage_guild requise.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import guild_setting_set, guild_setting_get


# Cle DB -> (label affiche, emoji, description courte)
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


class SetupView(discord.ui.View):
    """View avec 4 ChannelSelect + bouton Sauvegarder."""

    def __init__(self, guild_id: int, *, current: dict[str, str] | None = None):
        super().__init__(timeout=900)
        self.guild_id = int(guild_id)
        self.selections: dict[str, int] = {}
        # Pre-charge les valeurs deja sauvegardees
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
    async def s_welcome(self, interaction: discord.Interaction, select):
        self.selections["welcome"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📜 Salon de logs",
        min_values=1, max_values=1, row=1,
    )
    async def s_logs(self, interaction: discord.Interaction, select):
        self.selections["logs"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="🔴 Salon des alertes (Twitch / YouTube / Reddit)",
        min_values=1, max_values=1, row=2,
    )
    async def s_alerts(self, interaction: discord.Interaction, select):
        self.selections["alerts"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="🛡️ Salon admin/modo (notifications internes)",
        min_values=1, max_values=1, row=3,
    )
    async def s_admin(self, interaction: discord.Interaction, select):
        self.selections["admin"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.button(label="Sauvegarder la configuration",
                       style=discord.ButtonStyle.success, emoji="✅", row=4)
    async def btn_save(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Vérifie qu'au moins un salon a été choisi (sinon recharge depuis DB)
        if not self.selections:
            await interaction.response.send_message(
                "❌ Sélectionne au moins un salon avant de sauvegarder.",
                ephemeral=True,
            )
            return
        saved = []
        for key, cid in self.selections.items():
            guild_setting_set(self.guild_id, f"setup_{key}_channel_id", str(cid))
            saved.append(key)
        guild_setting_set(self.guild_id, "setup_completed", "1")

        # Récap
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
        embed.set_footer(text="Tu peux refaire /setup à tout moment pour modifier.")

        # Disable la view
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, row=4)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content="❌ Configuration annulée. Relance `/setup` quand tu veux.",
            embed=None, view=self,
        )


def setup_setup_commands(bot: commands.Bot):

    @bot.tree.command(name="setup",
                      description="⚠️ Configure les 4 salons essentiels du bot (admin/modo)")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_cmd(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Indisponible en DM.", ephemeral=True)
            return

        # Recharge valeurs existantes pour pre-remplir mentalement
        current = {}
        for key, _, _ in _SETUP_FIELDS:
            current[key] = guild_setting_get(interaction.guild.id, f"setup_{key}_channel_id", "")

        # Affiche les choix actuels
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
        embed.set_footer(text="Tu peux relancer /setup à tout moment.")

        view = SetupView(interaction.guild.id, current=current)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
