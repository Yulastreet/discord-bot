"""Combat de boss coopératif.

Un boss apparaît dans un salon. Les joueurs rejoignent (stats issues de leur
collection : PV/ATK + élément de leur carte vedette) et l'attaquent au tour par
tour. L'élément applique avantage/désavantage. Boss mort = loot pour tous.
"""
from __future__ import annotations

import time as _time
import random

import discord

from database import (
    BOSS_TIERS, card_boss_create, card_boss_get, card_boss_get_by_message,
    card_boss_set_message, card_boss_apply_damage, card_boss_set_status,
    boss_participant_add, boss_participant_get, boss_participants_list,
    boss_participant_update, compute_player_combat_stats, element_matchup,
    card_pick_random_exact_rarity, card_get, currency_add, user_card_add,
    CARD_ELEMENT_LABELS,
)

_ATTACK_COOLDOWN = 4.0   # secondes entre 2 attaques d'un meme joueur
_BOSS_COUNTER_RATIO = 0.45  # le boss riposte a 45% de son atk (pour que ca dure)

# tier -> rareté de la carte "avatar" du boss + loot
_TIER_RARITY = {1: "epic", 2: "legendary", 3: "mythic", 4: "mythic", 5: "secret"}


def _bar(cur, mx, segments=12):
    cur = max(0, cur)
    filled = int(round(segments * cur / mx)) if mx > 0 else 0
    filled = min(segments, filled)
    pct = int(round(100 * cur / mx)) if mx > 0 else 0
    return "█" * filled + "░" * (segments - filled) + f"  {pct}%"


def _elem_emoji(bot, element):
    try:
        from commandes.cards import _get_element_emoji
        return _get_element_emoji(bot, element)
    except Exception:
        return ""


def _player_element(user_id):
    """Élément de combat = celui de la carte 'milieu' du profil, sinon 1ere carte owned."""
    from database import card_profile_get, get_db
    prof = card_profile_get(user_id) or {}
    cid = prof.get("mid_id") or prof.get("left_id") or prof.get("right_id")
    if cid:
        card = card_get(int(cid))
        if card and card.get("element"):
            return card["element"]
    # fallback : element d'une carte possedee
    conn = get_db(); c = conn.cursor()
    r = c.execute("SELECT c.element FROM user_cards uc JOIN cards c ON c.id = uc.card_id "
                  "WHERE uc.user_id = ? AND c.element IS NOT NULL LIMIT 1",
                  (str(user_id),)).fetchone()
    conn.close()
    return r["element"] if r else "eclat"


def build_boss_embed(bot, boss):
    boss = card_boss_get(boss["id"])  # toujours frais depuis la DB
    parts = boss_participants_list(boss["id"])
    el = _elem_emoji(bot, boss["element"])
    color = 0x8e44ad if boss["status"] != "defeated" else 0x4ade80
    embed = discord.Embed(
        title=f"⚔️ Boss {BOSS_TIERS.get(boss['tier'], {}).get('label', '')} ｜ {boss['name']}",
        color=color,
    )
    embed.add_field(name="Élément", value=f"{el} {CARD_ELEMENT_LABELS.get(boss['element'], '?')}", inline=True)
    embed.add_field(name="ATK", value=f"🗡️ {boss['atk']:,}".replace(",", " "), inline=True)
    embed.add_field(name="Participants", value=f"👥 {len(parts)}", inline=True)
    embed.add_field(
        name=f"❤️ PV du boss",
        value=f"**{boss['hp']:,}** / {boss['max_hp']:,}".replace(",", " ") + f"\n`{_bar(boss['hp'], boss['max_hp'])}`",
        inline=False)
    if parts:
        lines = []
        for p in parts[:12]:
            pe = _elem_emoji(bot, p["element"])
            ko = " 💀" if p["hp"] <= 0 else ""
            lines.append(f"{pe} **{p['name']}** — ❤️ {max(0,p['hp']):,}".replace(",", " ")
                         + f" · 🗡️ {p['atk']:,}".replace(",", " ") + ko)
        embed.add_field(name="🛡️ Équipe", value="\n".join(lines), inline=False)
    if boss["status"] == "defeated":
        embed.description = "🎉 **Boss vaincu !** Récompenses distribuées aux participants."
    elif boss["status"] == "wiped":
        embed.description = "💀 **L'équipe a été anéantie...** Le boss survit."
    else:
        embed.description = ("Clique **Rejoindre** puis **Attaquer** ! L'élément donne "
                              "avantage (+25%) ou désavantage (-20%).")
    return embed


