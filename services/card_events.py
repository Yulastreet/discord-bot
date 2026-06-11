"""Cards Events : drops aleatoires dans un salon, captcha texte -> 1er a taper code gagne.

Owner configure par guild : channel + interval (min-max) + min_rarity.
Loop task verifie chaque minute si un drop doit etre lance.
Listener on_message verifie si message correspond a un code event pending.
"""
from __future__ import annotations

import datetime as _dt
import math
import os
import random
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFont

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


_CE_FONT_CACHE: dict = {}


def _ce_font(size: int):
    if size in _CE_FONT_CACHE:
        return _CE_FONT_CACHE[size]
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _CE_FONT_CACHE[size] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _CE_FONT_CACHE[size] = f
    return f


def _render_drop_image(bot, card: dict, code: str, event_id: int) -> Optional[str]:
    """Compose carte + bande captcha (code en image, non copiable). Retourne path local."""
    try:
        from services.card_render import _load_base, _ROOT
        cid = card["id"]
        base = _load_base(int(cid), fallback_url=card.get("image_url"))
        if base is None:
            return None
        base = base.convert("RGBA")
        W, H = base.size
        draw = ImageDraw.Draw(base)
        # Bande sombre translucide en bas
        band_h = int(H * 0.20)
        band = Image.new("RGBA", (W, band_h), (0, 0, 0, 165))
        base.alpha_composite(band, (0, H - band_h))
        # Bruit : quelques lignes
        for _ in range(6):
            x1, y1 = random.randint(0, W), H - band_h + random.randint(0, band_h)
            x2, y2 = random.randint(0, W), H - band_h + random.randint(0, band_h)
            draw.line((x1, y1, x2, y2), fill=(255, 255, 255, 60), width=2)
        # Code : chaque char tourne/jitter, couleur claire
        font = _ce_font(int(band_h * 0.62))
        n = len(code)
        # largeur approx pour centrer
        char_w = int(W * 0.78 / max(1, n))
        total_w = char_w * n
        x0 = (W - total_w) // 2
        cy = H - band_h // 2
        for i, ch in enumerate(code):
            ang = random.uniform(-22, 22)
            col = random.choice([(255, 240, 170), (180, 242, 58), (120, 200, 255),
                                  (255, 200, 120), (255, 255, 255)])
            ci = Image.new("RGBA", (char_w + 20, band_h), (0, 0, 0, 0))
            cd = ImageDraw.Draw(ci)
            cd.text((10, int(band_h * 0.12)), ch, font=font, fill=col)
            ci = ci.rotate(ang, expand=True, resample=Image.BICUBIC)
            px = x0 + i * char_w - 10 + random.randint(-4, 4)
            py = cy - ci.height // 2 + random.randint(-6, 6)
            base.alpha_composite(ci, (max(0, px), max(H - band_h, py)))
        out_dir = os.path.join(_ROOT, "static", "card_events")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{event_id}.png")
        base.convert("RGB").save(out_path, "PNG", optimize=True)
        return out_path
    except Exception as e:
        print(f"[card_event] render drop image err: {e}")
        return None


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
                       f"⚡ Première personne à **taper le code affiché sur l'image** "
                       f"dans ce salon gagne cette carte !"),
        color=color,
    )
    # Image = carte + code captcha (non copiable). Fallback : image carte brute.
    drop_img_path = _render_drop_image(bot, card, code, event_id)
    drop_file = None
    if drop_img_path:
        drop_file = discord.File(drop_img_path, filename="drop.png")
        embed.set_image(url="attachment://drop.png")
    elif card.get("image_url"):
        embed.set_image(url=card["image_url"])
    # Badge animé rareté en thumbnail (emoji custom du support server)
    try:
        from commandes.cards import _get_rarity_custom_emoji_url
        badge_url = _get_rarity_custom_emoji_url(bot, rarity)
        if badge_url:
            embed.set_thumbnail(url=badge_url)
    except Exception:
        pass
    embed.set_footer(text=f"Event #{event_id}")
    try:
        if drop_file:
            msg = await channel.send(content="🎁 **Drop Event !**", embed=embed, file=drop_file)
        else:
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
    from database import guild_setting_get
    for cfg in configs:
        # Respecte le toggle feature (opt-in par serveur)
        if guild_setting_get(str(cfg["guild_id"]), "card_events", "0") != "1":
            continue
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
