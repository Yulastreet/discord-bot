import discord
from discord import app_commands

def setup_socialalert_commands(bot, deps):
    globals().update(deps)
    # ===== SOCIAL ALERTS (commande slash) =====

    socialalert_group = app_commands.Group(
        name="socialalert",
        description="Alertes Twitch / YouTube / Reddit (admin/modo uniquement)",
        default_permissions=discord.Permissions(manage_guild=True),
    )


    @socialalert_group.command(name="add", description="Créer une alerte sociale (live, vidéo, post)")
    @app_commands.describe(
        plateforme="twitch / youtube / reddit",
        pseudo="Pseudo Twitch / @handle YouTube ou ID UCxxx / username Reddit (ou r/sub)",
        salon="Salon Discord où poster les alertes",
        message="Message custom (placeholders : {target}, {title}, {url}, {author}, {game}, {viewers})",
    )
    @app_commands.choices(plateforme=[
        app_commands.Choice(name="Twitch (live)",                value="twitch"),
        app_commands.Choice(name="YouTube (nouvelle vidéo)",     value="youtube"),
        app_commands.Choice(name="Reddit (nouveau post)",        value="reddit"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sa_add(
        interaction: discord.Interaction,
        plateforme: app_commands.Choice[str],
        pseudo: str,
        salon: discord.TextChannel,
        message: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        pseudo = pseudo.strip()
        plat = plateforme.value
        label = pseudo
        target_id = pseudo

        # Resolution YouTube @handle -> UC...
        if plat == "youtube" and not pseudo.startswith("UC"):
            resolved = await social.youtube_resolve_handle(pseudo)
            if not resolved:
                await interaction.followup.send(
                    f"❌ Impossible de résoudre `{pseudo}` vers un channel ID YouTube. "
                    f"Essaie avec l'ID UCxxx directement.", ephemeral=True,
                )
                return
            target_id = resolved
            label = pseudo  # garde le @handle pour affichage

        # Twitch sans creds = bloque
        if plat == "twitch" and not (os.getenv("TWITCH_CLIENT_ID") and os.getenv("TWITCH_CLIENT_SECRET")):
            await interaction.followup.send(
                "❌ Les alertes Twitch nécessitent `TWITCH_CLIENT_ID` et `TWITCH_CLIENT_SECRET` "
                "dans le `.env` du bot. Crée une app sur https://dev.twitch.tv/console/apps",
                ephemeral=True,
            )
            return

        aid = social_alert_create(
            guild_id=interaction.guild.id,
            platform=plat, target_id=target_id, target_label=label,
            channel_id=salon.id, message_template=message,
            created_by=interaction.user.id,
        )
        await interaction.followup.send(
            f"✅ Alerte **{plat}** créée (id `{aid}`) pour **{label}** dans {salon.mention}. "
            f"Premier check d'ici ~5 min.",
            ephemeral=True,
        )


    @socialalert_group.command(name="list", description="Lister les alertes sociales actives")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sa_list(interaction: discord.Interaction):
        rows = social_alerts_list(guild_id=interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                "_Aucune alerte sociale configurée sur ce serveur._", ephemeral=True,
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
            title="🔔 Alertes sociales",
            description="\n".join(parts),
            color=0xC8F050,
        )
        if len(rows) > 25:
            embed.set_footer(text=f"…et {len(rows)-25} autre(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @socialalert_group.command(name="remove", description="Supprimer une alerte sociale")
    @app_commands.describe(alerte_id="ID de l'alerte (visible avec /socialalert list)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sa_remove(interaction: discord.Interaction, alerte_id: int):
        n = social_alert_delete(alerte_id, guild_id=interaction.guild.id)
        if n:
            await interaction.response.send_message(
                f"🗑️ Alerte `#{alerte_id}` supprimée.", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ Alerte `#{alerte_id}` introuvable sur ce serveur.", ephemeral=True,
            )


    bot.tree.add_command(socialalert_group)
