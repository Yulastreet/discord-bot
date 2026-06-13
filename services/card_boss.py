"""Combat de boss coopératif (auto).

Flow :
1. Le boss apparaît -> phase de recrutement.
2. Les joueurs cliquent "Rejoindre" (stats issues de leur collection) et peuvent
   "Choisir ma carte" (définit l'élément de combat).
3. Le combat démarre 2 min après l'apparition, ou 10 s si 5 joueurs ont rejoint.
4. Combat AUTOMATIQUE au tour par tour : l'équipe tape, le boss riposte, jusqu'à
   la mort du boss ou l'anéantissement de l'équipe. Loot pour les participants.
"""
from __future__ import annotations

import asyncio
import random

import discord

from database import (
    BOSS_TIERS, card_boss_create, card_boss_get, card_boss_set_message,
    card_boss_apply_damage, card_boss_set_status,
    boss_participant_add, boss_participant_get, boss_participants_list,
    boss_participant_update, compute_player_combat_stats, element_matchup,
    card_pick_random_exact_rarity, card_get, card_get_by_name, currency_add,
    user_card_add, user_card_count_owned, CARD_ELEMENT_LABELS,
)

_RECRUIT_SECONDS = 120     # fenetre de recrutement par defaut
_QUICK_START_AT = 5        # nb de joueurs qui declenche le demarrage rapide
_QUICK_SECONDS = 10        # delai du demarrage rapide
_TURN_DELAY = 4.0          # secondes entre 2 tours auto
_MAX_TURNS = 60
_BOSS_RATIO = 0.5          # le boss frappe a 50% de son atk

_TIER_RARITY = {1: "epic", 2: "legendary", 3: "mythic", 4: "mythic", 5: "secret"}


def _bar(cur, mx, segments=14):
    cur = max(0, cur)
    filled = min(segments, int(round(segments * cur / mx))) if mx > 0 else 0
    pct = int(round(100 * cur / mx)) if mx > 0 else 0
    return "█" * filled + "░" * (segments - filled) + f"  {pct}%"


def _elem(bot, e):
    try:
        from commandes.cards import _get_element_emoji
        return _get_element_emoji(bot, e)
    except Exception:
        return ""


def _fmt(n):
    return f"{int(n):,}".replace(",", " ")


def _default_element(user_id):
    from database import card_profile_get, get_db
    prof = card_profile_get(user_id) or {}
    cid = prof.get("mid_id") or prof.get("left_id") or prof.get("right_id")
    if cid:
        card = card_get(int(cid))
        if card and card.get("element"):
            return card["element"]
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT c.element FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                  "WHERE uc.user_id = ? AND c.element IS NOT NULL LIMIT 1",
                  (str(user_id),)).fetchone()
    conn.close()
    return r["element"] if r else "eclat"


def build_boss_embed(bot, boss, phase_text="", log=None):
    boss = card_boss_get(boss["id"])
    parts = boss_participants_list(boss["id"])
    color = {"defeated": 0x4ade80, "wiped": 0xff3d57}.get(boss["status"], 0x8e44ad)
    embed = discord.Embed(
        title=f"⚔️ Boss {BOSS_TIERS.get(boss['tier'], {}).get('label','')} ｜ {boss['name']}",
        color=color)
    embed.add_field(name="Élément",
                    value=f"{_elem(bot, boss['element'])} {CARD_ELEMENT_LABELS.get(boss['element'],'?')}",
                    inline=True)
    embed.add_field(name="ATK", value=f"🗡️ {_fmt(boss['atk'])}", inline=True)
    embed.add_field(name="Participants", value=f"👥 {len(parts)}", inline=True)
    embed.add_field(name="❤️ PV du boss",
                    value=f"**{_fmt(boss['hp'])}** / {_fmt(boss['max_hp'])}\n`{_bar(boss['hp'], boss['max_hp'])}`",
                    inline=False)
    if parts:
        lines = []
        for p in parts[:12]:
            ko = " 💀" if p["hp"] <= 0 else ""
            lines.append(f"{_elem(bot, p['element'])} **{p['name']}** — ❤️ {_fmt(max(0,p['hp']))}"
                         f" · 🗡️ {_fmt(p['atk'])}{ko}")
        embed.add_field(name="🛡️ Équipe", value="\n".join(lines), inline=False)
    if log:
        embed.add_field(name="📜 Combat", value="\n".join(log[-4:]), inline=False)
    if phase_text:
        embed.description = phase_text
    elif boss["status"] == "defeated":
        embed.description = "🎉 **Boss vaincu !** Récompenses distribuées."
    elif boss["status"] == "wiped":
        embed.description = "💀 **L'équipe a été anéantie.** Le boss survit."
    return embed


