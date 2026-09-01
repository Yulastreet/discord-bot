import discord
from discord import app_commands

from database import get_welcome, set_welcome
from services.i18n import locale_of, t
from services.ui_v2 import Panel, row
from services.welcome_utils import DEFAULT_WELCOME_MESSAGE


class _WelcomeMessageModal(discord.ui.Modal):
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=1800,
    )

    def __init__(self, parent_view):
        super().__init__(title=t("server.welcome.modal_title", parent_view.locale))
        self.parent_view = parent_view
        self.message.label = t("server.welcome.modal_message_label", parent_view.locale)
        self.message.placeholder = t("server.welcome.modal_message_placeholder", parent_view.locale)
        self.message.default = parent_view.message or DEFAULT_WELCOME_MESSAGE

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.message = self.message.value
        await self.parent_view.refresh(interaction)


class _WelcomeBuilderView(discord.ui.LayoutView):
    def __init__(self, author_id: int, guild: discord.Guild, locale: str = "en"):
        super().__init__(timeout=900)
        self.author_id = author_id
        self.guild = guild
        self.locale = locale
        current = get_welcome(guild.id)
        self.channel = guild.get_channel(current["channel_id"]) if current else None
        self.message = (current or {}).get("message") or DEFAULT_WELCOME_MESSAGE
        self._rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                t("server.welcome.not_your_builder", self.locale), ephemeral=True)
            return False
        return True

    def _summary_panel(self) -> Panel:
        preview = self.message or DEFAULT_WELCOME_MESSAGE
        p = Panel(t("server.welcome.builder_title", self.locale))
        p.field(
            t("server.welcome.field_config", self.locale),
            t(
                "server.welcome.config_value", self.locale,
                channel=self.channel.mention if self.channel else t("server.welcome.not_set", self.locale),
                message=(preview[:400] + "...") if len(preview) > 400 else preview,
            ),
            inline=False,
        )
        p.field(
            t("server.welcome.field_variables", self.locale),
            t("server.welcome.variables_value", self.locale),
            inline=False,
        )
        p.footer(t("server.welcome.builder_footer", self.locale))
        return p

    def _rebuild(self):
        self.clear_items()
        # The summary is now the container of the V2 message, not a side embed.
        self.add_item(self._summary_panel().container())

        chan_sel = discord.ui.ChannelSelect(
            placeholder=t("server.welcome.select_channel", self.locale),
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

        async def _on_chan(interaction: discord.Interaction):
            ch = self.guild.get_channel(chan_sel.values[0].id)
            if isinstance(ch, discord.TextChannel):
                self.channel = ch
            await self.refresh(interaction)

        chan_sel.callback = _on_chan
        self.add_item(row(chan_sel))

        btn_message = discord.ui.Button(label=t("server.welcome.btn_edit_message", self.locale),
                                        style=discord.ButtonStyle.secondary)

        async def _on_message(interaction: discord.Interaction):
            await interaction.response.send_modal(_WelcomeMessageModal(self))

        btn_message.callback = _on_message

        btn_save = discord.ui.Button(
            label=t("server.welcome.btn_save", self.locale),
            style=discord.ButtonStyle.success,
            disabled=not self.channel,
        )
        btn_save.callback = self._on_save

        btn_cancel = discord.ui.Button(label=t("server.welcome.btn_cancel", self.locale),
                                       style=discord.ButtonStyle.danger)

        async def _on_cancel(interaction: discord.Interaction):
            # A V2 message has no `content`: replace the whole view.
            closed = Panel(description=t("server.welcome.cancelled", self.locale)).view(timeout=None)
            await interaction.response.edit_message(view=closed)
            self.stop()

        btn_cancel.callback = _on_cancel
        self.add_item(row(btn_message, btn_save, btn_cancel))

    async def refresh(self, interaction: discord.Interaction):
        self._rebuild()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def _on_save(self, interaction: discord.Interaction):
        if not self.channel:
            await interaction.response.send_message(
                t("server.welcome.pick_channel_first", self.locale), ephemeral=True)
            return
        set_welcome(interaction.guild.id, self.channel.id, self.message)
        # A V2 message has no `content`: replace the whole view.
        done = Panel(description=t("server.welcome.saved", self.locale,
                                   channel=self.channel.mention)).view(timeout=None)
        await interaction.response.edit_message(view=done)
        self.stop()


def setup_welcome_commands(bot):
    @bot.tree.command(name="setwelcome", description="Open the welcome message builder")
    @app_commands.describe(channel="Optional channel preselection")
    @app_commands.default_permissions(manage_guild=True)
    async def setwelcome(interaction: discord.Interaction, channel: discord.TextChannel = None):
        view = _WelcomeBuilderView(interaction.user.id, interaction.guild, locale_of(interaction))
        if channel:
            view.channel = channel
            view._rebuild()
        await interaction.response.send_message(view=view, ephemeral=True)
