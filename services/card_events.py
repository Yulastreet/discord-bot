"""Cards Events : drops aleatoires dans un salon, captcha texte -> 1er a taper code gagne.

Owner configure par guild : channel + interval (min-max) + min_rarity.
Loop task verifie chaque minute si un drop doit etre lance.
Listener on_message verifie si message correspond a un code event pending.
"""
from __future__ import annotations

import datetime as _dt
import random
from typing import Optional

import discord

from database import (
    card_event_config_all_enabled,
    card_event_config_set,
    card_event_log_create,
    card_event_log_get_pending_in_channel,
    card_event_log_update_message,
    card_event_log_claim,
    card_pick_random_by_min_rarity,
    user_card_add,
)


RARITY_COLORS = {
    "common":    0x9aa0a6,
    "rare":      0x4cb5f9,
    "epic":      0xa86dff,
    "legendary": 0xffa726,
    "mythic":    0xff3d57,
    "secret":    0x1c1c1e,
}
RARITY_EMOJIS = {
    "common": "⚪", "rare": "🔵", "epic": "🟣",
    "legendary": "🟠", "mythic": "🔴", "secret": "🌈",
}
# Chars sans ambiguite (pas O/0, pas I/l/1)
CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


def _gen_code() -> str:
    n = random.randint(5, 6)
    return "".join(random.choice(CODE_CHARS) for _ in range(n))


def _now_iso() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _schedule_next_drop(guild_id: str, cfg: dict) -> str:
    """Calcule prochain drop dans intervalle random [min, max]. UTC ISO."""
    mn = max(1, int(cfg.get("min_interval_min") or 300))
    mx = max(mn, int(cfg.get("max_interval_min") or 600))
    delay = random.randint(mn, mx)
    next_at = _dt.datetime.utcnow() + _dt.timedelta(minutes=delay)
    next_iso = next_at.strftime("%Y-%m-%d %H:%M:%S")
    card_event_config_set(guild_id, next_drop_at=next_iso)
    return next_iso


async def trigger_event_drop(bot, guild_id: int, channel_id: int,
                                min_rarity: str = "rare",
                                triggered_by: str = "auto") -> Optional[dict]:
    """Drop une carte dans le salon. Retourne dict {event_id, card, message_id}
    ou None si echec."""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        print(f"[card_event] guild {guild_id} introuvable")
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        print(f"[card_event] channel {channel_id} introuvable")
        return None
    card = card_pick_random_by_min_rarity(min_rarity)
    if not card:
        print(f"[card_event] aucune carte eligible min_rarity={min_rarity}")
        return None
    event_id = card_event_log_create(guild_id, channel_id, card["id"],
                                       triggered_by=triggered_by)
    code = _gen_code()
    rarity = card.get("rarity", "common")
    color = RARITY_COLORS.get(rarity, 0x9aa0a6)
    emoji = RARITY_EMOJIS.get(rarity, "⚪")
    embed = discord.Embed(
        title=f"{emoji} {card['name']}",
        description=(f"**Rareté :** {rarity.upper()}\n"
                       f"**Origine :** {card.get('subtitle') or '?'}\n"
                       f"**Univers :** {card.get('universe') or '?'}\n\n"
                       f"⚡ Première personne à taper `{code}` dans ce salon gagne cette carte !"),
        color=color,
    )
    if card.get("image_url"):
        embed.set_image(url=card["image_url"])
    embed.set_footer(text=f"Event #{event_id} · code: {code}")
    try:
        msg = await channel.send(content="🎁 **Drop Event !**", embed=embed)
        card_event_log_update_message(event_id, msg.id, claim_code=code)
        return {"event_id": event_id, "card": card, "message_id": msg.id,
                  "channel_id": channel_id, "claim_code": code}
    except Exception as e:
        print(f"[card_event] erreur send: {e}")
        return None


async def handle_message_claim(bot, message: discord.Message) -> bool:
    """Si message correspond au code d'un event pending dans le salon, claim."""
    if message.author.bot:
        return False
    if not message.guild:
        return False
    content = (message.content or "").strip()
    if not content or len(content) > 32:
        return False
    events = card_event_log_get_pending_in_channel(message.channel.id)
    if not events:
        return False
    matched = None
    for ev in events:
        code = ev.get("claim_code") or ""
        if code and content == code:
            matched = ev
            break
    if not matched:
        return False
    ok = card_event_log_claim(matched["id"], message.author.id)
    if not ok:
        return False
    try:
        user_card_add(message.author.id, matched["card_id"])
    except Exception as e:
        print(f"[card_event claim] add err: {e}")
    # Update embed + react au message gagnant
    try:
        msg_id = matched.get("message_id")
        if msg_id:
            event_msg = await message.channel.fetch_message(int(msg_id))
            if event_msg and event_msg.embeds:
                emb = event_msg.embeds[0]
                emb.description = (emb.description or "") + f"\n\n✅ **Gagnée par {message.author.mention}** !"
                emb.color = 0x4ade80
                await event_msg.edit(embed=emb)
    except Exception as e:
        print(f"[card_event claim update] err: {e}")
    try:
        await message.add_reaction("🎉")
    except Exception:
        pass
    return True


async def check_due_drops(bot) -> int:
    """Verifie tous les configs enabled et drop si next_drop_at <= now."""
    now_iso = _now_iso()
    configs = card_event_config_all_enabled()
    dropped = 0
    for cfg in configs:
        next_at = cfg.get("next_drop_at")
        if not next_at or next_at > now_iso:
            if not next_at:
                _schedule_next_drop(cfg["guild_id"], cfg)
            continue
        result = await trigger_event_drop(
            bot, int(cfg["guild_id"]), int(cfg["channel_id"]),
            min_rarity=cfg.get("min_rarity") or "rare",
            triggered_by="auto",
        )
        if result:
            dropped += 1
        _schedule_next_drop(cfg["guild_id"], cfg)
    return dropped