class JoinView(discord.ui.View):
    def __init__(self, boss_id):
        super().__init__(timeout=None)
        self.boss_id = boss_id

    @discord.ui.button(label="Rejoindre", style=discord.ButtonStyle.success, emoji="🛡️")
    async def join(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True)
            return
        uid = interaction.user.id
        if boss_participant_get(self.boss_id, uid):
            await interaction.response.send_message("Tu es déjà dans l'équipe.", ephemeral=True)
            return
        stats = compute_player_combat_stats(uid)
        boss_participant_add(self.boss_id, uid, interaction.user.display_name,
                             _default_element(uid), stats["hp"], stats["atk"])
        await interaction.response.send_message(
            "🛡️ Tu as rejoint ! Élément par défaut = ta carte vedette. "
            "Utilise **🎴 Choisir ma carte** pour le changer.", ephemeral=True)
        try:
            await interaction.message.edit(embed=build_boss_embed(interaction.client, boss), view=self)
        except Exception:
            pass

    @discord.ui.button(label="Choisir ma carte", style=discord.ButtonStyle.secondary, emoji="🎴")
    async def choose(self, interaction, btn):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "recruiting":
            await interaction.response.send_message("Le recrutement est terminé.", ephemeral=True)
            return
        if not boss_participant_get(self.boss_id, interaction.user.id):
            await interaction.response.send_message("Rejoins d'abord (🛡️).", ephemeral=True)
            return
        await interaction.response.send_modal(_ChooseCardModal(self.boss_id))


class _ChooseCardModal(discord.ui.Modal, title="Choisir ma carte de combat"):
    nom = discord.ui.TextInput(label="Nom de la carte (que tu possèdes)",
                                placeholder="ex: Goku", required=True, max_length=100)

    def __init__(self, boss_id):
        super().__init__()
        self.boss_id = boss_id

    async def on_submit(self, interaction):
        card = card_get_by_name(str(self.nom.value).strip())
        uid = interaction.user.id
        if not card:
            await interaction.response.send_message("Carte introuvable.", ephemeral=True); return
        if user_card_count_owned(uid, card["id"]) <= 0:
            await interaction.response.send_message(f"Tu ne possèdes pas **{card['name']}**.", ephemeral=True); return
        elem = card.get("element") or "eclat"
        boss_participant_update(self.boss_id, uid, element=elem)
        await interaction.response.send_message(
            f"🎴 Tu combattras avec **{card['name']}** "
            f"({_elem(interaction.client, elem)} {CARD_ELEMENT_LABELS.get(elem,'?')}).", ephemeral=True)
        try:
            boss = card_boss_get(self.boss_id)
            await interaction.message.edit(embed=build_boss_embed(interaction.client, boss))
        except Exception:
            pass


async def spawn_boss(bot, guild_id, channel_id, tier=1):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return None
    tier = max(1, min(5, int(tier)))
    cfg = BOSS_TIERS[tier]
    avatar = card_pick_random_exact_rarity(_TIER_RARITY.get(tier, "epic")) or card_pick_random_exact_rarity("epic")
    name = avatar["name"] if avatar else "Entité inconnue"
    element = (avatar.get("element") if avatar else None) or random.choice(list(CARD_ELEMENT_LABELS.keys()))
    bid = card_boss_create(guild_id, channel_id, name, element, tier, cfg["hp"], cfg["atk"])
    card_boss_set_status(bid, "recruiting")
    boss = card_boss_get(bid)
    embed = build_boss_embed(bot, boss,
                              phase_text=f"🐲 **Recrutement !** Le combat démarre dans **2 min** "
                                         f"(ou 10 s si **{_QUICK_START_AT}** joueurs).\n"
                                         f"**🛡️ Rejoindre** puis **🎴 Choisir ma carte**.")
    if avatar and str(avatar.get("image_url") or "").startswith("http"):
        embed.set_image(url=avatar["image_url"])
    view = JoinView(bid)
    msg = await channel.send(content="🐲 **Un boss est apparu !**", embed=embed, view=view)
    card_boss_set_message(bid, msg.id)
    asyncio.create_task(_run_boss(bot, bid, msg, view))
    return bid


