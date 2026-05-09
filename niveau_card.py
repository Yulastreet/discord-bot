"""Rendu carte /niveau premium (Pillow).

Compose une image 1024x320 :
- Background choisi par l'utilisateur (assets/niveau_bg/<id>.png)
- Avatar Discord rond
- Pseudo + Niveau + XP total
- Barre de progression XP
- Badge Premium
- Mention discrète "rendu possible par achat intégré"

Renvoie un BytesIO PNG, prêt à être passé à `discord.File`.
"""
from __future__ import annotations

import io
import math
import os
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

CARD_W, CARD_H = 1024, 320
BG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "niveau_bg")
AVATAR_SIZE = 200
AVATAR_X, AVATAR_Y = 60, (CARD_H - AVATAR_SIZE) // 2

ACCENT = (200, 240, 80)            # lime acide TookBot
ACCENT_DARK = (140, 180, 40)
TEXT_PRIMARY = (245, 250, 235)
TEXT_SECONDARY = (200, 215, 180)
TEXT_MUTED = (170, 180, 160)


# ──────────────────────────────────────────────────────────────────────────────
# Fonts
# ──────────────────────────────────────────────────────────────────────────────

_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = [
        # Linux DejaVu
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # Windows
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


# ──────────────────────────────────────────────────────────────────────────────
# Avatar
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_avatar_bytes(url: str) -> Optional[bytes]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        pass
    return None


def _make_round_avatar(raw: bytes, size: int) -> Image.Image:
    avatar = Image.open(io.BytesIO(raw)).convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    # Glow ring
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((2, 2, size - 2, size - 2), outline=ACCENT + (255,), width=4)
    out.alpha_composite(ring)
    return out


def _placeholder_avatar(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (50, 60, 80, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size, size), fill=(60, 70, 90, 255))
    return img


# ──────────────────────────────────────────────────────────────────────────────
# Background
# ──────────────────────────────────────────────────────────────────────────────

def _load_background(bg_id: str) -> Image.Image:
    """Charge le BG demandé, fallback sur 'default' puis sur un gradient noir."""
    candidates = [bg_id, "default"]
    for name in candidates:
        path = os.path.join(BG_DIR, f"{name}.png")
        if os.path.exists(path):
            try:
                img = Image.open(path).convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)
                return img
            except Exception:
                continue
    # Fallback procédural si aucun fichier
    img = Image.new("RGB", (CARD_W, CARD_H), (22, 24, 30))
    return img


# ──────────────────────────────────────────────────────────────────────────────
# Helpers UI
# ──────────────────────────────────────────────────────────────────────────────

