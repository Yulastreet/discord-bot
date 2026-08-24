import discord
from discord import app_commands

from services.i18n import ti

def setup_socialalert_commands(bot, deps):
    globals().update(deps)
    # ===== SOCIAL ALERTS (slash command) =====

    socialalert_group = app_commands.Group(
        name="socialalert",
        description="Twitch / YouTube / Reddit alerts (admin/mod only)",
        default_permissions=discord.Permissions(manage_guild=True),
    )


    @socialalert_group.command(name="add", description="Create a social alert (live, video, post)")
    @app_commands.describe(
        platform="twitch / youtube / reddit",
        username="Twitch username / YouTube @handle or UCxxx ID / Reddit username (or r/sub)",
        channel="Discord channel where the alerts are posted",
        message="Custom message (placeholders: {target}, {title}, {url}, {author}, {game}, {viewers})",
    )
    @app_commands.choices(platform=[
        app_commands.Choice(name="Twitch (live)",         value="twitch"),
        app_commands.Choice(name="YouTube (new video)",   value="youtube"),
        app_commands.Choice(name="Reddit (new post)",     value="reddit"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sa_add(
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        username: str,
        channel: discord.TextChannel,
        message: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        username = username.strip()
        plat = platform.value
        label = username
        target_id = username

        # YouTube @handle -> UC... resolution
        if plat == "youtube" and not username.startswith("UC"):
            resolved = await social.youtube_resolve_handle(username)
            if not resolved:
                await interaction.followup.send(
                    ti(interaction, "server.socialalert.youtube_unresolved", username=username),
                    ephemeral=True,
                )
                return
            target_id = resolved
            label = username  # keep the @handle for display

        # Twitch without credentials = blocked
        if plat == "twitch" and not (os.getenv("TWITCH_CLIENT_ID") and os.getenv("TWITCH_CLIENT_SECRET")):
            await interaction.followup.send(
                ti(interaction, "server.socialalert.twitch_no_creds"),
                ephemeral=True,
            )
            return

        aid = social_alert_create(
            guild_id=interaction.guild.id,
            platform=plat, target_id=target_id, target_label=label,
            channel_id=channel.id, message_template=message,
            created_by=interaction.user.id,
        )
        await interaction.followup.send(
            ti(interaction, "server.socialalert.created",
               platform=plat, alert_id=aid, target=label, channel=channel.mention),
            ephemeral=True,
        )


    @socialalert_group.command(name="list", description="List the active social alerts")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sa_list(interaction: discord.Interaction):
        rows = social_alerts_list(guild_id=interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                ti(interaction, "server.socialalert.none"), ephemeral=True,
            )
            return
        PLAT_EMOJI = {"twitch": "🟣", "youtube": "🔴", "reddit": "🟠"}
        parts = []
        for r in rows[:25]:
            emo = PLAT_EMOJI.get(r["platform"], "·")
            state = "✅" if r["enabled"] else "⏸️"
            parts.append(
                f"{state} `#{r['id']}` {emo} **{r['platform']}** · "
                f"`{r['target_label'] or r['target_id']}` → <#{r['channel_id']}>"
            )
        embed = discord.Embed(
            title=ti(interaction, "server.socialalert.list_title"),
            description="\n".join(parts),
            color=0xC8F050,
        )
        if len(rows) > 25:
            embed.set_footer(text=ti(interaction, "server.socialalert.list_more", count=len(rows) - 25))
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @socialalert_group.command(name="remove", description="Delete a social alert")
    @app_commands.describe(alert_id="Alert ID (shown by /socialalert list)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sa_remove(interaction: discord.Interaction, alert_id: int):
        n = social_alert_delete(alert_id, guild_id=interaction.guild.id)
        if n:
            await interaction.response.send_message(
                ti(interaction, "server.socialalert.removed", alert_id=alert_id), ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                ti(interaction, "server.socialalert.not_found", alert_id=alert_id), ephemeral=True,
            )


    bot.tree.add_command(socialalert_group)
