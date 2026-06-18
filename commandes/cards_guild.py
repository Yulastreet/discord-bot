"""Commandes /guild : guildes de joueurs (clubs cross-serveur).
Config pilotee par l'owner (get_guild_config). Hooks XP appeles depuis /roll, boss, etc.
"""
from __future__ import annotations

import discord
from discord import app_commands

from database import (
    get_guild_config, guild_create, guild_get, guild_get_by_name, guild_of_user,
    guild_member_role, guild_members, guild_member_count, guild_add_member,
    guild_remove_member, guild_set_role, guild_set_owner, guild_delete,
    guild_left_at, guild_invite_add, guild_invite_has, guild_add_xp, guild_top,
    guild_bank_add, guild_member_action_xp, guild_rewards_for_level,
    guild_level_for_xp, currency_get, currency_add,
    compute_player_combat_stats, combat_power, user_card_count,
    guild_bank_spend, guild_member_ids, roll_give_user,
    guild_set_color, guild_set_emblem, profile_color_hex, PROFILE_COLORS,
    guild_set_name, guild_quests_daily_get, guild_quests_weekly_get,
)
import datetime as _dt
import os as _os


def _is_owner(user_id) -> bool:
    owner = (_os.getenv("DISCORD_OWNER_ID") or "").strip()
    return bool(owner) and str(user_id) == owner


def _guild_xp_bar(bot, into, span, segments=14):
    filled = min(segments, int(round(segments * into / span))) if span > 0 else segments
    full = str(discord.utils.get(bot.emojis, name="playerlifebarfull") or "🟩")
    empty = str(discord.utils.get(bot.emojis, name="lifebarempty") or "⬛")
    return full * filled + empty * (segments - filled)


def _fmt_n(n):
    return f"{int(n):,}".replace(",", " ")


def _xp_needed_cumul(level, cfg):
    base = float(cfg.get("level_base", 600)); g = float(cfg.get("level_growth", 1.1))
    return sum(base * (g ** (n - 2)) for n in range(2, level + 1)) if level >= 2 else 0


def _progress_line(guild, cfg):
    lvl = guild["level"]; xp = guild["xp"]; maxlv = int(cfg.get("max_level", 60))
    if lvl >= maxlv:
        return f"Niveau **{lvl}** (MAX) · {xp:,} XP".replace(",", " ")
    cur = _xp_needed_cumul(lvl, cfg)
    nxt = _xp_needed_cumul(lvl + 1, cfg)
    into = xp - cur; span = max(1, nxt - cur)
    pct = int(100 * into / span)
    return f"Niveau **{lvl}** · {int(into):,}/{int(span):,} XP ({pct}%) vers {lvl+1}".replace(",", " ")


