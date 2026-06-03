"""Rendu d'une image style 'tweet X' a partir d'un message Discord.

Compose une image rectangulaire :
- Header : avatar rond + display_name + @username + verified tick
- Corps : texte du message entre guillemets, font serif elegante
- Footer : timestamp formate (16:42 · 4 juin 2026 · X)

Retourne BytesIO PNG pour discord.File.
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont


# Dimensions du tweet card. Largeur fixe, hauteur calculee dynamiquement
# selon la longueur du texte (max 8 lignes wrap, sinon truncate).
TW_W = 800
TW_PAD = 32
TW_AVATAR = 80
BG = (21, 32, 43)               # Twitter dark mode bg
TEXT = (231, 233, 234)
TEXT_MUTED = (113, 118, 123)
ACCENT = (29, 155, 240)         # Twitter blue
VERIFIED = (29, 155, 240)


_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _FONT_CACHE[key] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


_HTTP_SESSION: Optional[aiohttp.ClientSession] = None
_HTTP_LOCK = asyncio.Lock()


async def _http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION and not _HTTP_SESSION.closed:
        return _HTTP_SESSION
    async with _HTTP_LOCK:
        if _HTTP_SESSION and not _HTTP_SESSION.closed:
            return _HTTP_SESSION
        timeout = aiohttp.ClientTimeout(total=8, connect=4)
        _HTTP_SESSION = aiohttp.ClientSession(timeout=timeout)
        return _HTTP_SESSION


async def _fetch_bytes(url: str) -> Optional[bytes]:
    if not url:
        return None
    try:
        sess = await _http_session()
        async with sess.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


def _round_avatar(raw: bytes, size: int) -> Image.Image:
    img = Image.open(io.BytesIO(raw)).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _placeholder_avatar(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (60, 70, 90, 255))
    d = ImageDraw.Draw(img)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Wrap mot par mot en respectant la largeur max."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = w if not cur else f"{cur} {w}"
        if draw.textlength(candidate, font=font) <= max_w:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            # Le mot lui-meme est plus large : split brutal
            if draw.textlength(w, font=font) > max_w:
                tmp = ""
                for ch in w:
                    if draw.textlength(tmp + ch, font=font) > max_w:
                        lines.append(tmp); tmp = ch
                    else:
                        tmp += ch
                cur = tmp
            else:
                cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_verified_tick(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int = 9):
    """Petit badge bleu rond avec check blanc."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=VERIFIED)
    # Check stylise
    pts = [(cx - r * 0.45, cy + r * 0.05),
           (cx - r * 0.10, cy + r * 0.40),
           (cx + r * 0.45, cy - r * 0.30)]
    draw.line(pts, fill=(255, 255, 255), width=2)


async def render_tweet_card(
    *,
    display_name: str,
    username: str,
    avatar_url: Optional[str],
    text: str,
    timestamp_str: str,
    verified: bool = True,
) -> io.BytesIO:
    """Rend le tweet card -> BytesIO PNG.

    `timestamp_str` : deja formate (ex '16:42 · 4 juin 2026').
    """
    raw_avatar = await _fetch_bytes(avatar_url) if avatar_url else None
    return await asyncio.to_thread(
        _render_tweet_sync,
        display_name, username, raw_avatar, text, timestamp_str, verified,
    )


def _render_tweet_sync(display_name, username, raw_avatar, text,
                       timestamp_str, verified) -> io.BytesIO:
    # Pre-calcul des wraps pour determiner la hauteur
    f_name = _font(22, bold=True)
    f_user = _font(18, bold=False)
    f_body = _font(26, bold=False)
    f_meta = _font(15, bold=False)

    # Crepe temp draw pour calculs textlength
    tmp = Image.new("RGB", (TW_W, 400), BG)
    td = ImageDraw.Draw(tmp)

    body_text = f'"{text}"' if text else '" "'
    body_lines = _wrap_text(body_text, f_body, TW_W - TW_PAD * 2, td)
    # Limite a 10 lignes (truncate)
    if len(body_lines) > 10:
        body_lines = body_lines[:10]
        body_lines[-1] = body_lines[-1].rstrip() + "..."

    line_h = 36
    header_h = TW_PAD + TW_AVATAR + 20
    body_h = len(body_lines) * line_h + 20
    footer_h = 32 + TW_PAD
    total_h = header_h + body_h + footer_h

    base = Image.new("RGB", (TW_W, total_h), BG)
    draw = ImageDraw.Draw(base)

    # === Header : avatar + name/user ===
    avatar_img = None
    if raw_avatar:
        try:
            avatar_img = _round_avatar(raw_avatar, TW_AVATAR)
        except Exception:
            avatar_img = None
    if avatar_img is None:
        avatar_img = _placeholder_avatar(TW_AVATAR)
    base.paste(avatar_img, (TW_PAD, TW_PAD), avatar_img)

    name_x = TW_PAD + TW_AVATAR + 16
    # Display name
    draw.text((name_x, TW_PAD + 8), display_name, font=f_name, fill=TEXT)
    name_w = draw.textlength(display_name, font=f_name)
    if verified:
        _draw_verified_tick(draw, name_x + int(name_w) + 14, TW_PAD + 8 + 14, r=10)

    # @username
    draw.text((name_x, TW_PAD + 8 + 28),
              f"@{username}", font=f_user, fill=TEXT_MUTED)

    # === Body ===
    body_y = header_h + 10
    for line in body_lines:
        draw.text((TW_PAD, body_y), line, font=f_body, fill=TEXT)
        body_y += line_h

    # === Footer : timestamp + source ===
    footer_y = total_h - TW_PAD - 4
    draw.text((TW_PAD, footer_y - 18),
              f"{timestamp_str} · TookBot",
              font=f_meta, fill=TEXT_MUTED)

    # Mini logo X (oiseau / X) en haut a droite
    x_size = 24
    x_x = TW_W - TW_PAD - x_size
    x_y = TW_PAD + 6
    f_x = _font(28, bold=True)
    draw.text((x_x, x_y), "𝕏", font=f_x, fill=TEXT)

    buf = io.BytesIO()
    base.save(buf, "PNG", optimize=False, compress_level=6)
    buf.seek(0)
    return buf
