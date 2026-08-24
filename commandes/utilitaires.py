import os
import discord
from discord import app_commands

from services.i18n import t, ti, locale_of


def setup_utility_commands(bot):
    @bot.tree.command(name="ping", description="Check the bot latency")
    async def ping(interaction: discord.Interaction):
        latency = round(bot.latency * 1000)
        await interaction.response.send_message(
            ti(interaction, "utils.ping.result", latency=latency))

    @bot.tree.command(name="vote", description="Vote for TookBot on top.gg")
    async def vote(interaction: discord.Interaction):
        bot_id = (os.getenv("DISCORD_BOT_ID") or "").strip()
        if not bot_id and bot.user:
            bot_id = str(bot.user.id)
        url = f"https://top.gg/bot/{bot_id}/vote" if bot_id else "https://top.gg/"
        embed = discord.Embed(
            title=ti(interaction, "utils.vote.title"),
            description=ti(interaction, "utils.vote.description", url=url),
            color=0xff3d57,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="invite", description="Link to invite TookBot to your server")
    async def invite(interaction: discord.Interaction):
        bot_id = (os.getenv("DISCORD_BOT_ID") or "").strip()
        if not bot_id and bot.user:
            bot_id = str(bot.user.id)
        # Permissions integer (scope precis, PAS d'Administrator) :
        # View Channels + Send Messages + Embed Links + Attach Files +
        # Read History + Add Reactions + External Emojis + Manage Messages +
        # Manage Roles + Manage Channels + Kick + Ban + Moderate Members (timeout) +
        # Connect + Speak + Mute Members + Move Members + Use Slash Commands
        perms = "1101952052310"
        url = (f"https://discord.com/oauth2/authorize?client_id={bot_id}"
                f"&permissions={perms}&scope=bot+applications.commands")
        embed = discord.Embed(
            title=ti(interaction, "utils.invite.title"),
            description=ti(interaction, "utils.invite.description", url=url),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="userinfo", description="Info about a member")
    @app_commands.describe(member="The member you want info about")
    async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        embed = discord.Embed(
            title=ti(interaction, "utils.userinfo.title", name=member.name),
            color=member.color,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=ti(interaction, "utils.userinfo.field_name"), value=member.name)
        embed.add_field(name=ti(interaction, "utils.userinfo.field_id"), value=member.id)
        embed.add_field(name=ti(interaction, "utils.userinfo.field_created"),
                        value=member.created_at.strftime("%d/%m/%Y"))
        embed.add_field(name=ti(interaction, "utils.userinfo.field_joined"),
                        value=member.joined_at.strftime("%d/%m/%Y"))
        embed.add_field(name=ti(interaction, "utils.userinfo.field_top_role"),
                        value=member.top_role.mention)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="serverinfo", description="Info about the server")
    async def serverinfo(interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(
            title=ti(interaction, "utils.serverinfo.title", name=guild.name),
            color=discord.Color.blue(),
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.add_field(name=ti(interaction, "utils.serverinfo.field_id"),
                        value=f"`{guild.id}`", inline=False)
        embed.add_field(name=ti(interaction, "utils.serverinfo.field_owner"), value=guild.owner)
        embed.add_field(name=ti(interaction, "utils.serverinfo.field_members"),
                        value=guild.member_count)
        embed.add_field(name=ti(interaction, "utils.serverinfo.field_created"),
                        value=guild.created_at.strftime("%d/%m/%Y"))
        embed.add_field(name=ti(interaction, "utils.serverinfo.field_channels"),
                        value=len(guild.channels))
        embed.add_field(name=ti(interaction, "utils.serverinfo.field_roles"), value=len(guild.roles))
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="avatar", description="Show a member's avatar")
    @app_commands.describe(member="The member whose avatar you want to see")
    async def avatar(interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        embed = discord.Embed(
            title=ti(interaction, "utils.avatar.title", name=member.name),
            color=discord.Color.blue(),
        )
        embed.set_image(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="commands", description="Get the command list in DM (button navigation)")
    async def commands_cmd(interaction: discord.Interaction):
        loc   = locale_of(interaction)
        pages = _build_command_pages(loc)
        view  = CommandsPaginatorView(pages, owner_id=interaction.user.id, locale=loc)
        try:
            await interaction.user.send(embed=pages[0], view=view)
            await interaction.response.send_message(
                ti(interaction, "utils.help.dm_sent"),
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                ti(interaction, "utils.help.dm_blocked"),
                ephemeral=True,
            )
        except Exception as e:
            print(f"[commands] DM send err: {type(e).__name__}: {e}")
            await interaction.response.send_message(
                ti(interaction, "utils.help.send_error"), ephemeral=True,
            )


_PAGE_COLOR = 0x3498DB

# (page key, [field key prefix, ...]) -> drives the embed build below.
_PAGE_LAYOUT = [
    ("p1", ["moderation", "tempvoice", "tools"]),
    ("p2", ["fun", "xp", "duel", "music"]),
    ("p3", ["cs", "utils"]),
    ("p4", ["collection", "essences", "fusion", "events"]),
    ("p5", ["profile", "wishlist", "trade", "combat", "setup"]),
    ("p6", ["base", "members", "xp"]),
]


def _build_command_pages(locale: str) -> list:
    """Build the list of help embeds (6 dense pages)."""
    pages = []
    for page_key, field_keys in _PAGE_LAYOUT:
        embed = discord.Embed(
            title=t(f"utils.help.{page_key}.title", locale),
            color=_PAGE_COLOR,
        )
        for fk in field_keys:
            embed.add_field(
                name=t(f"utils.help.{page_key}.{fk}_name", locale),
                value=t(f"utils.help.{page_key}.{fk}_value", locale),
                inline=False,
            )
        pages.append(embed)

    for i, e in enumerate(pages, start=1):
        e.set_footer(text=t("utils.help.footer", locale, n=i, total=len(pages)))
    return pages


class CommandsPaginatorView(discord.ui.View):
    def __init__(self, pages: list, owner_id: int, locale: str = "en"):
        super().__init__(timeout=180)
        self.pages    = pages
        self.idx      = 0
        self.owner_id = owner_id
        self.locale   = locale
        self.close_btn.label = t("utils.help.btn_close", locale)
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
                    ti(interaction, "utils.help.not_yours"),
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
        pass  # disabled, display only

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="cmds:next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        if self.idx < len(self.pages) - 1:
            self.idx += 1
        self._refresh_state()
        await interaction.response.edit_message(embed=self.pages[self.idx], view=self)

    @discord.ui.button(label="✖ Close", style=discord.ButtonStyle.danger, custom_id="cmds:close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        try:
            await interaction.response.edit_message(
                content=ti(interaction, "utils.help.closed"), embed=None, view=None,
            )
        except Exception:
            pass
