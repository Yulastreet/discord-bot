import discord
from discord import app_commands

from services.i18n import ti


def setup_moderation_commands(bot):
    @bot.tree.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(member="The member to kick", reason="Reason for the kick")
    @app_commands.default_permissions(kick_members=True)
    async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                ti(interaction, "moderation.kick.forbidden", member=member.name), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                ti(interaction, "moderation.kick.http_error", member=member.name), ephemeral=True)
            return
        await interaction.response.send_message(
            ti(interaction, "moderation.kick.success", member=member.name, reason=reason))

    @bot.tree.command(name="ban", description="Ban a member from the server")
    @app_commands.describe(member="The member to ban", reason="Reason for the ban")
    @app_commands.default_permissions(ban_members=True)
    async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        try:
            await member.ban(reason=reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                ti(interaction, "moderation.ban.forbidden", member=member.name), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                ti(interaction, "moderation.ban.http_error", member=member.name), ephemeral=True)
            return
        await interaction.response.send_message(
            ti(interaction, "moderation.ban.success", member=member.name, reason=reason))

    @bot.tree.command(name="clear", description="Delete a number of messages in this channel")
    @app_commands.describe(amount="How many messages to delete")
    @app_commands.default_permissions(manage_messages=True)
    async def clear(interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.channel.purge(limit=amount)
        except discord.Forbidden:
            await interaction.followup.send(
                ti(interaction, "moderation.clear.forbidden"), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send(
                ti(interaction, "moderation.clear.http_error"), ephemeral=True)
            return
        await interaction.followup.send(
            ti(interaction, "moderation.clear.success", amount=amount), ephemeral=True)

    class PollBuilderModal(discord.ui.Modal, title="Create a poll"):
        question = discord.ui.TextInput(
            label="Question",
            placeholder="e.g. What pizza tonight?",
            max_length=300,
            required=True,
        )
        options = discord.ui.TextInput(
            label="Options (one per line, 2 to 10)",
            style=discord.TextStyle.paragraph,
            placeholder="Four cheese\nMargherita\nPepperoni",
            max_length=600,
            required=True,
        )
        duration = discord.ui.TextInput(
            label="Duration (in hours, 1 to 168)",
            placeholder="24",
            default="24",
            max_length=3,
            required=False,
        )

        async def on_submit(self, interaction: discord.Interaction):
            import datetime as _dt
            opts = [ln.strip() for ln in str(self.options.value).splitlines() if ln.strip()]
            if len(opts) < 2:
                await interaction.response.send_message(
                    ti(interaction, "moderation.poll.need_two_options"), ephemeral=True,
                )
                return
            if len(opts) > 10:
                opts = opts[:10]
            # Parse duration, clamp [1, 168]h (= 7 days max, Discord limit)
            try:
                dh = int(str(self.duration.value).strip() or "24")
            except ValueError:
                dh = 24
            dh = max(1, min(168, dh))
            try:
                poll = discord.Poll(
                    question=str(self.question.value).strip()[:300],
                    duration=_dt.timedelta(hours=dh),
                )
                for o in opts:
                    poll.add_answer(text=o[:55])
                await interaction.response.send_message(poll=poll)
            except Exception as e:
                print(f"[moderation/poll] err: {e!r}")
                await interaction.response.send_message(
                    ti(interaction, "moderation.poll.create_failed"), ephemeral=True,
                )

    @bot.tree.command(name="poll", description="Create a poll (opens a builder)")
    async def poll(interaction: discord.Interaction):
        # Opens the creation modal. The poll uses Discord's native component
        # (live voting + built-in UI). For 5+ options or more control,
        # use the dashboard builder.
        await interaction.response.send_modal(PollBuilderModal())
