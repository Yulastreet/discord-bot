import asyncio
import datetime as _dt
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


    @bot.tree.command(name="redeem", description="Utiliser un code promo (TookCoins, XP Pass, ou Pass gratuit)")
    @app_commands.describe(code="Le code promo a echanger (insensible a la casse)")
    async def redeem_code(interaction: discord.Interaction, code: str):
        code = (code or "").strip().upper()
        if not code or len(code) > 32:
            await interaction.response.send_message(
                "❌ Code invalide.", ephemeral=True)
            return
        ok, reason, promo = promo_redeem_check(code, interaction.user.id)
        if not ok:
            messages = {
                "code_invalid":     "❌ Ce code n'existe pas.",
                "max_uses_reached": "❌ Ce code a atteint son nombre max d'utilisations.",
                "expired":          "❌ Ce code a expiré.",
                "already_redeemed": "❌ Tu as déjà utilisé ce code.",
            }
            await interaction.response.send_message(messages.get(reason, "❌ Code invalide."),
                                                    ephemeral=True)
            return

        rtype  = promo["reward_type"]
        rvalue = int(promo["reward_value"])
        try:
            promo_redeem_apply(code, interaction.user.id)
        except Exception as e:
            print(f"[redeem] apply err: {e}")
            await interaction.response.send_message(
                "❌ Erreur lors de la validation. Réessaie plus tard.", ephemeral=True)
            return

        applied_label = "?"
        try:
            if rtype == "tookcoins":
                creer_duel_profil(interaction.user.id, interaction.user.name)
                ajouter_tookcoins(interaction.user.id, rvalue)
                applied_label = f"**+{rvalue} TookCoins** 🪙"
            elif rtype == "pass_xp":
                season = get_or_create_current_season()
                sid = season["season_id"]
                new_total = add_pass_xp(interaction.user.id, sid, rvalue)
                auto_claim_pass_tiers(interaction.user.id, sid, new_total)
                applied_label = f"**+{rvalue} XP Pass** 🎟️"
            elif rtype == "premium_grant_days":
                add_premium_grant(interaction.user.id, feature="pass",
                                  granted_by=f"promo:{code}",
                                  note=f"{rvalue} jours de Pass via code promo")
                applied_label = f"**Pass offert** ({rvalue} jour(s)) 🎁"
        except Exception as e:
            print(f"[redeem] reward apply err type={rtype}: {e}")
            await interaction.response.send_message(
                "⚠️ Code validé mais récompense non appliquée. Contacte le owner.",
                ephemeral=True)
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎉 Code utilisé !",
                description=f"Code `{code}` validé.\n\n{applied_label}",
                color=0xB9F23A,
            ),
            ephemeral=True,
        )


    @bot.tree.command(name="daily", description="Reclame ta recompense quotidienne (TookCoins + XP Pass si actif)")
    async def daily_claim(interaction: discord.Interaction):
        user = interaction.user
        today = _dt.datetime.utcnow().date()
        today_str = today.isoformat()

        state = daily_claim_get(user.id)
        last_str = state.get("last_claim_date")
        prev_streak = int(state.get("streak") or 0)

        if last_str == today_str:
            # Deja claim aujourd'hui
            tomorrow = _dt.datetime.combine(today + _dt.timedelta(days=1),
                                            _dt.time(0, 0, tzinfo=_dt.timezone.utc))
            now = _dt.datetime.now(_dt.timezone.utc)
            delta = tomorrow - now
            hours, rem = divmod(int(delta.total_seconds()), 3600)
            minutes = rem // 60
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⏳ Déjà réclamé",
                    description=(
                        f"Tu as déjà récupéré ta récompense aujourd'hui.\n\n"
                        f"Prochaine réclamation dans **{hours}h {minutes}m** (UTC minuit).\n"
                        f"Streak actuelle : **{prev_streak} jour(s)** 🔥"
                    ),
                    color=0xE67E22,
                ),
                ephemeral=True,
            )
            return

        # Calcul streak : si yesterday -> +1, sinon reset a 1
        new_streak = 1
        if last_str:
            try:
                last_date = _dt.date.fromisoformat(last_str)
                if (today - last_date).days == 1:
                    new_streak = prev_streak + 1
            except ValueError:
                pass

        # Recompenses : 10 base + 2/jour streak cap 7 -> max 24 TC
        # (vs 100 TC pour gagner un duel : daily reste un modeste appoint)
        streak_bonus = min(7, new_streak) * 2
        coins = 10 + streak_bonus
        # Essences (monnaie cartes) : 40 base + 8/jour streak cap 7 -> max 96 ✨
        essences_gain = 40 + min(7, new_streak) * 8

        # XP Pass si user a un Pass actif (10 XP/jour -> ~25 jours pour 1 tier)
        has_pass = bool(user_has_active_pass(user.id, sku_pass_id=SKU_PASS)) or (
            DISCORD_OWNER_ID and str(user.id) == str(DISCORD_OWNER_ID)
        )
        pass_xp_gain = 10 if has_pass else 0

        # Apply
        try:
            creer_duel_profil(user.id, user.name)
        except Exception:
            pass
        try:
            ajouter_tookcoins(user.id, coins)
        except Exception as e:
            print(f"[daily] ajouter_tookcoins err: {e}")
        try:
            from database import currency_add
            currency_add(user.id, essences_gain)
        except Exception as e:
            print(f"[daily] currency_add err: {e}")
        if pass_xp_gain:
            try:
                season = get_or_create_current_season()
                sid = season["season_id"]
                new_total = add_pass_xp(user.id, sid, pass_xp_gain)
                auto_claim_pass_tiers(user.id, sid, new_total)
            except Exception as e:
                print(f"[daily] add_pass_xp err: {e}")

        daily_claim_apply(user.id, today_str, new_streak)

        lines = [f"**+{coins} TookCoins** 🪙", f"**+{essences_gain} Essences** ✨"]
        if pass_xp_gain:
            lines.append(f"**+{pass_xp_gain} XP Pass** 🎟️")
        lines.append("")
        lines.append(f"🔥 Streak : **{new_streak} jour(s)** consécutif(s)")
        if new_streak < 7:
            lines.append(f"_Bonus streak max atteint à 7 jours (+14 TC)._")
        if not has_pass:
            lines.append(f"_Active un Pass pour gagner aussi de l'XP Pass quotidien._")

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"🎁 Récompense quotidienne — {user.display_name}",
                description="\n".join(lines),
                color=0xB9F23A,
            ),
        )