async def _run_boss(bot, bid, msg, view):
    try:
        # ── Phase recrutement ──
        waited = 0.0
        quick = False
        while waited < _RECRUIT_SECONDS:
            await asyncio.sleep(3)
            waited += 3
            boss = card_boss_get(bid)
            if not boss or boss["status"] != "recruiting":
                return
            if len(boss_participants_list(bid)) >= _QUICK_START_AT:
                quick = True
                break
        if quick:
            # compte a rebours rapide
            boss = card_boss_get(bid)
            try:
                await msg.edit(embed=build_boss_embed(bot, boss,
                    phase_text=f"⚡ **{_QUICK_START_AT} joueurs !** Le combat démarre dans **{_QUICK_SECONDS} s**."))
            except Exception:
                pass
            await asyncio.sleep(_QUICK_SECONDS)

        parts = boss_participants_list(bid)
        if not parts:
            card_boss_set_status(bid, "expired")
            for ch in view.children:
                ch.disabled = True
            try:
                await msg.edit(content="🕸️ **Personne n'a rejoint le combat.**",
                               embed=build_boss_embed(bot, card_boss_get(bid),
                                                      phase_text="Aucun participant. Le boss disparaît."),
                               view=view)
            except Exception:
                pass
            return

        # ── Combat automatique ──
        card_boss_set_status(bid, "fighting")
        for ch in view.children:
            ch.disabled = True
        log = ["⚔️ **Le combat commence !**"]
        try:
            await msg.edit(content="⚔️ **Combat en cours…**",
                           embed=build_boss_embed(bot, card_boss_get(bid), log=log), view=view)
        except Exception:
            pass

        turn = 0
        while turn < _MAX_TURNS:
            turn += 1
            await asyncio.sleep(_TURN_DELAY)
            boss = card_boss_get(bid)
            if not boss or boss["status"] != "fighting":
                return
            alive = [p for p in boss_participants_list(bid) if p["hp"] > 0]
            if not alive:
                card_boss_set_status(bid, "wiped")
                break
            # Equipe attaque
            total = 0
            best_eff = 1.0
            for p in alive:
                m = element_matchup(p["element"], boss["element"])
                total += max(1, int(p["atk"] * m))
                best_eff = max(best_eff, m)
            boss_hp = card_boss_apply_damage(bid, total)
            eff = " 🔥" if best_eff > 1 else ""
            log.append(f"Tour {turn} · 🗡️ L'équipe inflige **{_fmt(total)}**{eff}")
            if boss_hp <= 0:
                card_boss_set_status(bid, "defeated")
                break
            # Boss riposte sur une cible aleatoire
            target = random.choice(alive)
            cm = element_matchup(boss["element"], target["element"])
            dmg = int(boss["atk"] * cm * _BOSS_RATIO)
            new_hp = max(0, target["hp"] - dmg)
            boss_participant_update(bid, target["user_id"], hp=new_hp)
            ko = " 💀 **KO !**" if new_hp <= 0 else ""
            log.append(f"Tour {turn} · 👹 Le boss frappe **{target['name']}** : -{_fmt(dmg)}{ko}")
            try:
                await msg.edit(embed=build_boss_embed(bot, card_boss_get(bid), log=log), view=view)
            except Exception:
                pass

        # ── Fin ──
        boss = card_boss_get(bid)
        if boss["status"] == "fighting":   # cap de tours atteint
            card_boss_set_status(bid, "wiped")
            boss = card_boss_get(bid)
        await _finish(bot, bid, msg, view, log, boss["status"] == "defeated")
    except Exception as e:
        print(f"[boss] run err: {e!r}")


async def _finish(bot, bid, msg, view, log, victory):
    boss = card_boss_get(bid)
    parts = boss_participants_list(bid)
    for ch in view.children:
        ch.disabled = True
    loot_lines = []
    if victory:
        tier = boss["tier"]
        rar = _TIER_RARITY.get(tier, "epic")
        for p in parts:
            if p["damage"] <= 0:
                continue
            ess = tier * 150 + p["damage"] // 200
            currency_add(p["user_id"], ess)
            card = card_pick_random_exact_rarity(rar)
            extra = ""
            if card:
                user_card_add(p["user_id"], card["id"])
                extra = f" + **{card['name']}**"
            loot_lines.append(f"<@{p['user_id']}> : +{ess} ✨{extra}")
        log.append("🎉 **Boss vaincu !**")
    else:
        log.append("💀 **Défaite.**")
    try:
        await msg.edit(content=("🎉 **Victoire !**" if victory else "💀 **Défaite…**"),
                       embed=build_boss_embed(bot, boss, log=log), view=view)
    except Exception:
        pass
    if victory and loot_lines:
        try:
            ch = bot.get_channel(int(boss["channel_id"]))
            if ch:
                await ch.send("🎁 **Butin du boss :**\n" + "\n".join(loot_lines[:20]))
        except Exception:
            pass
