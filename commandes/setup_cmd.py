"""Slash /setup: initial configuration builder.

Configures the 4 essential bot channels (welcome/logs/alerts/admin).
When the user is the server OWNER and the mod config has never been done,
it creates a private temporary channel with a builder to pick the mod role
and the permissions granted to the mods.

Permissions: manage_guild required.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import guild_setting_set, guild_setting_get
from services.i18n import DEFAULT_LOCALE, guild_locale, locale_of, t, ti


# Setting keys of the 4 channels configured by /setup.
# Labels/hints live in locales/<lang>/utils.json under utils.setup.field.*
_SETUP_FIELDS = ["welcome", "logs", "alerts", "admin"]


# ===== Toggleable permissions for mods =====
# (key, kind) - label + description come from utils.setup.modperms.perm.<key>_*
MOD_PERMS_REGISTRY = [
    ("warn",            "slash"),
    ("kick",            "slash"),
    ("ban",             "slash"),
    ("clear",           "slash"),
    ("ticket",          "slash"),
    ("giveaway",        "slash"),
    ("poll",            "slash"),
    ("rolereaction",    "slash"),
    ("socialalert",     "slash"),
    ("setwelcome",      "slash"),
    ("reaction",        "slash"),
    ("modlogs",         "slash"),
    ("setup",           "slash"),
    ("note",            "slash"),
    ("logs",            "dashboard"),
    ("custom_commands", "dashboard"),
    ("music",           "dashboard"),
    ("features",        "dashboard"),
    ("settings",        "dashboard"),
]


class ModPermsView(discord.ui.View):
    """View in the temp channel: RoleSelect + 2 perm multi-selects + Save/Close."""

    def __init__(self, guild_id: int, owner_user_id: int, temp_channel_id: int):
        super().__init__(timeout=3600)
        self.guild_id = int(guild_id)
        self.owner_user_id = int(owner_user_id)
        self.temp_channel_id = int(temp_channel_id)
        self.selected_role: int | None = None
        self.selected_slash: set[str] = set()
        self.selected_dash: set[str] = set()
        # No interaction here: fall back to the guild locale override.
        self.locale = guild_locale(self.guild_id) or DEFAULT_LOCALE
        loc = self.locale

        # Populate both multi-select option lists dynamically
        slash_perms = [p for p in MOD_PERMS_REGISTRY if p[1] == "slash"]
        dash_perms  = [p for p in MOD_PERMS_REGISTRY if p[1] == "dashboard"]

        self.role_select.placeholder  = t("utils.setup.modperms.ph_role", loc)
        self.slash_select.placeholder = t("utils.setup.modperms.ph_slash", loc)
        self.dash_select.placeholder  = t("utils.setup.modperms.ph_dash", loc)
        self.btn_save.label   = t("utils.setup.modperms.btn_save", loc)
        self.btn_cancel.label = t("utils.setup.modperms.btn_cancel", loc)

        self.slash_select.options = [
            discord.SelectOption(
                label=t(f"utils.setup.modperms.perm.{key}_label", loc),
                value=key,
                description=t(f"utils.setup.modperms.perm.{key}_desc", loc)[:100],
            )
            for key, _ in slash_perms
        ]
        self.slash_select.max_values = len(slash_perms)
        self.dash_select.options = [
            discord.SelectOption(
                label=t(f"utils.setup.modperms.perm.{key}_label", loc),
                value=key,
                description=t(f"utils.setup.modperms.perm.{key}_desc", loc)[:100],
            )
            for key, _ in dash_perms
        ]
        self.dash_select.max_values = len(dash_perms)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the server owner may interact
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message(
                ti(interaction, "utils.setup.modperms.not_owner"),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Moderator role",
        min_values=1, max_values=1, row=0,
    )
    async def role_select(self, interaction: discord.Interaction, select):
        self.selected_role = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Slash commands",
        min_values=0, row=1,
    )
    async def slash_select(self, interaction: discord.Interaction, select):
        self.selected_slash = set(select.values)
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Dashboard pages",
        min_values=0, row=2,
    )
    async def dash_select(self, interaction: discord.Interaction, select):
        self.selected_dash = set(select.values)
        await interaction.response.defer()

    @discord.ui.button(label="Save", emoji="✅",
                       style=discord.ButtonStyle.success, row=3)
    async def btn_save(self, interaction: discord.Interaction, button):
        if not self.selected_role:
            await interaction.response.send_message(
                ti(interaction, "utils.setup.modperms.no_role"), ephemeral=True,
            )
            return
        # Save role
        guild_setting_set(self.guild_id, "mod_role_id", str(self.selected_role))
        # Save perms (1 for the selected ones, 0 for the rest)
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
            title=ti(interaction, "utils.setup.modperms.saved_title"),
            description=ti(
                interaction, "utils.setup.modperms.saved_description",
                role_id=self.selected_role,
                n_slash=len(self.selected_slash),
                t_slash=sum(1 for p in MOD_PERMS_REGISTRY if p[1] == "slash"),
                n_dash=len(self.selected_dash),
                t_dash=sum(1 for p in MOD_PERMS_REGISTRY if p[1] == "dashboard"),
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
                await ch.delete(reason=t("utils.setup.modperms.delete_reason_done", self.locale))
        except Exception as e:
            print(f"[setup] temp channel delete fail: {e}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=3)
    async def btn_cancel(self, interaction: discord.Interaction, button):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content=ti(interaction, "utils.setup.modperms.cancelled"),
            embed=None, view=self,
        )
        import asyncio
        await asyncio.sleep(10)
        try:
            ch = interaction.guild.get_channel(self.temp_channel_id)
            if ch:
                await ch.delete(reason=t("utils.setup.modperms.delete_reason_cancel", self.locale))
        except Exception:
            pass


async def _create_mod_perms_temp_channel(guild: discord.Guild, server_owner: discord.Member):
    """Create a private temp channel (owner + bot) holding the mod perms builder."""
    loc = guild_locale(guild.id) or DEFAULT_LOCALE
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me:           discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True),
        server_owner:       discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True),
    }
    try:
        channel = await guild.create_text_channel(
            name=t("utils.setup.modperms.channel_name", loc),
            overwrites=overwrites,
            reason=t("utils.setup.modperms.channel_reason", loc, owner=server_owner),
        )
    except discord.HTTPException as e:
        print(f"[setup] create temp channel fail: {e}")
        return None

    embed = discord.Embed(
        title=t("utils.setup.modperms.intro_title", loc),
        description=t("utils.setup.modperms.intro_description", loc,
                      owner=server_owner.mention),
        color=0xB9F23A,
    )
    embed.set_footer(text=t("utils.setup.modperms.intro_footer", loc))

    view = ModPermsView(guild.id, server_owner.id, channel.id)
    try:
        await channel.send(content=server_owner.mention, embed=embed, view=view)
    except discord.HTTPException as e:
        print(f"[setup] send temp channel fail: {e}")
    return channel


class SetupView(discord.ui.View):
    """Main /setup view: 4 ChannelSelect + Save/Cancel."""

    def __init__(self, guild_id: int, *, current: dict[str, str] | None = None,
                 server_owner: discord.Member | None = None,
                 locale: str = DEFAULT_LOCALE):
        super().__init__(timeout=900)
        self.guild_id = int(guild_id)
        self.server_owner = server_owner
        self.locale = locale
        self.selections: dict[str, int] = {}
        if current:
            for k, v in current.items():
                if v:
                    try:
                        self.selections[k] = int(v)
                    except (TypeError, ValueError):
                        pass
        self.s_welcome.placeholder = t("utils.setup.view.ph_welcome", locale)
        self.s_logs.placeholder    = t("utils.setup.view.ph_logs", locale)
        self.s_alerts.placeholder  = t("utils.setup.view.ph_alerts", locale)
        self.s_admin.placeholder   = t("utils.setup.view.ph_admin", locale)
        self.btn_save.label        = t("utils.setup.view.btn_save", locale)
        self.btn_cancel.label      = t("utils.setup.view.btn_cancel", locale)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Welcome channel",
        min_values=1, max_values=1, row=0,
    )
    async def s_welcome(self, interaction, select):
        self.selections["welcome"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Logs channel",
        min_values=1, max_values=1, row=1,
    )
    async def s_logs(self, interaction, select):
        self.selections["logs"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Alerts channel",
        min_values=1, max_values=1, row=2,
    )
    async def s_alerts(self, interaction, select):
        self.selections["alerts"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Admin/mod channel",
        min_values=1, max_values=1, row=3,
    )
    async def s_admin(self, interaction, select):
        self.selections["admin"] = select.values[0].id
        await interaction.response.defer()

    @discord.ui.button(label="Save the configuration",
                       style=discord.ButtonStyle.success, emoji="✅", row=4)
    async def btn_save(self, interaction: discord.Interaction, button):
        if not self.selections:
            await interaction.response.send_message(
                ti(interaction, "utils.setup.save.no_selection"),
                ephemeral=True,
            )
            return
        for key, cid in self.selections.items():
            guild_setting_set(self.guild_id, f"setup_{key}_channel_id", str(cid))
        guild_setting_set(self.guild_id, "setup_completed", "1")

        embed = discord.Embed(
            title=ti(interaction, "utils.setup.save.title"),
            description=ti(interaction, "utils.setup.save.description"),
            color=0xB9F23A,
        )
        for key in _SETUP_FIELDS:
            label = ti(interaction, f"utils.setup.field.{key}_label")
            cid = self.selections.get(key) or guild_setting_get(self.guild_id, f"setup_{key}_channel_id", "")
            if cid:
                embed.add_field(name=label, value=f"<#{cid}>", inline=False)
            else:
                embed.add_field(name=label,
                                value=ti(interaction, "utils.setup.not_configured"),
                                inline=False)

        # If the user IS the server owner and mod_access_configured=0 -> second step
        mod_configured = guild_setting_get(self.guild_id, "mod_access_configured", "0") == "1"
        is_owner_target = (interaction.guild.owner_id == interaction.user.id)
        if is_owner_target and not mod_configured:
            next_step_msg = ti(interaction, "utils.setup.save.next_owner")
        else:
            next_step_msg = ti(interaction, "utils.setup.save.next_again")
        embed.description = embed.description + next_step_msg

        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

        # Create the temp channel when owner + mod config never done
        if is_owner_target and not mod_configured and self.server_owner:
            try:
                await _create_mod_perms_temp_channel(interaction.guild, self.server_owner)
            except Exception as e:
                print(f"[setup] mod perms channel create err: {e}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=4)
    async def btn_cancel(self, interaction: discord.Interaction, button):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content=ti(interaction, "utils.setup.cancel.message"),
            embed=None, view=self,
        )


def setup_setup_commands(bot: commands.Bot):

    @bot.tree.command(name="setup",
                      description="⚠️ Configure the bot channels + mod permissions (admin/mod)")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_cmd(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                ti(interaction, "utils.setup.guild_only"), ephemeral=True)
            return

        loc = locale_of(interaction)

        current = {}
        for key in _SETUP_FIELDS:
            current[key] = guild_setting_get(interaction.guild.id, f"setup_{key}_channel_id", "")

        not_configured = ti(interaction, "utils.setup.not_configured")
        lines = []
        for key in _SETUP_FIELDS:
            cid = current.get(key)
            cur_str = f"<#{cid}>" if cid else not_configured
            lines.append(ti(
                interaction, "utils.setup.main.line",
                label=ti(interaction, f"utils.setup.field.{key}_label"),
                hint=ti(interaction, f"utils.setup.field.{key}_hint"),
                current=cur_str,
            ))
        body = "\n\n".join(lines)

        embed = discord.Embed(
            title=ti(interaction, "utils.setup.main.title"),
            description=ti(interaction, "utils.setup.main.description", body=body),
            color=0xFF6B35,
        )

        # Owner + mod config never done: warn that a 2nd step is coming
        is_owner_target = (interaction.guild.owner_id == interaction.user.id)
        mod_configured = guild_setting_get(interaction.guild.id, "mod_access_configured", "0") == "1"
        if is_owner_target and not mod_configured:
            embed.add_field(
                name=ti(interaction, "utils.setup.main.next_step_name"),
                value=ti(interaction, "utils.setup.main.next_step_value"),
                inline=False,
            )

        embed.set_footer(text=ti(interaction, "utils.setup.main.footer"))

        # Grab the owner Member for the temp channel overwrites
        owner_member = interaction.guild.owner

        view = SetupView(interaction.guild.id, current=current,
                         server_owner=owner_member, locale=loc)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
