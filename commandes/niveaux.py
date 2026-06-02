"""Slash commands /niveau et /leaderboard.

Refonte clean (juin 2026) : defer immediat pour eviter timeout interaction,
followup uniquement, filename horodate pour eviter cache attachment.
"""

import time as _time
import discord
from discord import app_commands


def setup_niveau_commands(bot, deps):
    globals().update(deps)

    @bot.tree.command(name="niveau", description="Voir ton niveau et XP sur ce serveur")
    @app_commands.describe(membre="Membre dont voir le niveau (defaut : toi)")
    async def niveau(interaction: discord.Interaction, membre: discord.Member = None):
        # ACK IMMEDIAT (interaction expire en 3s sinon)
        try:
            await interaction.response.defer()
        except Exception:
            pass

        membre = membre or interaction.user
        gid = str(interaction.guild.id)
        xp  = get_xp(gid, membre.id)
        level, in_lvl, needed, percent = get_progress(xp)

        # Premium : carte image Pillow
        if is_premium_user(membre.id):
            try:
                settings = get_premium_settings(membre.id) or {}
                cosmetic = get_user_cosmetic(membre.id) or {}
                buf = await render_niveau_card(
                    username=membre.display_name,
                    avatar_url=membre.display_avatar.url,
                    level=level,
                    xp_total=xp,
                    xp_in_level=in_lvl,
                    xp_needed=needed,
                    background=settings.get("niveau_background") or "default",
                    title=cosmetic.get("title"),
                    emoji_prefix=cosmetic.get("emoji"),
                )
                file = discord.File(buf, filename=f"niveau-{int(_time.time()*1000)}.png")
                await interaction.followup.send(file=file)
                return
            except Exception as e:
                print(f"[/niveau premium render] {type(e).__name__}: {e} — fallback embed")
                # Fallback embed plus bas

        # Fallback embed (non-premium ou render fail)
        filled = percent // 5
        bar = "█" * filled + "░" * (20 - filled)
        embed = discord.Embed(
            title=f"📊 {membre.display_name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="🏆 Niveau", value=f"**{level}**", inline=True)
        embed.add_field(name="⭐ XP total", value=f"**{xp}**", inline=True)
        embed.add_field(
            name="📈 Progression",
            value=f"`{bar}` **{percent}%**\n`{in_lvl} / {needed} XP`",
            inline=False,
        )
        embed.set_thumbnail(url=membre.display_avatar.url)
        try:
            await interaction.followup.send(embed=embed)
        except Exception as e:
            print(f"[/niveau followup] {type(e).__name__}: {e}")

    @bot.tree.command(name="leaderboard", description="Top XP du serveur")
    async def leaderboard(interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        gid = str(interaction.guild.id)
        rows = get_leaderboard(gid, limit=10) or []
        if not rows:
            await interaction.followup.send("Personne n'a encore gagne de XP sur ce serveur.")
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            prefix = medals[i] if i < 3 else f"`{i+1:>2}.`"
            uname = r.get("username") or "?"
            lines.append(f"{prefix}  **{uname}**  · niveau **{r['level']}** · {r['xp']} XP")
        embed = discord.Embed(
            title=f"🏆 Top XP — {interaction.guild.name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        try:
            await interaction.followup.send(embed=embed)
        except Exception as e:
            print(f"[/leaderboard followup] {type(e).__name__}: {e}")
