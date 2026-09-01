import os
import discord
from discord import app_commands

from services.i18n import t, ti, locale_of
from services.ui_v2 import Panel, row


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
        view = Panel(ti(interaction, "utils.vote.title"),
                     ti(interaction, "utils.vote.description", url=url)).view()
        await interaction.response.send_message(view=view, ephemeral=True)

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
        view = Panel(ti(interaction, "utils.invite.title"),
                     ti(interaction, "utils.invite.description", url=url)).view()
        await interaction.response.send_message(view=view, ephemeral=True)

    @bot.tree.command(name="userinfo", description="Info about a member")
    @app_commands.describe(member="The member you want info about")
    async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        p = Panel(ti(interaction, "utils.userinfo.title", name=member.name))
        p.thumbnail(member.display_avatar.url)
        p.field(ti(interaction, "utils.userinfo.field_name"), member.name, inline=True)
        p.field(ti(interaction, "utils.userinfo.field_id"), member.id, inline=True)
        p.field(ti(interaction, "utils.userinfo.field_created"),
                member.created_at.strftime("%d/%m/%Y"), inline=True)
        p.field(ti(interaction, "utils.userinfo.field_joined"),
                member.joined_at.strftime("%d/%m/%Y"), inline=True)
        p.field(ti(interaction, "utils.userinfo.field_top_role"), member.top_role.mention)
        await interaction.response.send_message(view=p.view())

    @bot.tree.command(name="serverinfo", description="Info about the server")
    async def serverinfo(interaction: discord.Interaction):
        guild = interaction.guild
        p = Panel(ti(interaction, "utils.serverinfo.title", name=guild.name))
        if guild.icon:
            p.thumbnail(guild.icon.url)
        p.field(ti(interaction, "utils.serverinfo.field_id"), f"`{guild.id}`")
        p.field(ti(interaction, "utils.serverinfo.field_owner"), guild.owner, inline=True)
        p.field(ti(interaction, "utils.serverinfo.field_members"), guild.member_count, inline=True)
        p.field(ti(interaction, "utils.serverinfo.field_created"),
                guild.created_at.strftime("%d/%m/%Y"), inline=True)
        p.field(ti(interaction, "utils.serverinfo.field_channels"), len(guild.channels), inline=True)
        p.field(ti(interaction, "utils.serverinfo.field_roles"), len(guild.roles), inline=True)
        await interaction.response.send_message(view=p.view())

    @bot.tree.command(name="avatar", description="Show a member's avatar")
    @app_commands.describe(member="The member whose avatar you want to see")
    async def avatar(interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        p = Panel(ti(interaction, "utils.avatar.title", name=member.name))
        p.image(member.display_avatar.url)
        await interaction.response.send_message(view=p.view())

    @bot.tree.command(name="commands", description="Get the command list in DM (button navigation)")
    async def commands_cmd(interaction: discord.Interaction):
        loc   = locale_of(interaction)
        pages = _build_command_pages(loc)
        view  = CommandsPaginatorView(pages, owner_id=interaction.user.id, locale=loc)
        try:
            await interaction.user.send(view=view)
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
    """Build the list of help panels (6 dense pages, Components V2)."""
    pages = []
    total = len(_PAGE_LAYOUT)
    for i, (page_key, field_keys) in enumerate(_PAGE_LAYOUT, start=1):
        p = Panel(t(f"utils.help.{page_key}.title", locale))
        for fk in field_keys:
            p.field(t(f"utils.help.{page_key}.{fk}_name", locale),
                    t(f"utils.help.{page_key}.{fk}_value", locale))
        p.footer(t("utils.help.footer", locale, n=i, total=total))
        pages.append(p)
    return pages


class _PagerRow(discord.ui.ActionRow):
    """Navigation row. custom_ids kept identical to the pre-V2 version."""

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="cmds:prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = self.view
        if not await v._guard(interaction):
            return
        if v.idx > 0:
            v.idx -= 1
        v._rebuild()
        await interaction.response.edit_message(view=v)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary,
                       custom_id="cmds:counter", disabled=True)
    async def counter_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # disabled, display only

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="cmds:next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = self.view
        if not await v._guard(interaction):
            return
        if v.idx < len(v.pages) - 1:
            v.idx += 1
        v._rebuild()
        await interaction.response.edit_message(view=v)

    @discord.ui.button(label="✖ Close", style=discord.ButtonStyle.danger, custom_id="cmds:close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        v = self.view
        if not await v._guard(interaction):
            return
        try:
            # A V2 message cannot fall back to plain content: replace the whole view.
            closed = Panel(description=ti(interaction, "utils.help.closed")).view(timeout=None)
            await interaction.response.edit_message(view=closed)
        except Exception:
            pass


class CommandsPaginatorView(discord.ui.LayoutView):
    def __init__(self, pages: list, owner_id: int, locale: str = "en"):
        super().__init__(timeout=180)
        self.pages    = pages
        self.idx      = 0
        self.owner_id = owner_id
        self.locale   = locale
        self.pager    = _PagerRow()
        self.pager.close_btn.label = t("utils.help.btn_close", locale)
        self._rebuild()

    def _rebuild(self):
        """Swap the container for the current page and refresh the nav state."""
        self.clear_items()
        self.add_item(self.pages[self.idx].container())
        self.pager.prev_btn.disabled = (self.idx == 0)
        self.pager.next_btn.disabled = (self.idx >= len(self.pages) - 1)
        self.pager.counter_btn.label = f"{self.idx + 1} / {len(self.pages)}"
        self.add_item(self.pager)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message(
                    ti(interaction, "utils.help.not_yours"), ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True
