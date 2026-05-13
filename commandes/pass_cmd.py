import asyncio
import discord
from discord import app_commands

def setup_pass_commands(bot, deps):
    globals().update(deps)
    # ===== BATTLE PASS =====

    _QUEST_TYPE_LABELS = {
        "send_messages": "💬 Messages envoyés",
        "play_duels":    "⚔️ Duels joués",
        "earn_xp":       "✨ XP gagné",
        "use_commands":  "🔧 Commandes utilisées",
    }


    def _quest_progress_bar(progress: int, target: int, width: int = 12) -> str:
        pct = min(1.0, progress / target if target else 0)
        filled = int(pct * width)
        return "▰" * filled + "▱" * (width - filled)


    @bot.tree.command(name="pass", description="Voir ta progression dans le Battle Pass")
    async def pass_status(interaction: discord.Interaction):
        user = interaction.user
        has_pass = user_has_active_pass(user.id, sku_pass_id=SKU_PASS) or (DISCORD_OWNER_ID and str(user.id) == str(DISCORD_OWNER_ID))

        if not has_pass:
            embed = discord.Embed(
                title="🎟️ TookBot Battle Pass",
                description=(
                    "Tu n'as pas encore de Pass actif sur cette saison.\n\n"
                    "Le Pass se prend dans la **boutique** du bot (clique sur le profil "
                    "de TookBot → Boutique). 30 paliers de récompenses chaque mois : "
                    "backgrounds exclusifs, sabres cosmétiques, titres, boosts XP, et plus."
                ),
                color=0x9CB94A,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        season = get_or_create_current_season()
        progress = get_pass_progress(user.id, season["season_id"])
        quests = list_user_active_quests(user.id)
        xp_total = progress.get("xp", 0)
        tier = pass_tier_from_xp(xp_total)
        next_tier_xp = (tier + 1) * PASS_XP_PER_TIER if tier < PASS_TIERS else PASS_XP_TOTAL
        xp_in_tier = xp_total - tier * PASS_XP_PER_TIER if tier < PASS_TIERS else PASS_XP_PER_TIER
        xp_needed = PASS_XP_PER_TIER if tier < PASS_TIERS else 0

        bar = _quest_progress_bar(min(xp_in_tier, xp_needed), max(1, xp_needed), width=20)
        embed = discord.Embed(
            title=f"🎟️ {season.get('name') or 'Battle Pass'}",
            description=(
                f"**Palier {tier} / {PASS_TIERS}**\n"
                f"`{bar}`  {xp_in_tier}/{xp_needed} XP du palier suivant\n"
                f"Total saison : **{xp_total} XP**"
            ),
            color=0x9CB94A,
        )

        daily = [q for q in quests if q["period"] == "daily"]
        weekly = [q for q in quests if q["period"] == "weekly"]

        if daily:
            lines = []
            for q in daily:
                lbl = _QUEST_TYPE_LABELS.get(q["type"], q["type"])
                done = "✅" if q["progress"] >= q["target"] else "🔸"
                bar_q = _quest_progress_bar(q["progress"], q["target"])
                lines.append(f"{done} {lbl} : `{bar_q}` {q['progress']}/{q['target']} (+{q['xp_reward']} XP)")
            embed.add_field(name="📅 Quêtes du jour", value="\n".join(lines), inline=False)

        if weekly:
            lines = []
            for q in weekly:
                lbl = _QUEST_TYPE_LABELS.get(q["type"], q["type"])
                done = "✅" if q["progress"] >= q["target"] else "🔸"
                bar_q = _quest_progress_bar(q["progress"], q["target"])
                lines.append(f"{done} {lbl} : `{bar_q}` {q['progress']}/{q['target']} (+{q['xp_reward']} XP)")
            embed.add_field(name="🗓️ Quêtes de la semaine", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"Saison se termine le {season.get('ends_at', '?')[:10]}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
