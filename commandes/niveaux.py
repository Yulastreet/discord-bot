import asyncio
import discord
from discord import app_commands

def setup_niveau_commands(bot, deps):
    globals().update(deps)
    # ===== NIVEAUX / XP =====

    @bot.tree.command(name="niveau", description="Voir ton niveau et XP (sur ce serveur)")
    @app_commands.describe(membre="Le membre dont tu veux voir le niveau")
    async def niveau(interaction: discord.Interaction, membre: discord.Member = None):
        membre = membre or interaction.user
        gid = str(interaction.guild.id)
        xp = get_xp(gid, membre.id)
        level, progress_xp, needed_xp, percent = get_progress(xp)

        # On regarde les entitlements du *membre affiche*, pas de l'auteur,
        # afin que tout le monde puisse voir la jolie carte du premium.
        if is_premium_user(membre.id):
            try:
                await interaction.response.defer()
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
            inline=False
        )
        embed.set_thumbnail(url=membre.display_avatar.url)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

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
