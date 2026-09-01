import discord
from discord import app_commands

from database import remove_reaction, set_reaction
from services.i18n import ti
from services.ui_v2 import Panel


def setup_reaction_commands(bot, user_reactions):
    @bot.tree.command(name="reaction_add", description="Add an automatic reaction on a member's messages")
    @app_commands.describe(member="The target member", emoji="The emoji to use")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reaction_add(interaction: discord.Interaction, member: discord.Member, emoji: str):
        gid = str(interaction.guild.id)
        user_reactions[(gid, member.id)] = emoji
        set_reaction(gid, member.id, emoji)
        await interaction.response.send_message(
            ti(interaction, "server.reactions.added", emoji=emoji, member=member.name)
        )

    @reaction_add.error
    async def reaction_add_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(
                ti(interaction, "server.reactions.no_perm"), ephemeral=True)

    @bot.tree.command(name="reaction_remove", description="Remove the automatic reaction of a member")
    @app_commands.describe(member="The member whose reaction should be removed")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reaction_remove(interaction: discord.Interaction, member: discord.Member):
        gid = str(interaction.guild.id)
        key = (gid, member.id)
        if key in user_reactions:
            del user_reactions[key]
            remove_reaction(gid, member.id)
            await interaction.response.send_message(
                ti(interaction, "server.reactions.removed", member=member.name)
            )
        else:
            await interaction.response.send_message(
                ti(interaction, "server.reactions.none_for_member", member=member.name),
                ephemeral=True,
            )

    @reaction_remove.error
    async def reaction_remove_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(
                ti(interaction, "server.reactions.no_perm"), ephemeral=True)

    @bot.tree.command(name="reaction_list", description="Show the active automatic reactions")
    async def reaction_list(interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        guild_reactions = {uid: emo for (g, uid), emo in user_reactions.items() if g == gid}
        if not guild_reactions:
            await interaction.response.send_message(
                ti(interaction, "server.reactions.none"),
                ephemeral=True,
            )
            return
        p = Panel(ti(interaction, "server.reactions.list_title"))
        for user_id, emoji in guild_reactions.items():
            member = interaction.guild.get_member(user_id)
            name = member.name if member else ti(interaction, "server.reactions.unknown_member",
                                                 user_id=user_id)
            p.field(name, emoji, inline=True)
        await interaction.response.send_message(view=p.view())
