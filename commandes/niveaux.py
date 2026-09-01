"""Slash commands /level and /leaderboard.

Clean rework (June 2026): immediate defer to avoid an interaction timeout,
followup only, timestamped filename to dodge the attachment cache,
interaction.id dedup to stop any double dispatch.
"""

import time as _time
import collections as _col
import discord
from discord import app_commands

from services.i18n import ti
from services.ui_v2 import Panel

# interaction.id dedup to hard-stop any double dispatch
# (Discord re-delivery, double tree register, etc). 1024 entries LRU.
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

    @bot.tree.command(name="level", description="See your level and XP on this server")
    @app_commands.describe(member="Member whose level to show (default: you)")
    async def level(interaction: discord.Interaction, member: discord.Member = None):
        if _is_dup_interaction(interaction):
            print(f"[/level] dedup interaction.id={interaction.id} - skip", flush=True)
            return

        # IMMEDIATE ACK (the interaction expires after 3s otherwise)
        try:
            await interaction.response.defer()
        except Exception:
            pass

        member = member or interaction.user
        gid = str(interaction.guild.id)
        xp  = get_xp(gid, member.id)
        lvl, in_lvl, needed, percent = get_progress(xp, gid)
        print(f"[/level] iid={interaction.id} guild={gid} user={member.id} xp={xp} level={lvl}", flush=True)

        # Step 1: RENDER (can fail). We end up with a buf or None.
        buf = None
        prem = is_premium_user(member.id)
        print(f"[/level] prem={prem}", flush=True)
        if prem:
            try:
                settings = get_premium_settings(member.id) or {}
                cosmetic = get_user_cosmetic(member.id) or {}
                print(f"[/level] settings={settings} cosmetic={cosmetic}", flush=True)
                buf = await render_niveau_card(
                    username=member.display_name,
                    avatar_url=member.display_avatar.url,
                    level=lvl,
                    xp_total=xp,
                    xp_in_level=in_lvl,
                    xp_needed=needed,
                    background=settings.get("niveau_background") or "default",
                    title=cosmetic.get("title"),
                    emoji_prefix=cosmetic.get("emoji"),
                )
            except Exception as e:
                print(f"[/level premium render] {type(e).__name__}: {e} - fallback embed", flush=True)
                buf = None

        # Step 2: SEND (a single send overall, never two)
        if buf is not None:
            file = discord.File(buf, filename=f"level-{int(_time.time()*1000)}.png")
            try:
                await interaction.followup.send(file=file)
            except Exception as e:
                print(f"[/level premium send] {type(e).__name__}: {e}", flush=True)
            return  # NO fallback behind this: we already tried to send

        # Fallback panel (non-premium, or render failure)
        filled = percent // 5
        bar = "█" * filled + "░" * (20 - filled)
        p = Panel(ti(interaction, "utils.level.title", name=member.display_name))
        p.thumbnail(member.display_avatar.url)
        p.field(ti(interaction, "utils.level.field_level"),
                f"**{lvl}**", inline=True)
        p.field(ti(interaction, "utils.level.field_xp"),
                f"**{xp}**", inline=True)
        p.field(
            ti(interaction, "utils.level.field_progress"),
            ti(interaction, "utils.level.progress_value",
               bar=bar, percent=percent, current=in_lvl, needed=needed),
            inline=False,
        )
        try:
            await interaction.followup.send(view=p.view())
        except Exception as e:
            print(f"[/level fallback send] {type(e).__name__}: {e}", flush=True)

    @bot.tree.command(name="leaderboard", description="Server XP top")
    async def leaderboard(interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        gid = str(interaction.guild.id)
        rows = get_leaderboard(gid, limit=10) or []
        if not rows:
            await interaction.followup.send(
                ti(interaction, "utils.level.leaderboard.empty"))
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            prefix = medals[i] if i < 3 else f"`{i+1:>2}.`"
            uname = r.get("username") or "?"
            lines.append(ti(interaction, "utils.level.leaderboard.line",
                            prefix=prefix, name=uname, level=r["level"], xp=r["xp"]))
        p = Panel(
            ti(interaction, "utils.level.leaderboard.title", guild=interaction.guild.name),
            "\n".join(lines),
        )
        try:
            await interaction.followup.send(view=p.view())
        except Exception as e:
            print(f"[/leaderboard followup] {type(e).__name__}: {e}")
