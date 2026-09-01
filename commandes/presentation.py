"""Slash /presentation : member introduction builder + channel config.

- /presentation create : opens a modal (name, age, description, hobbies,
  favourite games) then posts an embed in the configured channel.
- /presentation setup <channel> <state> : admin/mod configures the dedicated channel.

The create command only works inside the configured channel and when enabled.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import guild_setting_get, guild_setting_set
from services.i18n import locale_of, t, ti
from services.ui_v2 import Panel


class _PresentationModal(discord.ui.Modal):
    name = discord.ui.TextInput(label="Name", max_length=64, required=True)
    age = discord.ui.TextInput(label="Age", max_length=3, required=False)
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        max_length=1000, required=True,
    )
    hobbies = discord.ui.TextInput(label="Hobbies", max_length=300, required=False)
    games = discord.ui.TextInput(label="Favourite games", max_length=300, required=False)

    def __init__(self, target_channel: discord.TextChannel, locale: str = "en"):
        super().__init__(title=t("server.presentation.modal_title", locale))
        self.target_channel = target_channel
        self.locale = locale
        self.name.label = t("server.presentation.field_name", locale)
        self.name.placeholder = t("server.presentation.field_name_ph", locale)
        self.age.label = t("server.presentation.field_age", locale)
        self.age.placeholder = t("server.presentation.field_age_ph", locale)
        self.description.label = t("server.presentation.field_description", locale)
        self.description.placeholder = t("server.presentation.field_description_ph", locale)
        self.hobbies.label = t("server.presentation.field_hobbies", locale)
        self.hobbies.placeholder = t("server.presentation.field_hobbies_ph", locale)
        self.games.label = t("server.presentation.field_games", locale)
        self.games.placeholder = t("server.presentation.field_games_ph", locale)

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        p = Panel(
            ti(interaction, "server.presentation.embed_title", name=self.name.value),
            self.description.value,
        )
        # V2 has no embed author line: the username stays as a subtext byline.
        p.text(f"-# {user}")
        if user.display_avatar:
            p.thumbnail(user.display_avatar.url)
        if self.age.value.strip():
            p.field(ti(interaction, "server.presentation.embed_age"),
                    self.age.value.strip(), inline=True)
        p.field(ti(interaction, "server.presentation.embed_member"),
                user.mention, inline=True)
        if self.hobbies.value.strip():
            p.field(ti(interaction, "server.presentation.embed_hobbies"),
                    self.hobbies.value.strip(), inline=False)
        if self.games.value.strip():
            p.field(ti(interaction, "server.presentation.embed_games"),
                    self.games.value.strip(), inline=False)
        p.footer(ti(interaction, "server.presentation.embed_footer"))

        try:
            # The embed version never pinged: keep the author mention silent.
            await self.target_channel.send(
                view=p.view(), allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            await interaction.response.send_message(
                ti(interaction, "server.presentation.post_forbidden"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            ti(interaction, "server.presentation.posted", channel=self.target_channel.mention),
            ephemeral=True,
        )


def setup_presentation_commands(bot: commands.Bot):

    presentation_group = app_commands.Group(
        name="presentation",
        description="Introduce yourself to the community",
    )

    @presentation_group.command(name="create", description="Open the introduction form")
    async def presentation_create(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                ti(interaction, "server.presentation.dm_unavailable"), ephemeral=True)
            return
        enabled = guild_setting_get(interaction.guild.id, "presentation_enabled", "0") == "1"
        ch_id = guild_setting_get(interaction.guild.id, "presentation_channel_id", "")
        if not enabled or not ch_id:
            await interaction.response.send_message(
                ti(interaction, "server.presentation.disabled"),
                ephemeral=True,
            )
            return
        # Restriction: only usable inside the configured channel
        if str(interaction.channel_id) != str(ch_id):
            await interaction.response.send_message(
                ti(interaction, "server.presentation.wrong_channel", channel_id=ch_id),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(ch_id))
        if not channel:
            await interaction.response.send_message(
                ti(interaction, "server.presentation.channel_missing"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(_PresentationModal(channel, locale_of(interaction)))

    @presentation_group.command(name="setup", description="Admin: configure the introductions channel")
    @app_commands.describe(channel="Channel where introductions will be posted",
                           state="Enable or disable introductions")
    @app_commands.choices(state=[
        app_commands.Choice(name="Enable", value="on"),
        app_commands.Choice(name="Disable", value="off"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def presentation_setup(interaction: discord.Interaction,
                                 channel: discord.TextChannel,
                                 state: app_commands.Choice[str]):
        if not interaction.guild:
            await interaction.response.send_message(
                ti(interaction, "server.presentation.dm_unavailable"), ephemeral=True)
            return
        guild_setting_set(interaction.guild.id, "presentation_channel_id", str(channel.id))
        guild_setting_set(interaction.guild.id, "presentation_enabled",
                          "1" if state.value == "on" else "0")
        state_txt = ti(interaction, "server.presentation.state_enabled" if state.value == "on"
                       else "server.presentation.state_disabled")
        await interaction.response.send_message(
            ti(interaction, "server.presentation.setup_done",
               state=state_txt, channel=channel.mention),
            ephemeral=True,
        )

    bot.tree.add_command(presentation_group)
