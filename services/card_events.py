"""Cards Events : drops aleatoires dans un salon, captcha texte -> 1er a taper code gagne.

Owner configure par guild : channel + interval (min-max) + min_rarity.
Loop task verifie chaque minute si un drop doit etre lance.
Listener on_message verifie si message correspond a un code event pending.
"""
from __future__ import annotations

import datetime as _dt
import os
import random
from typing import Optional

import discord

from services.i18n import t, guild_locale
from services.ui_v2 import Panel
from PIL import Image, ImageDraw, ImageFont

from database import (
    card_event_config_set,
    card_event_log_create,
    card_event_log_get_pending_in_channel,
    card_event_log_update_message,
    card_event_log_claim,
    card_pick_random_by_min_rarity,
    user_card_add,
)


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
        from services.card_render import _load_base, _ROOT, _CARD_W, _CARD_H
        cid = card["id"]
        base = _load_base(int(cid), fallback_url=card.get("image_url"))
        # _load_base ne gere que le fallback http : tente aussi un /static/ local
        if base is None:
            img = card.get("image_url") or ""
            if isinstance(img, str) and "/static/" in img:
                rel = "/static/" + img.split("/static/", 1)[1].split("?")[0]
                p = os.path.join(_ROOT, rel.lstrip("/").replace("/", os.sep))
                if os.path.exists(p):
                    try:
                        base = Image.open(p).convert("RGBA")
                    except Exception:
                        base = None
        # Dernier recours : canvas plein (le CODE doit TOUJOURS apparaitre -> claimable)
        if base is None:
            print(f"[card_event] pas d'image base carte {cid} -> canvas de secours")
            base = Image.new("RGBA", (_CARD_W, _CARD_H), (28, 28, 36, 255))
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


def _drop_panel(bot, card, event_id, *, image_url=None, loc="en",
                ping=None, extra=None):
    """Components V2 panel of a drop (replaces the former embed).

    `ping` = raw role mention, rendered above the title (a V2 message has no
    `content`). `extra` = line appended afterwards (winner / troll).
    """
    rarity = card.get("rarity", "common")
    emoji = RARITY_EMOJIS.get(rarity, "⚪")
    header = "🎁 **Drop Event !**"
    p = Panel()
    p.text(f"{ping} {header}" if ping else header)
    p.text(f"## {emoji} {card.get('name', '?')}")
    p.text(t("services.card_event.drop_description", loc,
             rarity=str(rarity).upper(),
             origin=card.get('subtitle') or '?',
             universe=card.get('universe') or '?'))
    if extra:
        p.text(extra)
    # Animated rarity badge as thumbnail (custom emoji from the support server)
    try:
        from commandes.cards import _get_rarity_custom_emoji_url
        badge_url = _get_rarity_custom_emoji_url(bot, rarity)
        if badge_url:
            p.thumbnail(badge_url)
    except Exception:
        pass
    if image_url:
        p.image(image_url)
    p.footer(f"Event #{event_id}")
    return p


async def trigger_event_drop(bot, guild_id: int, channel_id: int,
                                min_rarity: str = "rare",
                                exact_rarity: bool = False,
                                card_id: int = None,
                                triggered_by: str = "auto") -> Optional[dict]:
    """Drop a card in the channel. Returns dict {event_id, card, message_id}
    ou None si echec. card_id -> carte precise (ignore rarete). exact_rarity=True
    -> card of that EXACT rarity (otherwise min)."""
    _loc = guild_locale(guild_id) or "en"
    guild = bot.get_guild(int(guild_id))
    if not guild:
        print(f"[card_event] guild {guild_id} introuvable")
        return None
    channel = guild.get_channel(int(channel_id))
    if not channel:
        print(f"[card_event] channel {channel_id} introuvable")
        return None
    if card_id:
        from database import card_get
        card = card_get(int(card_id))
    elif exact_rarity:
        from database import card_pick_random_exact_rarity
        card = card_pick_random_exact_rarity(min_rarity)
    else:
        card = card_pick_random_by_min_rarity(min_rarity)
    if not card:
        print(f"[card_event] aucune carte eligible rarity={min_rarity} exact={exact_rarity} card_id={card_id}")
        return None
    # Code genere et ENREGISTRE des la creation (avant le send) : le claim matche
    # par code, donc meme si l'update du message_id echoue plus tard, c'est claimable.
    code = _gen_code()
    event_id = card_event_log_create(guild_id, channel_id, card["id"],
                                       triggered_by=triggered_by, claim_code=code)
    # Image = card + captcha code (not copyable). Fallback: raw card image.
    drop_img_path = _render_drop_image(bot, card, code, event_id)
    drop_file = None
    img_url = None
    if drop_img_path:
        drop_file = discord.File(drop_img_path, filename="drop.png")
        img_url = "attachment://drop.png"
    elif card.get("image_url"):
        img_url = card["image_url"]
    # Ping du role "fans de cartes" si configure (/cardsetup role)
    from database import guild_card_config_get
    _cfg = guild_card_config_get(guild.id) or {}
    _role_id = _cfg.get("ping_role_id")
    # V2: the message carries no `content` any more, the ping becomes a text
    # block of the panel. The send keeps its allowed_mentions so the role is
    # really pinged.
    ping = f"<@&{_role_id}>" if _role_id else None
    allowed = (discord.AllowedMentions(roles=True) if _role_id
               else discord.AllowedMentions.none())
    view = _drop_panel(bot, card, event_id, image_url=img_url,
                       loc=_loc, ping=ping).view()
    try:
        if drop_file:
            msg = await channel.send(view=view, file=drop_file, allowed_mentions=allowed)
        else:
            msg = await channel.send(view=view, allowed_mentions=allowed)
    except Exception as e:
        # Envoi rate : on retire le ghost (sinon event sans message). Le retry du
        # dispatch repartira proprement.
        print(f"[card_event] erreur send: {e}")
        try:
            from database import card_event_log_delete
            card_event_log_delete(event_id)
        except Exception:
            pass
        return None
    # Message envoye + code deja en base -> claimable. msg_id best-effort (non-fatal).
    try:
        card_event_log_update_message(event_id, msg.id)
    except Exception as e:
        print(f"[card_event] update message_id err (claim reste OK via code): {e}")
    return {"event_id": event_id, "card": card, "message_id": msg.id,
              "channel_id": channel_id, "claim_code": code}


