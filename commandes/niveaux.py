"""Slash commands /niveau et /leaderboard.

Refonte clean (juin 2026) : defer immediat pour eviter timeout interaction,
followup uniquement, filename horodate pour eviter cache attachment,
dedup interaction.id pour stopper tout double-dispatch.
"""

import time as _time
import collections as _col
import discord
from discord import app_commands

# Dedup interaction.id pour stopper net tout double-dispatch
# (re-delivery Discord, double tree register, etc). 1024 entries LRU.
_INTER_SEEN = _col.OrderedDict()
_INTER_SEEN_MAX = 1024


def _is_dup_interaction(interaction):
    iid = getattr(interaction, "id", None)
    if iid is None:
        return False
    if iid in _INTER_SEEN:
        return True
    _INTER_SEEN[iid] = True
    if len(_INTER_SEEN) > _INTER_SEEN_MAX:
        _INTER_SEEN.popitem(last=False)
    return False


def setup_niveau_commands(bot, deps):
    globals().update(deps)

    @bot.tree.command(name="niveau", description="Voir ton niveau et XP sur ce serveur")
    @app_commands.describe(membre="Membre dont voir le niveau (defaut : toi)")
    async def niveau(interaction: discord.Interaction, membre: discord.Member = None):
        if _is_dup_interaction(interaction):
            print(f"[/niveau] dedup interaction.id={interaction.id} — skip", flush=True)
            return

        # ACK IMMEDIAT (interaction expire en 3s sinon)
        try:
            await interaction.response.defer()
        except Exception:
            pass

        membre = membre or interaction.user
        gid = str(interaction.guild.id)
        xp  = get_xp(gid, membre.id)
        level, in_lvl, needed, percent = get_progress(xp, gid)
        print(f"[/niveau] iid={interaction.id} guild={gid} user={membre.id} xp={xp} level={level}", flush=True)

        # Etape 1 : RENDER (peut fail). On obtient un buf ou None.
        buf = None
        prem = is_premium_user(membre.id)
        print(f"[/niveau] prem={prem}", flush=True)
        if prem:
            try:
                settings = get_premium_settings(membre.id) or {}
                cosmetic = get_user_cosmetic(membre.id) or {}
                print(f"[/niveau] settings={settings} cosmetic={cosmetic}", flush=True)
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
            except Exception as e:
                print(f"[/niveau premium render] {type(e).__name__}: {e} — fallback embed", flush=True)
                buf = None

        # Etape 2 : SEND (un seul send total, jamais deux)
        if buf is not None:
            file = discord.File(buf, filename=f"niveau-{int(_time.time()*1000)}.png")
            try:
                await interaction.followup.send(file=file)
            except Exception as e:
                print(f"[/niveau premium send] {type(e).__name__}: {e}", flush=True)
            return  # PAS de fallback derriere : on a deja tente d'envoyer

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
            print(f"[/niveau fallback send] {type(e).__name__}: {e}", flush=True)

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
