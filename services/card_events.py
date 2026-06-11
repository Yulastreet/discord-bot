"""Cards Events : drops aleatoires dans un salon, 1ere reaction wins.

Owner configure par guild : channel + interval (min-max) + min_rarity.
Loop task verifie chaque minute si un drop doit etre lance.
Listener on_raw_reaction_add traite les claims.
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
    card_event_log_get_by_message,
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
# Pool d'emojis pour reaction gagnante (ronds + carrés colorés)
CLAIM_EMOJIS = [
    "🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "🟤", "⚫", "⚪",
    "🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "🟫", "⬛", "⬜",
]
EMOJI_NAMES = {
    "🔴": "rond rouge", "🟠": "rond orange", "🟡": "rond jaune",
    "🟢": "rond vert", "🔵": "rond bleu", "🟣": "rond violet",
    "🟤": "rond marron", "⚫": "rond noir", "⚪": "rond blanc",
    "🟥": "carré rouge", "🟧": "carré orange", "🟨": "carré jaune",
    "🟩": "carré vert", "🟦": "carré bleu", "🟪": "carré violet",
    "🟫": "carré marron", "⬛": "carré noir", "⬜": "carré blanc",
}


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
    # Pick carte
    card = card_pick_random_by_min_rarity(min_rarity)
    if not card:
        print(f"[card_event] aucune carte eligible min_rarity={min_rarity}")
        return None
    # Create log row
    event_id = card_event_log_create(guild_id, channel_id, card["id"],
                                       triggered_by=triggered_by)
    # Pick winning emoji
    winning = random.choice(CLAIM_EMOJIS)
    winning_name = EMOJI_NAMES.get(winning, winning)
    # Build embed (titre = nom carte uniquement, sans "Drop Event !")
    rarity = card.get("rarity", "common")
    color = RARITY_COLORS.get(rarity, 0x9aa0a6)
    emoji = RARITY_EMOJIS.get(rarity, "⚪")
    embed = discord.Embed(
        title=f"{emoji} {card['name']}",
        description=(f"**Rareté :** {rarity.upper()}\n"
                       f"**Origine :** {card.get('subtitle') or '?'}\n"
                       f"**Univers :** {card.get('universe') or '?'}\n\n"
                       f"⚡ Première personne à réagir avec le **{winning_name}** {winning} gagne cette carte !"),
        color=color,
    )
    if card.get("image_url"):
        embed.set_image(url=card["image_url"])
    embed.set_footer(text=f"Event #{event_id}")
    try:
        msg = await channel.send(content="🎁 **Drop Event !**", embed=embed)
        card_event_log_update_message(event_id, msg.id, winning_emoji=winning)
        # Ajoute toutes les reactions claim possibles
        for e in CLAIM_EMOJIS:
            try:
                await msg.add_reaction(e)
            except Exception:
                pass
        return {"event_id": event_id, "card": card, "message_id": msg.id,
                  "channel_id": channel_id, "winning_emoji": winning}
    except Exception as e:
        print(f"[card_event] erreur send: {e}")
        return None


async def handle_reaction_claim(bot, payload: discord.RawReactionActionEvent) -> bool:
    """Si la reaction concerne un event drop pending, claim pour user.
    Retourne True si claim effectue."""
    # Skip bot reactions
    if payload.user_id == bot.user.id:
        return False
    event = card_event_log_get_by_message(payload.message_id)
    if not event:
        return False
    # Verifie que l'emoji utilise est le bon
    winning = event.get("winning_emoji")
    used = str(payload.emoji)
    if winning and used != winning:
        # Mauvaise reaction, retire-la silencieusement
        try:
            channel = bot.get_channel(payload.channel_id)
            if channel:
                msg = await channel.fetch_message(payload.message_id)
                user = bot.get_user(payload.user_id) or await bot.fetch_user(payload.user_id)
                if msg and user:
                    await msg.remove_reaction(payload.emoji, user)
        except Exception:
            pass
        return False
    ok = card_event_log_claim(event["id"], payload.user_id)
    if not ok:
        return False
    # Add carte au user
    try:
        user_card_add(payload.user_id, event["card_id"])
    except Exception as e:
        print(f"[card_event claim] add err: {e}")
    # Update embed pour montrer le claim
    try:
        channel = bot.get_channel(payload.channel_id)
        if channel:
            msg = await channel.fetch_message(payload.message_id)
            if msg and msg.embeds:
                emb = msg.embeds[0]
                user = bot.get_user(payload.user_id) or await bot.fetch_user(payload.user_id)
                emb.description = (emb.description or "") + f"\n\n✅ **Gagnée par {user.mention if user else f'<@{payload.user_id}>'}** !"
                emb.color = 0x4ade80
                await msg.edit(embed=emb)
    except Exception as e:
        print(f"[card_event claim update] err: {e}")
    return True


async def check_due_drops(bot) -> int:
    """Verifie tous les configs enabled et drop si next_drop_at <= now.
    Retourne nb de drops effectues."""
    now_iso = _now_iso()
    configs = card_event_config_all_enabled()
    dropped = 0
    for cfg in configs:
        next_at = cfg.get("next_drop_at")
        if not next_at or next_at > now_iso:
            # Init next_drop_at si jamais set
            if not next_at:
                _schedule_next_drop(cfg["guild_id"], cfg)
            continue
        # Drop !
        result = await trigger_event_drop(
            bot, int(cfg["guild_id"]), int(cfg["channel_id"]),
            min_rarity=cfg.get("min_rarity") or "rare",
            triggered_by="auto",
        )
        if result:
            dropped += 1
        _schedule_next_drop(cfg["guild_id"], cfg)
    return dropped