async def handle_message_claim(bot, message: discord.Message) -> bool:
    """If the message matches the code of a pending event in the channel, claim it."""
    if message.author.bot:
        return False
    if not message.guild:
        return False
    _loc = guild_locale(message.guild.id) or "en"
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
    # === FAUX DROP (troll) : aucune carte donnee, le bot se moque ===
    if (matched.get("triggered_by") or "") == "fake_troll":
        from database import card_get
        cname = (card_get(matched["card_id"]) or {}).get("name", "cette carte")
        try:
            msg_id = matched.get("message_id")
            if msg_id:
                event_msg = await message.channel.fetch_message(int(msg_id))
                if event_msg:
                    tcard = card_get(matched["card_id"]) or {}
                    timg = ("attachment://drop.png" if event_msg.attachments
                            else (tcard.get("image_url") or None))
                    tp = _drop_panel(bot, tcard, matched["id"], image_url=timg,
                                     loc=_loc,
                                     extra=f"🤡 **{message.author.mention} s'est fait troll !**")
                    # V2: a panel text block really pings -> mentions cut off.
                    await event_msg.edit(view=tp.view(),
                                         allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            print(f"[fake drop] edit err: {e}")
        troll = random.choice([
            t(f"services.card_event.troll_{i}", _loc,
              user=message.author.display_name, card=cname)
            for i in (1, 2, 3)
        ])
        try:
            await message.channel.send(troll)
        except Exception:
            pass
        try:
            await message.add_reaction("🤡")
        except Exception:
            pass
        return True
    try:
        user_card_add(message.author.id, matched["card_id"])
    except Exception as e:
        print(f"[card_event claim] add err: {e}")
    # Update embed: replace the captcha image with the clean card (no code) + react
    try:
        msg_id = matched.get("message_id")
        if msg_id:
            event_msg = await message.channel.fetch_message(int(msg_id))
            if event_msg:
                # Clean image = local render (webp/png) or public domain, WITHOUT the
                # captcha strip. attachments=[] / [file] drops the old image (the code).
                from database import card_get
                from commandes.cards import _resolve_card_image
                wcard = card_get(matched["card_id"]) or {}
                url, clean_file = _resolve_card_image(wcard)
                won = t("services.card_event.won_by", _loc, user=message.author.mention)
                atts = [clean_file] if (not url and clean_file) else []
                wimg = url or ("attachment://card.png" if clean_file else None)
                wp = _drop_panel(bot, wcard, matched["id"], image_url=wimg,
                                 loc=_loc, extra=won)
                # V2: a panel text block really pings -> mentions cut off.
                await event_msg.edit(view=wp.view(), attachments=atts,
                                     allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        print(f"[card_event claim update] err: {e}")
    try:
        await message.add_reaction("🎉")
    except Exception:
        pass
    return True


async def check_due_drops(bot) -> int:
    """Config GLOBALE : pour chaque serveur ayant la feature activee, drop selon
    the global interval + rarity. Channel auto-resolved. Per-guild next_drop_at timer."""
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
