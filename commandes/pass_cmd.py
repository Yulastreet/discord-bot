import datetime as _dt
import discord
from discord import app_commands

from services.i18n import locale_of, t, ti
from services.ui_v2 import Panel

def setup_pass_commands(bot, deps):
    globals().update(deps)
    # ===== BATTLE PASS =====

    _QUEST_TYPE_KEYS = {
        "send_messages": "server.pass.quest_send_messages",
        "play_duels":    "server.pass.quest_play_duels",
        "earn_xp":       "server.pass.quest_earn_xp",
        "use_commands":  "server.pass.quest_use_commands",
    }


    def _quest_progress_bar(progress: int, target: int, width: int = 12) -> str:
        pct = min(1.0, progress / target if target else 0)
        filled = int(pct * width)
        return "▰" * filled + "▱" * (width - filled)


    @bot.tree.command(name="pass", description="See your Battle Pass progress")
    async def pass_status(interaction: discord.Interaction):
        user = interaction.user
        loc = locale_of(interaction)
        has_pass = user_has_active_pass(user.id, sku_pass_id=SKU_PASS) or (DISCORD_OWNER_ID and str(user.id) == str(DISCORD_OWNER_ID))

        if not has_pass:
            p = Panel(
                t("server.pass.no_pass_title", loc),
                t("server.pass.no_pass_body", loc),
            )
            await interaction.response.send_message(view=p.view(), ephemeral=True)
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
        p = Panel(
            t("server.pass.season_title", loc,
              season=season.get("name") or t("server.pass.default_season_name", loc)),
            t("server.pass.season_body", loc,
              tier=tier, tiers=PASS_TIERS, bar=bar,
              xp_in_tier=xp_in_tier, xp_needed=xp_needed, xp_total=xp_total),
        )

        daily = [q for q in quests if q["period"] == "daily"]
        weekly = [q for q in quests if q["period"] == "weekly"]

        if daily:
            lines = []
            for q in daily:
                lbl = t(_QUEST_TYPE_KEYS[q["type"]], loc) if q["type"] in _QUEST_TYPE_KEYS else q["type"]
                done = "✅" if q["progress"] >= q["target"] else "🔸"
                bar_q = _quest_progress_bar(q["progress"], q["target"])
                lines.append(f"{done} {lbl} : `{bar_q}` {q['progress']}/{q['target']} (+{q['xp_reward']} XP)")
            p.field(t("server.pass.daily_quests", loc), "\n".join(lines), inline=False)

        if weekly:
            lines = []
            for q in weekly:
                lbl = t(_QUEST_TYPE_KEYS[q["type"]], loc) if q["type"] in _QUEST_TYPE_KEYS else q["type"]
                done = "✅" if q["progress"] >= q["target"] else "🔸"
                bar_q = _quest_progress_bar(q["progress"], q["target"])
                lines.append(f"{done} {lbl} : `{bar_q}` {q['progress']}/{q['target']} (+{q['xp_reward']} XP)")
            p.field(t("server.pass.weekly_quests", loc), "\n".join(lines), inline=False)

        p.footer(t("server.pass.season_footer", loc, date=season.get("ends_at", "?")[:10]))
        await interaction.response.send_message(view=p.view(), ephemeral=True)


    @bot.tree.command(name="redeem", description="Redeem a promo code (TookCoins, Pass XP, or a free Pass)")
    @app_commands.describe(code="The promo code to redeem (case insensitive)")
    async def redeem_code(interaction: discord.Interaction, code: str):
        loc = locale_of(interaction)
        code = (code or "").strip().upper()
        if not code or len(code) > 32:
            await interaction.response.send_message(
                ti(interaction, "server.redeem.invalid_code"), ephemeral=True)
            return
        ok, reason, promo = promo_redeem_check(code, interaction.user.id)
        if not ok:
            keys = {
                "code_invalid":     "server.redeem.code_unknown",
                "max_uses_reached": "server.redeem.max_uses",
                "expired":          "server.redeem.expired",
                "already_redeemed": "server.redeem.already_used",
            }
            await interaction.response.send_message(
                t(keys.get(reason, "server.redeem.invalid_code"), loc), ephemeral=True)
            return

        rtype  = promo["reward_type"]
        rvalue = int(promo["reward_value"])
        try:
            promo_redeem_apply(code, interaction.user.id)
        except Exception as e:
            print(f"[redeem] apply err: {e}")
            await interaction.response.send_message(
                ti(interaction, "server.redeem.validation_error"), ephemeral=True)
            return

        applied_label = "?"
        try:
            if rtype == "tookcoins":
                creer_duel_profil(interaction.user.id, interaction.user.name)
                ajouter_tookcoins(interaction.user.id, rvalue)
                applied_label = t("server.redeem.reward_tookcoins", loc, amount=rvalue)
            elif rtype == "pass_xp":
                season = get_or_create_current_season()
                sid = season["season_id"]
                new_total = add_pass_xp(interaction.user.id, sid, rvalue)
                auto_claim_pass_tiers(interaction.user.id, sid, new_total)
                applied_label = t("server.redeem.reward_pass_xp", loc, amount=rvalue)
            elif rtype == "premium_grant_days":
                add_premium_grant(interaction.user.id, feature="pass",
                                  granted_by=f"promo:{code}",
                                  note=f"{rvalue} days of Pass via promo code")
                applied_label = t("server.redeem.reward_pass_days", loc, days=rvalue)
            elif rtype == "roll":
                from database import roll_give_user as _rg
                _rg(interaction.user.id, rvalue)
                applied_label = t("server.redeem.reward_roll", loc, amount=rvalue)
            elif rtype == "epic_roll":
                from database import user_item_add as _uia
                _uia(interaction.user.id, "epic_roll", rvalue)
                applied_label = t("server.redeem.reward_epic_roll", loc, amount=rvalue)
            elif rtype == "golden_roll":
                from database import user_item_add as _uia
                _uia(interaction.user.id, "golden_roll", rvalue)
                applied_label = t("server.redeem.reward_golden_roll", loc, amount=rvalue)
        except Exception as e:
            print(f"[redeem] reward apply err type={rtype}: {e}")
            await interaction.response.send_message(
                ti(interaction, "server.redeem.reward_failed"), ephemeral=True)
            return

        await interaction.response.send_message(
            view=Panel(
                t("server.redeem.success_title", loc),
                t("server.redeem.success_body", loc, code=code, reward=applied_label),
            ).view(),
            ephemeral=True,
        )


    @bot.tree.command(name="daily", description="Claim your daily reward (TookCoins + Pass XP if active)")
    async def daily_claim(interaction: discord.Interaction):
        user = interaction.user
        loc = locale_of(interaction)
        today = _dt.datetime.utcnow().date()
        today_str = today.isoformat()

        state = daily_claim_get(user.id)
        last_str = state.get("last_claim_date")
        prev_streak = int(state.get("streak") or 0)

        if last_str == today_str:
            # Already claimed today
            tomorrow = _dt.datetime.combine(today + _dt.timedelta(days=1),
                                            _dt.time(0, 0, tzinfo=_dt.timezone.utc))
            now = _dt.datetime.now(_dt.timezone.utc)
            delta = tomorrow - now
            hours, rem = divmod(int(delta.total_seconds()), 3600)
            minutes = rem // 60
            await interaction.response.send_message(
                view=Panel(
                    t("server.daily.already_title", loc, user=user.display_name),
                    t("server.daily.already_body", loc,
                      hours=hours, minutes=minutes, streak=prev_streak),
                ).view(),
            )
            return

        # Streak: yesterday -> +1, otherwise reset to 1
        new_streak = 1
        if last_str:
            try:
                last_date = _dt.date.fromisoformat(last_str)
                if (today - last_date).days == 1:
                    new_streak = prev_streak + 1
            except ValueError:
                pass

        # Rewards: 10 base + 2/streak day capped at 7 -> max 24 TC
        # (vs 100 TC for winning a duel: daily stays a modest top-up)
        streak_bonus = min(7, new_streak) * 2
        coins = 10 + streak_bonus
        # Essences (card currency): 40 base + 8 per streak day (cap 7), so 96 max
        essences_gain = 40 + min(7, new_streak) * 8

        # Pass XP when the user has an active Pass (10 XP/day -> ~25 days for 1 tier)
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

        # Event currency when a global event is running
        event_coins_gain = 0
        event_coin_name = t("server.daily.event_tokens", loc)
        event_emoji = "🎟️"
        try:
            from database import (global_event_for_guild, event_coins_add, EVENT_DAILY_COINS)
            _ev = global_event_for_guild(interaction.guild.id if interaction.guild else None)
            if _ev.get("active"):
                event_coins_gain = EVENT_DAILY_COINS
                event_coins_add(user.id, _ev["key"], event_coins_gain)
                event_emoji = _ev.get("coin_emoji") or "🎟️"
                event_coin_name = _ev.get("coin") or t("server.daily.event_tokens", loc)
        except Exception as e:
            print(f"[daily] event coins err: {e}")

        daily_claim_apply(user.id, today_str, new_streak)

        lines = [t("server.daily.reward_tookcoins", loc, amount=coins),
                 t("server.daily.reward_essences", loc, amount=essences_gain)]
        if event_coins_gain:
            lines.append(t("server.daily.reward_event", loc, amount=event_coins_gain,
                           currency=event_coin_name, emoji=event_emoji))
        if pass_xp_gain:
            lines.append(t("server.daily.reward_pass_xp", loc, amount=pass_xp_gain))
        lines.append("")
        lines.append(t("server.daily.streak_line", loc, streak=new_streak))
        if new_streak < 7:
            lines.append(t("server.daily.streak_hint", loc))
        if not has_pass:
            lines.append(t("server.daily.pass_hint", loc))

        await interaction.response.send_message(
            view=Panel(
                t("server.daily.title", loc, user=user.display_name),
                "\n".join(lines),
            ).view(),
        )
