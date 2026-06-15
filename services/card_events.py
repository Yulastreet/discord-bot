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


def global_event_config() -> dict:
    """Config GLOBALE des drops (s'applique a tous les serveurs feature ON)."""
    from database import get_setting
    try:
        mn = int(get_setting("card_event_interval_min", "300") or 300)
    except (ValueError, TypeError):
        mn = 300
    try:
        mx = int(get_setting("card_event_interval_max", "600") or 600)
    except (ValueError, TypeError):
        mx = 600
    rar = (get_setting("card_event_min_rarity", "rare") or "rare").strip().lower()
    if rar not in ("common", "rare", "epic", "legendary", "mythic"):
        rar = "rare"
    return {"min_interval_min": max(1, mn), "max_interval_min": max(1, mx),
            "min_rarity": rar,
            "enabled": (get_setting("card_event_enabled", "1") or "1") == "1"}


def _schedule_next_drop(guild_id: str, gcfg: dict | None = None) -> str:
    """Calcule prochain drop dans intervalle global random [min, max]. UTC ISO."""
    gcfg = gcfg or global_event_config()
    mn = max(1, int(gcfg.get("min_interval_min") or 300))
    mx = max(mn, int(gcfg.get("max_interval_min") or 600))
    delay = random.randint(mn, mx)
    next_at = _dt.datetime.utcnow() + _dt.timedelta(minutes=delay)
    next_iso = next_at.strftime("%Y-%m-%d %H:%M:%S")
    card_event_config_set(guild_id, next_drop_at=next_iso)
    return next_iso


def _resolve_drop_channel(guild):
    """Salon de drop d'un serveur, par ordre de priorite :
    1. salon /cardsetup (guild_card_config) si configure et writable
    2. salon override card_event_config si defini
    3. system_channel
    4. 1er salon texte ou le bot peut ecrire (type general)."""
    from database import card_event_config_get, guild_card_config_get
    me = guild.me

    def _try(cid):
        if not cid:
            return None
        ch = guild.get_channel(int(cid)) if str(cid).isdigit() else None
        if ch and me and ch.permissions_for(me).send_messages:
            return ch
        return None

    # 1. salon configure via /cardsetup
    try:
        cards_cfg = guild_card_config_get(guild.id) or {}
        ch = _try(cards_cfg.get("channel_id"))
        if ch:
            return ch
    except Exception:
        pass
    # 2. override event config
    cfg = card_event_config_get(guild.id) or {}
    ch = _try(cfg.get("channel_id"))
    if ch:
        return ch
    # 3. system channel
    if guild.system_channel and me and guild.system_channel.permissions_for(me).send_messages:
        return guild.system_channel
    # 4. premier salon texte writable
    for ch in guild.text_channels:
        try:
            if me and ch.permissions_for(me).send_messages and ch.permissions_for(me).view_channel:
                return ch
        except Exception:
            continue
    return None


async def trigger_event_drop(bot, guild_id: int, channel_id: int,
                                min_rarity: str = "rare",
                                exact_rarity: bool = False,
                                triggered_by: str = "auto") -> Optional[dict]:
    """Drop une carte dans le salon. Retourne dict {event_id, card, message_id}
    ou None si echec. exact_rarity=True -> carte de cette rareté EXACTE (sinon min)."""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        print(f"[card_event] guild {guild_id} introuvable")
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        print(f"[card_event] channel {channel_id} introuvable")
        return None
    if exact_rarity:
        from database import card_pick_random_exact_rarity
        card = card_pick_random_exact_rarity(min_rarity)
    else:
        card = card_pick_random_by_min_rarity(min_rarity)
    if not card:
        print(f"[card_event] aucune carte eligible rarity={min_rarity} exact={exact_rarity}")
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
    # Update embed : remplace l'image captcha par la carte propre (sans code) + react
    try:
        msg_id = matched.get("message_id")
        if msg_id:
            event_msg = await message.channel.fetch_message(int(msg_id))
            if event_msg and event_msg.embeds:
                emb = event_msg.embeds[0]
                emb.description = (emb.description or "") + f"\n\n✅ **Gagnée par {message.author.mention}** !"
                emb.color = 0x4ade80
                # Image propre = render local (webp/png) ou domaine public, SANS bande
                # captcha. attachments=[] / [file] vire l'ancienne image (le code).
                from database import card_get
                from commandes.cards import _resolve_card_image
                url, clean_file = _resolve_card_image(card_get(matched["card_id"]) or {})
                if url:
                    emb.set_image(url=url)
                    await event_msg.edit(embed=emb, attachments=[])
                elif clean_file:
                    emb.set_image(url="attachment://card.png")
                    await event_msg.edit(embed=emb, attachments=[clean_file])
                else:
                    await event_msg.edit(embed=emb, attachments=[])
    except Exception as e:
        print(f"[card_event claim update] err: {e}")
    try:
        await message.add_reaction("🎉")
    except Exception:
        pass
    return True


async def check_due_drops(bot) -> int:
    """Config GLOBALE : pour chaque serveur ayant la feature activee, drop selon
    l'intervalle + rareté globaux. Salon auto-resolu. Timer next_drop_at par serveur."""
    from database import guild_setting_get, card_event_config_get
    gcfg = global_event_config()
    if not gcfg.get("enabled"):
        return 0
    now_iso = _now_iso()
    dropped = 0
    for guild in list(bot.guilds):
        if guild_setting_get(str(guild.id), "card_events", "0") != "1":
            continue
        cfg = card_event_config_get(guild.id) or {}
        next_at = cfg.get("next_drop_at")
        if not next_at:
            _schedule_next_drop(guild.id, gcfg)
            continue
        if next_at > now_iso:
            continue
        channel = _resolve_drop_channel(guild)
        if channel:
            result = await trigger_event_drop(
                bot, int(guild.id), int(channel.id),
                min_rarity=gcfg["min_rarity"], triggered_by="auto")
            if result:
                dropped += 1
        _schedule_next_drop(guild.id, gcfg)
    return dropped