async def spawn_boss(bot, guild_id, channel_id, tier=1):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return None
    tier = max(1, min(5, int(tier)))
    cfg = BOSS_TIERS[tier]
    rarity = _TIER_RARITY.get(tier, "epic")
    avatar = card_pick_random_exact_rarity(rarity) or card_pick_random_exact_rarity("epic")
    name = avatar["name"] if avatar else "Entité inconnue"
    element = avatar.get("element") if avatar else "neant"
    if not element:
        element = random.choice(list(CARD_ELEMENT_LABELS.keys()))
    bid = card_boss_create(guild_id, channel_id, name, element, tier, cfg["hp"], cfg["atk"])
    boss = card_boss_get(bid)
    embed = build_boss_embed(bot, boss)
    if avatar and avatar.get("image_url") and str(avatar["image_url"]).startswith("http"):
        embed.set_image(url=avatar["image_url"])
    view = BossView(bid)
    msg = await channel.send(content="🐲 **Un boss est apparu !**", embed=embed, view=view)
    card_boss_set_message(bid, msg.id)
    return bid


class BossView(discord.ui.View):
    def __init__(self, boss_id):
        super().__init__(timeout=None)
        self.boss_id = boss_id

    @discord.ui.button(label="Rejoindre", style=discord.ButtonStyle.success, emoji="🛡️")
    async def join(self, interaction: discord.Interaction, btn: discord.ui.Button):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "open":
            await interaction.response.send_message("Ce combat est terminé.", ephemeral=True)
            return
        uid = interaction.user.id
        if boss_participant_get(self.boss_id, uid):
            await interaction.response.send_message("Tu es déjà dans l'équipe.", ephemeral=True)
            return
        stats = compute_player_combat_stats(uid)
        elem = _player_element(uid)
        boss_participant_add(self.boss_id, uid, interaction.user.display_name, elem,
                             stats["hp"], stats["atk"])
        await interaction.response.edit_message(
            embed=build_boss_embed(interaction.client, boss), view=self)

    @discord.ui.button(label="Attaquer", style=discord.ButtonStyle.danger, emoji="🗡️")
    async def attack(self, interaction: discord.Interaction, btn: discord.ui.Button):
        boss = card_boss_get(self.boss_id)
        if not boss or boss["status"] != "open":
            await interaction.response.send_message("Ce combat est terminé.", ephemeral=True)
            return
        uid = interaction.user.id
        p = boss_participant_get(self.boss_id, uid)
        if not p:
            await interaction.response.send_message("Rejoins d'abord le combat (🛡️ Rejoindre).", ephemeral=True)
            return
        if p["hp"] <= 0:
            await interaction.response.send_message("Tu es **KO** sur ce boss.", ephemeral=True)
            return
        now = _time.time()
        if now - (p["last_attack"] or 0) < _ATTACK_COOLDOWN:
            wait = _ATTACK_COOLDOWN - (now - (p["last_attack"] or 0))
            await interaction.response.send_message(f"⏳ Recharge ({wait:.0f}s).", ephemeral=True)
            return
        # Degats joueur -> boss
        mult = element_matchup(p["element"], boss["element"])
        dmg = max(1, int(p["atk"] * mult))
        boss_hp = card_boss_apply_damage(self.boss_id, dmg)
        # Riposte boss -> joueur
        cmult = element_matchup(boss["element"], p["element"])
        counter = int(boss["atk"] * cmult * _BOSS_COUNTER_RATIO)
        new_hp = max(0, p["hp"] - counter)
        boss_participant_update(self.boss_id, uid, hp=new_hp, add_damage=dmg, last_attack=now)

        eff = " _(super efficace !)_" if mult > 1 else (" _(peu efficace)_" if mult < 1 else "")
        if boss_hp <= 0:
            card_boss_set_status(self.boss_id, "defeated")
            await self._finish(interaction, victory=True)
            return
        # Wipe ? tous KO
        parts = boss_participants_list(self.boss_id)
        if parts and all(pp["hp"] <= 0 for pp in parts):
            card_boss_set_status(self.boss_id, "wiped")
            await self._finish(interaction, victory=False)
            return
        await interaction.response.edit_message(
            embed=build_boss_embed(interaction.client, boss), view=self)
        try:
            await interaction.followup.send(
                f"🗡️ Tu infliges **{dmg:,}**".replace(",", " ") + f" dégâts{eff}"
                + (f" · tu encaisses {counter:,}".replace(",", " ") if counter else ""),
                ephemeral=True)
        except Exception:
            pass

    async def _finish(self, interaction, victory: bool):
        boss = card_boss_get(self.boss_id)
        parts = boss_participants_list(self.boss_id)
        for child in self.children:
            child.disabled = True
        loot_lines = []
        if victory:
            tier = boss["tier"]
            reward_rarity = _TIER_RARITY.get(tier, "epic")
            for p in parts:
                if p["damage"] <= 0:
                    continue
                ess = tier * 150 + p["damage"] // 200
                currency_add(p["user_id"], ess)
                card = card_pick_random_exact_rarity(reward_rarity)
                cname = ""
                if card:
                    user_card_add(p["user_id"], card["id"])
                    cname = f" + **{card['name']}**"
                loot_lines.append(f"<@{p['user_id']}> : +{ess} ✨{cname}")
        emb = build_boss_embed(interaction.client, boss)
        await interaction.response.edit_message(embed=emb, view=self)
        if victory and loot_lines:
            try:
                await interaction.channel.send(
                    "🎁 **Butin du boss :**\n" + "\n".join(loot_lines[:20]))
            except Exception:
                pass