def _draw_xp_bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, percent: float):
    percent = max(0.0, min(100.0, float(percent)))
    radius = h // 2
    # Track
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=(20, 22, 30, 220))
    # Inner shadow line (visuel propre)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, outline=(0, 0, 0, 100), width=1)
    # Fill
    fill_w = int(w * percent / 100)
    if fill_w >= 4:
        draw.rounded_rectangle((x, y, x + fill_w, y + h), radius=radius, fill=ACCENT + (255,))
        # Highlight haut
        draw.rounded_rectangle((x + 2, y + 2, x + fill_w - 2, y + h // 2),
                               radius=radius // 2, fill=(255, 255, 255, 60))


def _draw_premium_badge(draw: ImageDraw.ImageDraw, x: int, y: int):
    f = _font(18, bold=True)
    text = "★ PREMIUM"
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 14, 8
    draw.rounded_rectangle(
        (x, y, x + tw + pad_x * 2, y + th + pad_y * 2),
        radius=14, fill=ACCENT + (255,),
    )
    draw.text((x + pad_x, y + pad_y - 2), text, font=f, fill=(20, 30, 8, 255))


def _shadow_layer(text_layer: Image.Image, blur: int = 4) -> Image.Image:
    return text_layer.filter(ImageFilter.GaussianBlur(blur))


# ──────────────────────────────────────────────────────────────────────────────
# Render principal
# ──────────────────────────────────────────────────────────────────────────────

async def render_niveau_card(
    *,
    username: str,
    avatar_url: Optional[str],
    level: int,
    xp_total: int,
    xp_in_level: int,
    xp_needed: int,
    background: str = "default",
    rank: Optional[int] = None,
) -> io.BytesIO:
    """Génère la carte et retourne un BytesIO PNG."""
    base = _load_background(background).convert("RGBA")

    # Voile foncé sous le texte (gauche zone avatar->droite)
    veil = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    vd.rectangle((0, 0, CARD_W, CARD_H), fill=(0, 0, 0, 75))
    base.alpha_composite(veil)

    draw = ImageDraw.Draw(base)

    # ── Avatar ───────────────────────────────────────────────────────────
    avatar_img: Optional[Image.Image] = None
    if avatar_url:
        raw = await _fetch_avatar_bytes(avatar_url)
        if raw:
            try:
                avatar_img = _make_round_avatar(raw, AVATAR_SIZE)
            except Exception:
                avatar_img = None
    if avatar_img is None:
        avatar_img = _placeholder_avatar(AVATAR_SIZE)
    base.alpha_composite(avatar_img, (AVATAR_X, AVATAR_Y))

    # ── Bloc texte droite ────────────────────────────────────────────────
    text_x = AVATAR_X + AVATAR_SIZE + 40
    # Pseudo
    name_font = _font(46, bold=True)
    # Truncate si nécessaire
    display = username
    while draw.textlength(display, font=name_font) > CARD_W - text_x - 40 and len(display) > 1:
        display = display[:-1]
    if display != username:
        display = display[:-1] + "…"
    draw.text((text_x, 38), display, font=name_font, fill=TEXT_PRIMARY)

    # Niveau / XP
    f_label = _font(20, bold=True)
    f_value = _font(28, bold=True)
    sub_y = 100
    # Niveau
    draw.text((text_x, sub_y), "NIVEAU", font=f_label, fill=ACCENT)
    draw.text((text_x, sub_y + 26), str(level), font=f_value, fill=TEXT_PRIMARY)
    # XP total
    xp_x = text_x + 160
    draw.text((xp_x, sub_y), "XP TOTAL", font=f_label, fill=ACCENT)
    draw.text((xp_x, sub_y + 26), f"{xp_total:,}".replace(",", " "), font=f_value, fill=TEXT_PRIMARY)
    # Rang (optionnel)
    if rank:
        rk_x = xp_x + 200
        draw.text((rk_x, sub_y), "RANG", font=f_label, fill=ACCENT)
        draw.text((rk_x, sub_y + 26), f"#{rank}", font=f_value, fill=TEXT_PRIMARY)

    # ── Barre XP ─────────────────────────────────────────────────────────
    bar_x = text_x
    bar_y = 200
    bar_w = CARD_W - bar_x - 60
    bar_h = 26
    pct = (xp_in_level / xp_needed * 100) if xp_needed > 0 else 0
    _draw_xp_bar(draw, bar_x, bar_y, bar_w, bar_h, pct)
    # Texte sous la barre
    f_xp = _font(18, bold=False)
    xp_text = f"{xp_in_level:,} / {xp_needed:,} XP".replace(",", " ")
    draw.text((bar_x, bar_y + bar_h + 8), xp_text, font=f_xp, fill=TEXT_SECONDARY)
    pct_text = f"{pct:.0f}%"
    pct_w = draw.textlength(pct_text, font=f_xp)
    draw.text((bar_x + bar_w - pct_w, bar_y + bar_h + 8), pct_text, font=f_xp, fill=TEXT_SECONDARY)

    # ── Badge premium ────────────────────────────────────────────────────
    _draw_premium_badge(draw, x=CARD_W - 180, y=24)

    # ── Mention discrète ────────────────────────────────────────────────
    f_mention = _font(11, bold=False)
    mention = "Rendu possible grâce à un achat intégré"
    draw.text((CARD_W - 16, CARD_H - 18), mention, font=f_mention,
              fill=(200, 215, 180, 130), anchor="rb")

    # Output
    buf = io.BytesIO()
    base.convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf


def list_available_backgrounds() -> list[str]:
    """Retourne la liste des IDs de backgrounds disponibles (sans extension)."""
    if not os.path.isdir(BG_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(BG_DIR)):
        if fn.lower().endswith(".png"):
            out.append(os.path.splitext(fn)[0])
    return out
