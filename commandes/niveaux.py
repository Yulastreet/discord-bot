import asyncio
import discord
from discord import app_commands

def setup_niveau_commands(bot, deps):
    globals().update(deps)
    # ===== NIVEAUX / XP =====

    @bot.tree.command(name="niveau", description="Voir ton niveau et XP (sur ce serveur)")
    @app_commands.describe(membre="Le membre dont tu veux voir le niveau")
    async def niveau(interaction: discord.Interaction, membre: discord.Member = None):
        # ACK immediatement : interaction expire en ~3s sinon (NotFound 10062).
        try:
            await interaction.response.defer()
        except Exception:
            pass  # deja ack par autre chose, on continue avec followup

        membre = membre or interaction.user
        gid = str(interaction.guild.id)
        xp = get_xp(gid, membre.id)
        level, progress_xp, needed_xp, percent = get_progress(xp)
        print(f"[/niveau] guild={gid} user={membre.id} xp={xp} level={level}", flush=True)

        # Premium ? Carte image. Sinon embed simple.
        if is_premium_user(membre.id):
            try:
                settings = get_premium_settings(membre.id)
                cosmetic = get_user_cosmetic(membre.id)
                buf = await render_niveau_card(
                    username=membre.display_name,
                    avatar_url=membre.display_avatar.url,
                    level=level,
                    xp_total=xp,
                    xp_in_level=progress_xp,
                    xp_needed=needed_xp,
                    background=settings.get("niveau_background") or "default",
                    title=cosmetic.get("title"),
                    emoji_prefix=cosmetic.get("emoji"),
                )
                file = discord.File(buf, filename="niveau.png")
                await interaction.followup.send(file=file)
                return
            except Exception as e:
                print(f"[niveau premium] render error: {e!r} — fallback embed")
                # Fallback embed ci-dessous

        filled = int(percent / 5)
        bar = "█" * filled + "░" * (20 - filled)
        embed = discord.Embed(title=f"📊 {membre.display_name}", color=discord.Color.blurple())
        embed.add_field(name="🏆 Niveau", value=f"**{level}**", inline=True)
        embed.add_field(name="⭐ XP Total", value=f"**{xp}**", inline=True)
        embed.add_field(
            name="📈 Progression",
            value=f"`{bar}` **{percent}%**\n`{progress_xp} / {needed_xp} XP`",
            inline=False,
        )
        embed.set_thumbnail(url=membre.display_avatar.url)
        try:
            await interaction.followup.send(embed=embed)
        except Exception as e:
            print(f"[/niveau] followup fail: {e!r}")

    @bot.tree.command(name="leaderboard", description="Classement XP de ce serveur")
    async def leaderboard(interaction: discord.Interaction):
        await interaction.response.defer()
        gid = str(interaction.guild.id)
        sorted_users = get_leaderboard(gid, limit=10)
        if not sorted_users:
            await interaction.followup.send("Personne n'a encore d'XP sur ce serveur.")
            return
        embed = discord.Embed(title=f"🏆 Classement XP — {interaction.guild.name}", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"]
        description = ""
        for i, row in enumerate(sorted_users):
            try:
                user = await bot.fetch_user(int(row["user_id"]))
                name = user.name
            except Exception:
                name = row.get("username") or "Utilisateur inconnu"
            medal = medals[i] if i < 3 else f"**#{i+1}**"
            description += f"{medal} {name} — **{row['xp']} XP** (Niveau {row['level']})\n"
        embed.description = description
        await interaction.followup.send(embed=embed)

    xp_group = app_commands.Group(name="xp", description="Admin : activer / desactiver le systeme d'XP du serveur")

    @xp_group.command(name="on", description="Reactive le gain d'XP sur ce serveur")
    @app_commands.default_permissions(manage_guild=True)
    async def xp_on(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Pas dispo en DM.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ Permission requise : **Gérer le serveur**.", ephemeral=True)
            return
        guild_setting_set(interaction.guild.id, "xp_enabled", "1")
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⭐ XP réactivé",
                description="Les membres gagneront à nouveau de l'XP en envoyant des messages.",
                color=0x2ECC71),
            ephemeral=True,
        )

    @xp_group.command(name="off", description="Desactive le gain d'XP sur ce serveur")
    @app_commands.default_permissions(manage_guild=True)
    async def xp_off(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Pas dispo en DM.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ Permission requise : **Gérer le serveur**.", ephemeral=True)
            return
        guild_setting_set(interaction.guild.id, "xp_enabled", "0")
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏸️ XP désactivé",
                description="Plus de gain d'XP sur ce serveur. Les niveaux actuels sont conservés.\n"
                            "Réactive avec `/xp on`.",
                color=0xE67E22),
            ephemeral=True,
        )

    @xp_group.command(name="status", description="Voir si l'XP est actif sur ce serveur")
    async def xp_status(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Pas dispo en DM.", ephemeral=True)
            return
        enabled = guild_setting_get(interaction.guild.id, "xp_enabled", "1") == "1"
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⭐ État XP",
                description=f"L'XP est **{'activé' if enabled else 'désactivé'}** sur **{interaction.guild.name}**.",
                color=0x2ECC71 if enabled else 0xE67E22),
            ephemeral=True,
        )

    bot.tree.add_command(xp_group)
