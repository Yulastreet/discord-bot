"""Slash /presentation : builder de présentation membre + config salon.

- /presentation create : ouvre un modal (nom/pseudo, âge, description, passions,
  jeux favoris) puis poste un embed dans le salon configuré.
- /presentation setup <salon> <on/off> : admin/modo configure le salon dédié.

La commande create n'est utilisable que dans le salon configuré et si activée.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import guild_setting_get, guild_setting_set


class _PresentationModal(discord.ui.Modal, title="Ta présentation"):
    nom = discord.ui.TextInput(
        label="Nom ou pseudo",
        placeholder="Comment on t'appelle ?",
        max_length=64, required=True,
    )
    age = discord.ui.TextInput(
        label="Âge",
        placeholder="Ex : 21",
        max_length=3, required=False,
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Présente-toi en quelques lignes...",
        max_length=1000, required=True,
    )
    passions = discord.ui.TextInput(
        label="Passions / centres d'intérêt",
        placeholder="Ex : dessin, musique, dev, sport...",
        max_length=300, required=False,
    )
    jeux = discord.ui.TextInput(
        label="Jeux favoris",
        placeholder="Ex : LoL, CS2, Valorant, Minecraft...",
        max_length=300, required=False,
    )

    def __init__(self, target_channel: discord.TextChannel):
        super().__init__()
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        embed = discord.Embed(
            title=f"Présentation de {self.nom.value}",
            description=self.description.value,
            color=0xC8F050,
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url if user.display_avatar else None)
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        if self.age.value.strip():
            embed.add_field(name="🎂 Âge", value=self.age.value.strip(), inline=True)
        embed.add_field(name="👤 Membre", value=user.mention, inline=True)
        if self.passions.value.strip():
            embed.add_field(name="✨ Passions", value=self.passions.value.strip(), inline=False)
        if self.jeux.value.strip():
            embed.add_field(name="🎮 Jeux favoris", value=self.jeux.value.strip(), inline=False)
        embed.set_footer(text="Présentation soumise via /presentation")

        try:
            await self.target_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Je n'ai pas la permission d'écrire dans le salon de présentation.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"✅ Ta présentation a été postée dans {self.target_channel.mention} !",
            ephemeral=True,
        )


def setup_presentation_commands(bot: commands.Bot):

    presentation_group = app_commands.Group(
        name="presentation",
        description="Présente-toi à la communauté",
    )

    @presentation_group.command(name="create", description="Ouvre le formulaire de présentation")
    async def presentation_create(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Indisponible en DM.", ephemeral=True)
            return
        enabled = guild_setting_get(interaction.guild.id, "presentation_enabled", "0") == "1"
        ch_id = guild_setting_get(interaction.guild.id, "presentation_channel_id", "")
        if not enabled or not ch_id:
            await interaction.response.send_message(
                "❌ Les présentations ne sont pas activées sur ce serveur. "
                "Un admin doit faire `/presentation setup`.",
                ephemeral=True,
            )
            return
        # Restriction : utilisable uniquement dans le salon configuré
        if str(interaction.channel_id) != str(ch_id):
            await interaction.response.send_message(
                f"❌ Cette commande s'utilise uniquement dans <#{ch_id}>.",
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(ch_id))
        if not channel:
            await interaction.response.send_message(
                "❌ Salon de présentation introuvable. Demande à un admin de refaire `/presentation setup`.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(_PresentationModal(channel))

    @presentation_group.command(name="setup", description="Admin : configure le salon des présentations")
    @app_commands.describe(salon="Salon où les présentations seront postées",
                           etat="Activer ou désactiver les présentations")
    @app_commands.choices(etat=[
        app_commands.Choice(name="Activer", value="on"),
        app_commands.Choice(name="Désactiver", value="off"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def presentation_setup(interaction: discord.Interaction,
                                 salon: discord.TextChannel,
                                 etat: app_commands.Choice[str]):
        if not interaction.guild:
            await interaction.response.send_message("❌ Indisponible en DM.", ephemeral=True)
            return
        guild_setting_set(interaction.guild.id, "presentation_channel_id", str(salon.id))
        guild_setting_set(interaction.guild.id, "presentation_enabled",
                          "1" if etat.value == "on" else "0")
        state_txt = "activées" if etat.value == "on" else "désactivées"
        await interaction.response.send_message(
            f"✅ Présentations **{state_txt}** · salon : {salon.mention}\n"
            f"Les membres peuvent désormais faire `/presentation create` dans {salon.mention}.",
            ephemeral=True,
        )

    bot.tree.add_command(presentation_group)