def setup_guild_commands(bot, deps):
    globals().update(deps)

    grp = app_commands.Group(name="guild", description="Guildes de joueurs (clubs)")

    def _is_officer(role):
        return role in ("master", "officer")

    # ---- create ----
    @grp.command(name="create", description="Créer une guilde (coûte des essences)")
    @app_commands.describe(nom="Nom de la guilde", tag="Tag court (optionnel, ex: TKBT)")
    async def g_create(interaction: discord.Interaction, nom: str, tag: str = None):
        uid = interaction.user.id
        cfg = get_guild_config()
        if guild_of_user(uid):
            await interaction.response.send_message("Tu es déjà dans une guilde.", ephemeral=True); return
        nom = nom.strip()[:40]
        if len(nom) < 2:
            await interaction.response.send_message("Nom trop court.", ephemeral=True); return
        if guild_get_by_name(nom):
            await interaction.response.send_message("Ce nom de guilde est déjà pris.", ephemeral=True); return
        cost = int(cfg.get("create_cost", 10000))
        if currency_get(uid) < cost:
            await interaction.response.send_message(
                f"Il te faut **{cost:,}** ✨ pour créer une guilde.".replace(",", " "), ephemeral=True); return
        currency_add(uid, -cost)
        gid = guild_create(nom, uid, tag=(tag or "").strip()[:8] or None)
        await interaction.response.send_message(
            f"🎉 Guilde **{nom}** créée ! Tu en es le **Maître**. "
            f"Invite des membres avec `/guild invite`.", ephemeral=True)

    # ---- info ----
    @grp.command(name="info", description="Voir une guilde (la tienne par défaut)")
    @app_commands.describe(nom="Nom d'une guilde (sinon la tienne)")
    async def g_info(interaction: discord.Interaction, nom: str = None):
        cfg = get_guild_config()
        g = guild_get_by_name(nom) if nom else guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message(
                "Guilde introuvable." if nom else "Tu n'es dans aucune guilde. `/guild create` ou demande une invite.",
                ephemeral=True); return
        members = guild_members(g["id"])
        rew = guild_rewards_for_level(g["level"], cfg)
        perks = []
        if rew.get("essence_pct"): perks.append(f"+{rew['essence_pct']}% essences")
        if rew.get("xp_pct"): perks.append(f"+{rew['xp_pct']}% XP guilde")
        if rew.get("roll_cd_min"): perks.append(f"−{rew['roll_cd_min']} min cooldown roll")
        if rew.get("charges"): perks.append(f"+{rew['charges']} roll/h")
        if rew.get("wishlist"): perks.append(f"+{rew['wishlist']} wishlist")
        if rew.get("boss_pct"): perks.append(f"+{rew['boss_pct']}% loot boss")
        unlocks = [k for k in ("bank", "raids", "shop") if rew.get(k)]
        title = f"🛡️ {g['name']}" + (f" [{g['tag']}]" if g.get("tag") else "")
        emb = discord.Embed(title=title, color=0x8e44ad)
        emb.add_field(name="Progression", value=_progress_line(g, cfg), inline=False)
        emb.add_field(name=f"Membres ({len(members)}/{cfg.get('max_members',30)})",
                      value="\n".join(f"{'👑' if m['role']=='master' else ('🔧' if m['role']=='officer' else '▫️')} "
                                      f"<@{m['user_id']}> · {m['xp_contributed']:,} XP".replace(",", " ")
                                      for m in members[:30]) or "—", inline=False)
        emb.add_field(name="Banque", value=f"{g['bank']:,} ✨".replace(",", " "), inline=True)
        if perks:
            emb.add_field(name="Bonus actifs", value=" · ".join(perks), inline=False)
        if unlocks:
            emb.add_field(name="Débloqué", value=" · ".join(unlocks), inline=False)
        await interaction.response.send_message(embed=emb,
                                                allowed_mentions=discord.AllowedMentions.none())

    # ---- invite ----
    @grp.command(name="invite", description="Inviter un joueur dans ta guilde (Maître/Officier)")
    @app_commands.describe(membre="Joueur à inviter")
    async def g_invite(interaction: discord.Interaction, membre: discord.Member):
        cfg = get_guild_config()
        g = guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message("Tu n'es dans aucune guilde.", ephemeral=True); return
        if not _is_officer(guild_member_role(g["id"], interaction.user.id)):
            await interaction.response.send_message("Réservé au Maître / Officiers.", ephemeral=True); return
        if membre.bot:
            await interaction.response.send_message("Pas un bot.", ephemeral=True); return
        if guild_of_user(membre.id):
            await interaction.response.send_message("Ce joueur est déjà dans une guilde.", ephemeral=True); return
        if guild_member_count(g["id"]) >= int(cfg.get("max_members", 30)):
            await interaction.response.send_message("Guilde pleine.", ephemeral=True); return
        guild_invite_add(g["id"], membre.id)
        await interaction.response.send_message(
            f"✅ {membre.mention} invité. Il peut rejoindre avec `/guild accept nom:{g['name']}`.",
            allowed_mentions=discord.AllowedMentions(users=[membre]))

    # ---- accept ----
    @grp.command(name="accept", description="Rejoindre une guilde qui t'a invité")
    @app_commands.describe(nom="Nom de la guilde")
    async def g_accept(interaction: discord.Interaction, nom: str):
        uid = interaction.user.id
        cfg = get_guild_config()
        if guild_of_user(uid):
            await interaction.response.send_message("Tu es déjà dans une guilde.", ephemeral=True); return
        g = guild_get_by_name(nom)
        if not g:
            await interaction.response.send_message("Guilde introuvable.", ephemeral=True); return
        if not guild_invite_has(g["id"], uid):
            await interaction.response.send_message("Tu n'as pas d'invitation de cette guilde.", ephemeral=True); return
        # cooldown anti guild-hopping (owner bypass)
        la = guild_left_at(uid)
        cd_h = int(cfg.get("hop_cooldown_h", 24))
        if la and cd_h > 0 and not _is_owner(uid):
            try:
                left = _dt.datetime.fromisoformat(la.replace("Z", ""))
                delta_h = (_dt.datetime.utcnow() - left).total_seconds() / 3600
                if delta_h < cd_h:
                    await interaction.response.send_message(
                        f"Tu as quitté une guilde récemment. Attends encore **{int(cd_h - delta_h) + 1}h**.",
                        ephemeral=True); return
            except Exception:
                pass
        if guild_member_count(g["id"]) >= int(cfg.get("max_members", 30)):
            await interaction.response.send_message("Guilde pleine.", ephemeral=True); return
        guild_add_member(g["id"], uid, "member")
        await interaction.response.send_message(f"🛡️ Tu as rejoint **{g['name']}** !", ephemeral=True)

    # ---- leave ----
    @grp.command(name="leave", description="Quitter ta guilde")
    async def g_leave(interaction: discord.Interaction):
        uid = interaction.user.id
        g = guild_of_user(uid)
        if not g:
            await interaction.response.send_message("Tu n'es dans aucune guilde.", ephemeral=True); return
        if guild_member_role(g["id"], uid) == "master":
            if guild_member_count(g["id"]) > 1:
                await interaction.response.send_message(
                    "Tu es le Maître. Transfère d'abord (`/guild transfer`) ou dissous (`/guild disband`).",
                    ephemeral=True); return
            guild_delete(g["id"])
            await interaction.response.send_message("Guilde dissoute (tu étais seul).", ephemeral=True); return
        guild_remove_member(g["id"], uid)
        await interaction.response.send_message(f"Tu as quitté **{g['name']}**.", ephemeral=True)

    # ---- kick ----
    @grp.command(name="kick", description="Exclure un membre (Maître/Officier)")
    @app_commands.describe(membre="Membre à exclure")
    async def g_kick(interaction: discord.Interaction, membre: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message("Tu n'es dans aucune guilde.", ephemeral=True); return
        my = guild_member_role(g["id"], interaction.user.id)
        if not _is_officer(my):
            await interaction.response.send_message("Réservé au Maître / Officiers.", ephemeral=True); return
        tgt = guild_member_role(g["id"], membre.id)
        if not tgt:
            await interaction.response.send_message("Ce joueur n'est pas dans ta guilde.", ephemeral=True); return
        if tgt == "master" or (tgt == "officer" and my != "master"):
            await interaction.response.send_message("Tu ne peux pas exclure ce membre.", ephemeral=True); return
        guild_remove_member(g["id"], membre.id)
        await interaction.response.send_message(f"{membre.mention} exclu de **{g['name']}**.",
                                                allowed_mentions=discord.AllowedMentions.none())

    # ---- promote / demote / transfer ----
    @grp.command(name="promote", description="Promouvoir un membre officier (Maître)")
    @app_commands.describe(membre="Membre")
    async def g_promote(interaction: discord.Interaction, membre: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
        if guild_member_role(g["id"], membre.id) != "member":
            await interaction.response.send_message("Membre invalide.", ephemeral=True); return
        guild_set_role(g["id"], membre.id, "officer")
        await interaction.response.send_message(f"🔧 {membre.mention} est Officier.",
                                                allowed_mentions=discord.AllowedMentions.none())

    @grp.command(name="demote", description="Rétrograder un officier (Maître)")
    @app_commands.describe(membre="Officier")
    async def g_demote(interaction: discord.Interaction, membre: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
        if guild_member_role(g["id"], membre.id) != "officer":
            await interaction.response.send_message("Ce joueur n'est pas Officier.", ephemeral=True); return
        guild_set_role(g["id"], membre.id, "member")
        await interaction.response.send_message(f"▫️ {membre.mention} redevient membre.",
                                                allowed_mentions=discord.AllowedMentions.none())

    @grp.command(name="transfer", description="Transférer la maîtrise (Maître)")
    @app_commands.describe(membre="Nouveau Maître")
    async def g_transfer(interaction: discord.Interaction, membre: discord.Member):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
        if not guild_member_role(g["id"], membre.id):
            await interaction.response.send_message("Ce joueur n'est pas dans ta guilde.", ephemeral=True); return
        guild_set_role(g["id"], interaction.user.id, "officer")
        guild_set_owner(g["id"], membre.id)
        await interaction.response.send_message(f"👑 {membre.mention} est le nouveau Maître.",
                                                allowed_mentions=discord.AllowedMentions.none())

    # ---- disband ----
    @grp.command(name="disband", description="Dissoudre ta guilde (Maître)")
    async def g_disband(interaction: discord.Interaction):
        g = guild_of_user(interaction.user.id)
        if not g or guild_member_role(g["id"], interaction.user.id) != "master":
            await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
        guild_delete(g["id"])
        await interaction.response.send_message(f"💥 Guilde **{g['name']}** dissoute.", ephemeral=True)

    # ---- donate ----
    @grp.command(name="donate", description="Donner des essences à la banque de ta guilde")
    @app_commands.describe(montant="Essences à donner")
    async def g_donate(interaction: discord.Interaction, montant: int):
        uid = interaction.user.id
        g = guild_of_user(uid)
        if not g:
            await interaction.response.send_message("Tu n'es dans aucune guilde.", ephemeral=True); return
        cfg = get_guild_config()
        if not guild_rewards_for_level(g["level"], cfg).get("bank"):
            lv = _unlock_level(cfg, "bank") or "?"
            await interaction.response.send_message(
                f"🔒 La banque de guilde se débloque au **niveau {lv}**.", ephemeral=True); return
        if montant <= 0:
            await interaction.response.send_message("Montant invalide.", ephemeral=True); return
        if currency_get(uid) < montant:
            await interaction.response.send_message("Pas assez d'essences.", ephemeral=True); return
        currency_add(uid, -montant)
        guild_bank_add(g["id"], montant)
        per100 = int(cfg.get("xp", {}).get("essence_per_100", 0))
        xp = (montant // 100) * per100
        if xp > 0:
            guild_member_action_xp(uid, xp)
        try:
            from database import guild_quest_progress
            guild_quest_progress(uid, "donate", montant)
        except Exception:
            pass
        await interaction.response.send_message(
            f"💰 +{montant:,} ✨ à la banque de **{g['name']}**".replace(",", " ")
            + (f" (+{xp} XP guilde)" if xp else "") + ".", ephemeral=True)

    # ---- top ----
    @grp.command(name="top", description="Classement des guildes")
    async def g_top(interaction: discord.Interaction):
        rows = guild_top(15)
        if not rows:
            await interaction.response.send_message("Aucune guilde pour l'instant.", ephemeral=True); return
        lines = []
        for i, g in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i}.`")
            tag = f" [{g['tag']}]" if g.get("tag") else ""
            lines.append(f"{medal} **{g['name']}**{tag} — niv **{g['level']}** · {g['members']} membres")
        emb = discord.Embed(title="🏆 Classement des guildes", description="\n".join(lines),
                            color=0xF2B33A)
        await interaction.response.send_message(embed=emb)

    @g_accept.autocomplete("nom")
    @g_info.autocomplete("nom")
    async def _guild_name_ac(interaction: discord.Interaction, current: str):
        try:
            q = (current or "").strip().lower()
            rows = guild_top(200)
            out = [g["name"] for g in rows if q in g["name"].lower()][:25] if q else [g["name"] for g in rows[:25]]
            return [app_commands.Choice(name=n[:100], value=n[:100]) for n in out]
        except Exception:
            return []

    bot.tree.add_command(grp)

    # ===== /guildprofile : carte de visite riche d'une guilde =====
    _ROLE_ICON = {"master": "👑", "officer": "🔧", "member": "▫️"}
    _ROLE_RANK = {"master": 0, "officer": 1, "member": 2}

    def _build_member_rows(g):
        """[{user_id, role, power, cards}] pour tous les membres (calcul stats)."""
        rows = []
        for m in guild_members(g["id"]):
            try:
                st = compute_player_combat_stats(m["user_id"])
                pw = combat_power(st["hp"], st["atk"])
            except Exception:
                pw = 0
            try:
                cards = user_card_count(m["user_id"])
            except Exception:
                cards = 0
            rows.append({"user_id": m["user_id"], "role": m["role"], "power": pw, "cards": cards})
        return rows

    def _guildprofile_embed(g, rows, sort):
        cfg = get_guild_config()
        if sort == "role":
            rows = sorted(rows, key=lambda r: (_ROLE_RANK.get(r["role"], 9), -r["power"]))
        elif sort == "cards":
            rows = sorted(rows, key=lambda r: -r["cards"])
        else:
            sort = "power"; rows = sorted(rows, key=lambda r: -r["power"])
        total_power = sum(r["power"] for r in rows)
        # barre d'XP dans le niveau
        lvl = g["level"]; maxlv = int(cfg.get("max_level", 60))
        cur = _xp_needed_cumul(lvl, cfg); nxt = _xp_needed_cumul(lvl + 1, cfg)
        into = g["xp"] - cur; span = max(1, nxt - cur)
        bar = _guild_xp_bar(bot, into, span)
        pct = 100 if lvl >= maxlv else int(100 * into / span)
        tag = f" [{g['tag']}]" if g.get("tag") else ""
        emblem = g.get("emblem") or "🛡️"
        emb = discord.Embed(title=f"{emblem} {g['name']}{tag}", color=profile_color_hex(g.get("color"), 0x8e44ad))
        emb.add_field(
            name=f"Niveau {lvl}" + (" (MAX)" if lvl >= maxlv else ""),
            value=(f"{bar}  **{pct}%**\n" + (f"_{_fmt_n(into)} / {_fmt_n(span)} XP_" if lvl < maxlv else f"_{_fmt_n(g['xp'])} XP_")),
            inline=False)
        # Puissance totale en emojis chiffres (les membres restent en texte)
        try:
            from services.card_boss import _power_digits
            pw_str = _power_digits(bot, total_power)
        except Exception:
            pw_str = f"**{_fmt_n(total_power)}**"
        emb.add_field(name="⚡ Puissance totale de la guilde", value=pw_str + "\n​", inline=False)
        emb.add_field(name="💰 Banque", value=f"{_fmt_n(g['bank'])} ✨\n​", inline=False)
        # Prochain palier (niveau + ce qu'il apporte de NOUVEAU)
        cur_rew = guild_rewards_for_level(lvl, cfg)
        nxt = next((p for p in sorted(cfg.get("rewards", []), key=lambda x: x.get("level", 0))
                    if p.get("level", 0) > lvl), None)
        if lvl >= maxlv or not nxt:
            up_txt = "🏅 Palier maximum atteint."
        else:
            bits = []
            if nxt.get("essence_pct"): bits.append(f"+{nxt['essence_pct']}% essences")
            if nxt.get("xp_pct"): bits.append(f"+{nxt['xp_pct']}% XP")
            if nxt.get("roll_cd_min"): bits.append(f"−{nxt['roll_cd_min']} min CD roll")
            if nxt.get("charges"): bits.append(f"+{nxt['charges']} roll/h")
            if nxt.get("wishlist"): bits.append(f"+{nxt['wishlist']} wishlist")
            if nxt.get("boss_pct"): bits.append(f"+{nxt['boss_pct']}% loot boss")
            for k, lbl in (("bank", "🏦 banque"), ("raids", "⚔️ raids"), ("shop", "🛒 boutique")):
                if nxt.get(k) and not cur_rew.get(k):
                    bits.append(f"débloque {lbl}")
            up_txt = f"**Niveau {nxt['level']}** → " + (" · ".join(bits) or "—")
        emb.add_field(name="⬆️ Prochain palier", value=up_txt, inline=False)
        lines = []
        for r in rows[:30]:
            lines.append(f"{_ROLE_ICON.get(r['role'],'▫️')} <@{r['user_id']}> — "
                         f"⚡ {_fmt_n(r['power'])} · {r['cards']} cartes")
        emb.add_field(name="───────────────────────",
                      value=f"**Membres ({len(rows)})** — tri : {sort}\n" + ("\n".join(lines) or "—"),
                      inline=False)
        return emb

    class ShopBuyView(discord.ui.View):
        def __init__(self, gid):
            super().__init__(timeout=120)
            self.gid = gid
            for it in (get_guild_config().get("shop") or [])[:20]:
                b = discord.ui.Button(label=f"{it['name']} — {it['cost']} ✨",
                                      style=discord.ButtonStyle.primary)
                b.callback = self._mk(it)
                self.add_item(b)

        def _mk(self, it):
            async def cb(inter: discord.Interaction):
                if not guild_get(self.gid):
                    await inter.response.send_message("Guilde dissoute.", ephemeral=True); return
                if guild_member_role(self.gid, inter.user.id) not in ("master", "officer"):
                    await inter.response.send_message("Réservé au Maître / Officiers.", ephemeral=True); return
                if not guild_bank_spend(self.gid, int(it["cost"])):
                    await inter.response.send_message("Banque de guilde insuffisante.", ephemeral=True); return
                t = it["type"]; v = int(it["value"])
                if t == "guild_xp":
                    guild_add_xp(self.gid, v); eff = f"+{v} XP guilde"
                elif t == "rolls_all":
                    for mid in guild_member_ids(self.gid):
                        roll_give_user(mid, v)
                    eff = f"+{v} rolls à chaque membre"
                elif t == "essence_all":
                    for mid in guild_member_ids(self.gid):
                        currency_add(mid, v)
                    eff = f"+{v} ✨ à chaque membre"
                else:
                    eff = "effet appliqué"
                g = guild_get(self.gid)
                await inter.response.send_message(
                    f"🛒 **{it['name']}** acheté par {inter.user.mention} → {eff}. "
                    f"Banque restante : {_fmt_n(g['bank'])} ✨.",
                    allowed_mentions=discord.AllowedMentions.none())
            return cb

    def _unlock_level(cfg, key):
        """Niveau minimum ou un deblocage (bank/shop/raids) devient actif, ou None."""
        for p in sorted(cfg.get("rewards", []), key=lambda x: x.get("level", 0)):
            if p.get(key):
                return p.get("level")
        return None

    class GuildProfileView(discord.ui.View):
        def __init__(self, gid, rows, invoker_role=None):
            super().__init__(timeout=180)
            self.gid = gid; self.rows = rows; self.sort = "power"
            self.invoker_role = invoker_role
            cfg = get_guild_config()
            g = guild_get(gid)
            rew = guild_rewards_for_level(g["level"], cfg) if g else {}
            self._bank_ok = bool(rew.get("bank"))
            self._shop_ok = bool(rew.get("shop"))
            self._bank_lv = _unlock_level(cfg, "bank")
            self._shop_lv = _unlock_level(cfg, "shop")
            # Boutique : visible seulement Maitre / Officier
            if invoker_role not in ("master", "officer"):
                self.remove_item(self.b_shop)
            elif not self._shop_ok:
                self.b_shop.style = discord.ButtonStyle.secondary; self.b_shop.emoji = "🔒"
            # Customisation : Maitre seulement
            if invoker_role != "master":
                self.remove_item(self.b_custom)
            # Verrou visuel banque (gris + 🔒) si pas debloque ; reste cliquable
            if not self._bank_ok:
                self.b_bank.style = discord.ButtonStyle.secondary; self.b_bank.emoji = "🔒"

        async def _refresh(self, interaction):
            g = guild_get(self.gid)
            if not g:
                await interaction.response.edit_message(content="Guilde dissoute.", embed=None, view=None); return
            await interaction.response.edit_message(embed=_guildprofile_embed(g, self.rows, self.sort), view=self)

        _SORT_ROT = {"power": "role", "role": "cards", "cards": "power"}
        _SORT_LBL = {"power": "Tri : puissance", "role": "Tri : rôle", "cards": "Tri : cartes"}
        _SORT_EMO = {"power": "⚡", "role": "👑", "cards": "🎴"}

        @discord.ui.button(label="Tri : puissance", emoji="⚡", style=discord.ButtonStyle.secondary)
        async def s_rotate(self, interaction, btn):
            self.sort = self._SORT_ROT.get(self.sort, "power")
            btn.label = self._SORT_LBL[self.sort]; btn.emoji = self._SORT_EMO[self.sort]
            await self._refresh(interaction)

        @discord.ui.button(label="Quêtes", emoji="📜", style=discord.ButtonStyle.primary)
        async def b_quests(self, interaction, btn):
            g = guild_get(self.gid)
            if not g:
                await interaction.response.edit_message(content="Guilde dissoute.", embed=None, view=None); return
            qv = QuestView(self.gid, self.rows, self.invoker_role, interaction.user.id)
            await interaction.response.edit_message(
                embed=_quests_daily_embed(interaction.client, g, interaction.user.id), view=qv)

        @discord.ui.button(label="Banque", emoji="💰", style=discord.ButtonStyle.success, row=1)
        async def b_bank(self, interaction, btn):
            if not self._bank_ok:
                lv = self._bank_lv or "?"
                await interaction.response.send_message(
                    f"🔒 La **banque** se débloque au **niveau {lv}** de guilde.", ephemeral=True); return
            g = guild_get(self.gid)
            await interaction.response.send_message(
                f"💰 **Banque de {g['name']}** : {_fmt_n(g['bank'])} ✨\n"
                f"Alimente-la avec `/guild donate montant:…`.", ephemeral=True)

        @discord.ui.button(label="Boutique", emoji="🛒", style=discord.ButtonStyle.primary, row=1)
        async def b_shop(self, interaction, btn):
            if not self._shop_ok:
                lv = self._shop_lv or "?"
                await interaction.response.send_message(
                    f"🔒 La **boutique** se débloque au **niveau {lv}** de guilde.", ephemeral=True); return
            items = get_guild_config().get("shop") or []
            if not items:
                await interaction.response.send_message(
                    "🛒 Boutique vide (à configurer par l'owner).", ephemeral=True); return
            desc = "\n".join(f"**{it['name']}** — {_fmt_n(it['cost'])} ✨\n_{it.get('desc','')}_"
                             for it in items)
            emb = discord.Embed(title="🛒 Boutique de guilde", description=desc, color=0x4ade80)
            emb.set_footer(text="Achat réservé au Maître / Officiers · payé par la banque")
            await interaction.response.send_message(embed=emb, view=ShopBuyView(self.gid), ephemeral=True)

        @discord.ui.button(label="Guilde customisation", emoji="🎨", style=discord.ButtonStyle.secondary, row=1)
        async def b_custom(self, interaction, btn):
            if guild_member_role(self.gid, interaction.user.id) != "master":
                await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
            g = guild_get(self.gid)
            await interaction.response.send_message(
                "🎨 **Customisation de guilde** — choisis la couleur d'embed et l'emblème "
                "(emoji standard) appliqué sur le `/cardprofile` de tes membres.",
                view=GuildCustomizeView(self.gid), ephemeral=True)

    class GuildCustomizeView(discord.ui.View):
        def __init__(self, gid):
            super().__init__(timeout=300)
            self.gid = gid
            self.add_item(self._ColorSelect(gid))

        class _ColorSelect(discord.ui.Select):
            def __init__(self, gid):
                self.gid = gid
                g = guild_get(gid)
                cur = (g or {}).get("color")
                opts = [discord.SelectOption(label=c["name"], value=c["key"],
                                             default=(c["key"] == cur)) for c in PROFILE_COLORS]
                super().__init__(placeholder="🎨 Couleur d'embed de la guilde",
                                 options=opts, min_values=1, max_values=1)

            async def callback(self, interaction):
                if guild_member_role(self.gid, interaction.user.id) != "master":
                    await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
                key = self.values[0]
                guild_set_color(self.gid, key)
                col = next((c for c in PROFILE_COLORS if c["key"] == key), None)
                await interaction.response.send_message(
                    f"🎨 Couleur d'embed **{col['name'] if col else key}** appliquée.", ephemeral=True)

        @discord.ui.button(label="Renommer la guilde", emoji="✏️", style=discord.ButtonStyle.secondary)
        async def rename(self, interaction, btn):
            if guild_member_role(self.gid, interaction.user.id) != "master":
                await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
            g = guild_get(self.gid)
            # Cooldown 1/mois (30 jours)
            ra = (g or {}).get("renamed_at")
            if ra:
                try:
                    last = _dt.datetime.fromisoformat(ra.replace("Z", ""))
                    days = (_dt.datetime.utcnow() - last).total_seconds() / 86400
                    if days < 30:
                        await interaction.response.send_message(
                            f"🔒 Tu as déjà renommé la guilde récemment. Attends encore "
                            f"**{int(30 - days) + 1} jour(s)** (1 renommage / mois).", ephemeral=True)
                        return
                except Exception:
                    pass
            await interaction.response.send_modal(GuildRenameModal(self.gid))

        @discord.ui.button(label="Définir l'emblème", emoji="🏅", style=discord.ButtonStyle.primary)
        async def set_emblem(self, interaction, btn):
            if guild_member_role(self.gid, interaction.user.id) != "master":
                await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
            await interaction.response.send_modal(EmblemModal(self.gid))

        @discord.ui.button(label="Retirer l'emblème", emoji="🗑️", style=discord.ButtonStyle.secondary)
        async def clear_emblem(self, interaction, btn):
            if guild_member_role(self.gid, interaction.user.id) != "master":
                await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
            guild_set_emblem(self.gid, None)
            await interaction.response.send_message("🗑️ Emblème retiré.", ephemeral=True)

    class EmblemModal(discord.ui.Modal, title="Emblème de guilde"):
        def __init__(self, gid):
            super().__init__()
            self.gid = gid
            self.emoji_in = discord.ui.TextInput(
                label="Emoji (standard uniquement)",
                placeholder="Ex: 🔥 ⚔️ 🐉 — pas d'emoji personnalisé",
                max_length=8, required=True)
            self.add_item(self.emoji_in)

        async def on_submit(self, interaction):
            val = str(self.emoji_in.value).strip()
            if not val:
                await interaction.response.send_message("Emoji vide.", ephemeral=True); return
            if "<" in val or ":" in val:
                await interaction.response.send_message(
                    "❌ Emoji personnalisé refusé. Utilise un emoji standard (🔥 ⚔️ 🐉).",
                    ephemeral=True); return
            if any(ch.isalnum() for ch in val):
                await interaction.response.send_message(
                    "❌ Ça doit être un emoji, pas du texte.", ephemeral=True); return
            guild_set_emblem(self.gid, val)
            await interaction.response.send_message(
                f"🏅 Emblème **{val}** appliqué. Il apparaît sur le `/cardprofile` de tes membres.",
                ephemeral=True)

    class GuildRenameModal(discord.ui.Modal, title="Renommer la guilde"):
        def __init__(self, gid):
            super().__init__()
            self.gid = gid
            self.name_in = discord.ui.TextInput(
                label="Nouveau nom de guilde",
                placeholder="3 à 32 caractères",
                min_length=3, max_length=32, required=True)
            self.add_item(self.name_in)

        async def on_submit(self, interaction):
            if guild_member_role(self.gid, interaction.user.id) != "master":
                await interaction.response.send_message("Réservé au Maître.", ephemeral=True); return
            g = guild_get(self.gid)
            if not g:
                await interaction.response.send_message("Guilde dissoute.", ephemeral=True); return
            # Re-check cooldown a la soumission (anti contournement)
            ra = g.get("renamed_at")
            if ra:
                try:
                    last = _dt.datetime.fromisoformat(ra.replace("Z", ""))
                    days = (_dt.datetime.utcnow() - last).total_seconds() / 86400
                    if days < 30:
                        await interaction.response.send_message(
                            f"🔒 Renommage dispo dans **{int(30 - days) + 1} jour(s)** (1 / mois).",
                            ephemeral=True); return
                except Exception:
                    pass
            new = str(self.name_in.value).strip()
            if len(new) < 3:
                await interaction.response.send_message("Nom trop court (3 caractères min).", ephemeral=True); return
            if new.lower() == (g.get("name") or "").lower():
                await interaction.response.send_message("C'est déjà le nom actuel.", ephemeral=True); return
            other = guild_get_by_name(new)
            if other and other["id"] != self.gid:
                await interaction.response.send_message("Ce nom est déjà pris par une autre guilde.", ephemeral=True); return
            guild_set_name(self.gid, new)
            await interaction.response.send_message(
                f"✏️ Guilde renommée en **{new}** ! Prochain renommage dans 30 jours.", ephemeral=True)

    def _quest_bar(into, span, seg=10):
        span = max(1, span)
        filled = min(seg, int(round(seg * into / span)))
        full = str(discord.utils.get(bot.emojis, name="playerlifebarfull") or "🟩")
        empty = str(discord.utils.get(bot.emojis, name="lifebarempty") or "⬛")
        return full * filled + empty * (seg - filled)

    def _quests_daily_embed(bot, g, user_id):
        col = profile_color_hex(g.get("color"), 0x8e44ad)
        emb = discord.Embed(title=f"📜 Quêtes quotidiennes ｜ {g['name']}",
                            description="Tes quêtes perso du jour. Reset chaque jour. "
                                        "Compléter = XP pour ta guilde.", color=col)
        for q in guild_quests_daily_get(user_id, g["id"]):
            prog = min(q["progress"], q["target"])
            check = "✅" if q["done"] else "⬜"
            emb.add_field(
                name=f"{check} {q['label']}",
                value=f"{_quest_bar(prog, q['target'])}  **{prog}/{q['target']}**  ·  +{q['xp']} XP",
                inline=False)
        emb.set_footer(text="🔁 Bascule vers les quêtes hebdomadaires de guilde")
        return emb

    def _quests_weekly_embed(bot, g):
        col = profile_color_hex(g.get("color"), 0x8e44ad)
        emb = discord.Embed(title=f"📅 Quêtes hebdomadaires ｜ {g['name']}",
                            description="Objectifs collectifs de la guilde. Tous les membres "
                                        "contribuent. Reset chaque semaine.", color=col)
        for q in guild_quests_weekly_get(g["id"]):
            prog = min(q["progress"], q["target"])
            check = "✅" if q["done"] else "⬜"
            reward = f"+{q['xp']} XP" + (f" · +{_fmt_n(q['bank'])} ✨ banque" if q.get("bank") else "")
            top = q.get("contrib", [])[:3]
            contrib_txt = ""
            if top:
                contrib_txt = "\n" + " · ".join(f"<@{c['user_id']}> ({_fmt_n(c['contrib'])})" for c in top)
            emb.add_field(
                name=f"{check} {q['label']}",
                value=f"{_quest_bar(prog, q['target'])}  **{_fmt_n(prog)}/{_fmt_n(q['target'])}**  ·  {reward}"
                      + contrib_txt,
                inline=False)
        emb.set_footer(text="🔁 Bascule vers tes quêtes quotidiennes")
        return emb

    class QuestView(discord.ui.View):
        def __init__(self, gid, rows, invoker_role, invoker_id):
            super().__init__(timeout=180)
            self.gid = gid; self.rows = rows
            self.invoker_role = invoker_role; self.invoker_id = invoker_id
            self.page = "daily"

        @discord.ui.button(label="Quotidiennes / Hebdo", emoji="🔁", style=discord.ButtonStyle.primary)
        async def toggle(self, interaction, btn):
            g = guild_get(self.gid)
            if not g:
                await interaction.response.edit_message(content="Guilde dissoute.", embed=None, view=None); return
            self.page = "weekly" if self.page == "daily" else "daily"
            emb = (_quests_weekly_embed(interaction.client, g) if self.page == "weekly"
                   else _quests_daily_embed(interaction.client, g, self.invoker_id))
            await interaction.response.edit_message(embed=emb, view=self)

        @discord.ui.button(label="Retour", emoji="◀️", style=discord.ButtonStyle.secondary)
        async def back(self, interaction, btn):
            g = guild_get(self.gid)
            if not g:
                await interaction.response.edit_message(content="Guilde dissoute.", embed=None, view=None); return
            view = GuildProfileView(self.gid, self.rows, invoker_role=self.invoker_role)
            await interaction.response.edit_message(
                embed=_guildprofile_embed(g, self.rows, "power"), view=view)

    @bot.tree.command(name="guildprofile",
                       description="Profil d'une guilde (la tienne par défaut)")
    @app_commands.describe(nom="Nom d'une guilde (sinon la tienne)")
    async def guildprofile(interaction: discord.Interaction, nom: str = None):
        g = guild_get_by_name(nom) if nom else guild_of_user(interaction.user.id)
        if not g:
            await interaction.response.send_message(
                "Guilde introuvable." if nom else "Tu n'es dans aucune guilde.", ephemeral=True)
            return
        await interaction.response.defer()
        rows = _build_member_rows(g)
        inv_role = guild_member_role(g["id"], interaction.user.id)
        view = GuildProfileView(g["id"], rows, invoker_role=inv_role)
        await interaction.followup.send(embed=_guildprofile_embed(g, rows, "power"), view=view)

    @guildprofile.autocomplete("nom")
    async def _gp_ac(interaction: discord.Interaction, current: str):
        try:
            q = (current or "").strip().lower()
            rows = guild_top(200)
            out = [g["name"] for g in rows if q in g["name"].lower()][:25] if q else [g["name"] for g in rows[:25]]
            return [app_commands.Choice(name=n[:100], value=n[:100]) for n in out]
        except Exception:
            return []
