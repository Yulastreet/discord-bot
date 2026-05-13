import discord
from discord import app_commands

from database import get_welcome, set_welcome
from welcome_utils import DEFAULT_WELCOME_MESSAGE


class _WelcomeMessageModal(discord.ui.Modal, title="Message de bienvenue"):
    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        placeholder="Ex: Coucou @pseudo sur le serveur, installe toi !",
    )

    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view
        self.message.default = parent_view.message or DEFAULT_WELCOME_MESSAGE

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.message = self.message.value
        await self.parent_view.refresh(interaction)


class _WelcomeBuilderView(discord.ui.View):
    def __init__(self, author_id: int, guild: discord.Guild):
        super().__init__(timeout=900)
        self.author_id = author_id
        self.guild = guild
        current = get_welcome(guild.id)
        self.salon = guild.get_channel(current["channel_id"]) if current else None
        self.message = (current or {}).get("message") or DEFAULT_WELCOME_MESSAGE
        self._rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Ce builder n'est pas le tien.", ephemeral=True)
            return False
        return True

    def _summary_embed(self) -> discord.Embed:
        preview = self.message or DEFAULT_WELCOME_MESSAGE
        embed = discord.Embed(title="Builder bienvenue", color=discord.Color.green())
        embed.add_field(
            name="Configuration",
            value=(
                f"**Salon :** {self.salon.mention if self.salon else '_non defini_'}\n"
                f"**Message :** {(preview[:400] + '...') if len(preview) > 400 else preview}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Variables",
            value="`@pseudo` ou `{user}` = mention du membre, `{username}` = pseudo, `{guild}` = serveur, `{count}` = nombre de membres.",
            inline=False,
        )
        embed.set_footer(text="Le builder expire dans 15 minutes.")
        return embed

    def _rebuild(self):
        self.clear_items()
        chan_sel = discord.ui.ChannelSelect(
            placeholder="Salon ou envoyer les bienvenues...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=0,
        )

        async def _on_chan(interaction: discord.Interaction):
            ch = self.guild.get_channel(chan_sel.values[0].id)
            if isinstance(ch, discord.TextChannel):
                self.salon = ch
            await self.refresh(interaction)

        chan_sel.callback = _on_chan
        self.add_item(chan_sel)

        btn_message = discord.ui.Button(label="Modifier le message", style=discord.ButtonStyle.secondary, row=1)

        async def _on_message(interaction: discord.Interaction):
            await interaction.response.send_modal(_WelcomeMessageModal(self))

        btn_message.callback = _on_message
        self.add_item(btn_message)

        btn_save = discord.ui.Button(
            label="Enregistrer",
            style=discord.ButtonStyle.success,
            row=1,
            disabled=not self.salon,
        )
        btn_save.callback = self._on_save
        self.add_item(btn_save)

        btn_cancel = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.danger, row=1)

        async def _on_cancel(interaction: discord.Interaction):
            self.clear_items()
            await interaction.response.edit_message(content="Builder annule.", embed=None, view=None)
            self.stop()

        btn_cancel.callback = _on_cancel
        self.add_item(btn_cancel)

    async def refresh(self, interaction: discord.Interaction):
        self._rebuild()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self._summary_embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self._summary_embed(), view=self)

    async def _on_save(self, interaction: discord.Interaction):
        if not self.salon:
            await interaction.response.send_message("Choisis d'abord un salon.", ephemeral=True)
            return
        set_welcome(interaction.guild.id, self.salon.id, self.message)
        self.clear_items()
        await interaction.response.edit_message(
            content=f"Bienvenue configure dans {self.salon.mention}.",
            embed=None,
            view=None,
        )
        self.stop()


def setup_welcome_commands(bot):
    @bot.tree.command(name="setwelcome", description="Ouvrir le builder de bienvenue")
    @app_commands.describe(salon="Preselection optionnelle du salon")
    @app_commands.default_permissions(administrator=True)
    async def setwelcome(interaction: discord.Interaction, salon: discord.TextChannel = None):
        view = _WelcomeBuilderView(interaction.user.id, interaction.guild)
        if salon:
            view.salon = salon
            view._rebuild()
        await interaction.response.send_message(embed=view._summary_embed(), view=view, ephemeral=True)
