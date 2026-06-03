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

import asyncio
import io
import math
import os
import re
import time
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# pilmoji rend les emojis couleur (Twemoji) sur les images Pillow.
# Fallback gracieux si la lib n'est pas dispo.
try:
    from pilmoji import Pilmoji
    _HAS_PILMOJI = True
except Exception:
    _HAS_PILMOJI = False
    Pilmoji = None  # type: ignore


CARD_W, CARD_H = 1024, 320
# Module deplace dans cards/, donc on remonte d'un niveau pour pointer assets/
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG_DIR = os.path.join(_REPO_ROOT, "assets", "niveau_bg")
# BG custom owner : 1 par owner_id (perso, jamais expose aux autres).
BG_OWNER_DIR = os.path.join(BG_DIR, "owner")
os.makedirs(BG_OWNER_DIR, exist_ok=True)
AVATAR_SIZE = 200
AVATAR_X, AVATAR_Y = 60, (CARD_H - AVATAR_SIZE) // 2

ACCENT = (200, 240, 80)            # lime acide TookBot
ACCENT_DARK = (140, 180, 40)
TEXT_PRIMARY = (245, 250, 235)
TEXT_SECONDARY = (200, 215, 180)
TEXT_MUTED = (170, 180, 160)


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


# Session HTTP partagee (TLS reuse + connection pool).
_HTTP_SESSION: Optional[aiohttp.ClientSession] = None
_HTTP_SESSION_LOCK = asyncio.Lock()

# Cache avatar bytes : { url: (expires_ts, bytes) }. TTL court car les avatars
# Discord changent peu mais on veut suivre les renames; 10 min suffisent.
_AVATAR_CACHE: dict[str, tuple[float, bytes]] = {}
_AVATAR_TTL = 600  # secondes
_AVATAR_CACHE_MAX = 256


async def _get_http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION and not _HTTP_SESSION.closed:
        return _HTTP_SESSION
    async with _HTTP_SESSION_LOCK:
        if _HTTP_SESSION and not _HTTP_SESSION.closed:
            return _HTTP_SESSION
        timeout = aiohttp.ClientTimeout(total=8, connect=4)
        _HTTP_SESSION = aiohttp.ClientSession(timeout=timeout)
        return _HTTP_SESSION


async def _fetch_avatar_bytes(url: str) -> Optional[bytes]:
    if not url:
        return None
    now = time.monotonic()
    cached = _AVATAR_CACHE.get(url)
    if cached and cached[0] > now:
        return cached[1]
    try:
        session = await _get_http_session()
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                if len(_AVATAR_CACHE) >= _AVATAR_CACHE_MAX:
                    # purge oldest
                    oldest = min(_AVATAR_CACHE, key=lambda k: _AVATAR_CACHE[k][0])
                    _AVATAR_CACHE.pop(oldest, None)
                _AVATAR_CACHE[url] = (now + _AVATAR_TTL, data)
                return data
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
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size, size), fill=(60, 70, 90, 255))
    d.ellipse((2, 2, size - 2, size - 2), outline=ACCENT + (255,), width=4)
    return img


# Cache backgrounds en RAM : { id: (mtime_disk, Image RGB) }.
# La cle inclut le mtime du fichier source ; quand le fichier est modifie
# (upload owner, regeneration saisonniere) on invalide automatiquement, ce
# qui rend le cache transparent entre les processus pm2 (bot et web).
_BG_CACHE: dict[str, tuple[float, Image.Image]] = {}


def _resolve_bg_path(bg_id: str) -> Optional[str]:
    """Resout l'ID du BG vers un chemin disque.

    Supporte :
    - 'owner:<owner_id>' -> assets/niveau_bg/owner/<owner_id>.png  (owner uniquement)
    - 'seasonal:<YYYY-MM>:<name>' -> assets/niveau_bg/seasonal/<YYYY-MM>/<name>.png
    - '<name>' simple -> assets/niveau_bg/<name>.png  (BG permanent)
    """
    if not bg_id:
        return None
    if bg_id.startswith("owner:"):
        owner_id = bg_id.split(":", 1)[1]
        path = os.path.join(BG_OWNER_DIR, f"{owner_id}.png")
        return path if os.path.exists(path) else None
    if bg_id.startswith("seasonal:"):
        parts = bg_id.split(":", 2)
        if len(parts) == 3:
            mk, name = parts[1], parts[2]
            path = os.path.join(BG_DIR, "seasonal", mk, f"{name}.png")
            return path if os.path.exists(path) else None
        return None
    path = os.path.join(BG_DIR, f"{bg_id}.png")
    return path if os.path.exists(path) else None


def _load_background(bg_id: str) -> Image.Image:
    """Charge le BG demandé (cache RAM mtime-aware), fallback default puis gradient.

    Le cache stocke (mtime_disk, Image). Si le fichier sur disque a un mtime
    plus recent que celui en cache, on recharge automatiquement — utile pour
    propager un upload owner BG ou la regeneration des BG saisonniers entre
    les processus pm2 (bot + web ont chacun leur cache).

    Renvoie une COPIE pour que les operations suivantes (alpha_composite, draw)
    ne mutent pas l'image cachee.
    """
    candidates = [bg_id, "default"]
    for name in candidates:
        path = _resolve_bg_path(name)
        if path:
            try:
                disk_mtime = os.path.getmtime(path)
                cached = _BG_CACHE.get(name)
                if cached and cached[0] >= disk_mtime:
                    return cached[1].copy()
                img = Image.open(path).convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)
                _BG_CACHE[name] = (disk_mtime, img)
                return img.copy()
            except Exception:
                continue
    # Fallback procédural si aucun fichier
    return Image.new("RGB", (CARD_W, CARD_H), (22, 24, 30))


def invalidate_bg_cache(bg_id: str = None):
    """A appeler apres upload/replacement d'un BG pour purger le cache RAM."""
    if bg_id is None:
        _BG_CACHE.clear()
    else:
        _BG_CACHE.pop(bg_id, None)


def preload_backgrounds():
    """A appeler au boot pour eviter la 1ere latence de decode disque."""
    if not os.path.isdir(BG_DIR):
        return
    for fn in os.listdir(BG_DIR):
        if not fn.lower().endswith(".png"):
            continue
        bg_id = os.path.splitext(fn)[0]
        if bg_id in _BG_CACHE:
            continue
        path = os.path.join(BG_DIR, fn)
        try:
            mtime = os.path.getmtime(path)
            img = Image.open(path).convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)
            _BG_CACHE[bg_id] = (mtime, img)
        except Exception:
            pass


_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"  # drapeaux regionaux
    "\U0001F300-\U0001FAFF"  # symboles & pictogrammes etendus
    "\U00002600-\U000027BF"  # divers symboles + dingbats
    "\U0001F000-\U0001F0FF"  # mahjong/dominos/cartes
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U00002190-\U000021FF"  # fleches
    "\U00002B00-\U00002BFF"  # fleches & etoiles diverses
    "\U0000200D"             # zero width joiner
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    """Retire les emojis unicode et nettoie les espaces restants (fallback sans pilmoji)."""
    return _EMOJI_RE.sub("", text).strip()


def _draw_text_emoji(image: Image.Image, xy, text: str, font, fill,
                      emoji_scale: float = 1.0):
    """Dessine du texte contenant potentiellement des emojis couleur.

    Utilise pilmoji si dispo (rendu Twemoji propre). Sinon fallback Pillow
    qui retire les emojis (eviter le carre tofu de la police par defaut).
    `emoji_scale` ajuste la taille des glyphes emoji (alignement vertical).
    """
    if _HAS_PILMOJI:
        try:
            with Pilmoji(image) as pj:
                pj.text(xy, text, font=font, fill=fill, emoji_scale_factor=emoji_scale)
            return
        except Exception:
            pass  # fallback ci-dessous
    # Pas de pilmoji : on retire les emojis pour ne pas afficher de tofu.
    ImageDraw.Draw(image).text(xy, _strip_emojis(text), font=font, fill=fill)


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
    title: Optional[str] = None,
    emoji_prefix: Optional[str] = None,
) -> io.BytesIO:
    """Génère la carte et retourne un BytesIO PNG.

    `title` (str) : titre Pass affiche sous le pseudo (ex: "Maître").
    `emoji_prefix` (str) : emoji Pass affiche devant le pseudo (ex: "🌟").
    """
    print(f"[render_niveau_card] user={username} level={level} xp_total={xp_total} "
          f"xp_in_level={xp_in_level} xp_needed={xp_needed} bg={background}", flush=True)
    raw_avatar = await _fetch_avatar_bytes(avatar_url) if avatar_url else None
    return await asyncio.to_thread(
        _render_niveau_sync,
        username, raw_avatar, level, xp_total, xp_in_level, xp_needed,
        background, rank, title, emoji_prefix,
    )


def _render_niveau_sync(username, raw_avatar, level, xp_total, xp_in_level,
                         xp_needed, background, rank, title=None,
                         emoji_prefix=None) -> io.BytesIO:
    base = _load_background(background).convert("RGBA")

    # Voile foncé sous le texte (gauche zone avatar->droite)
    veil = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    vd.rectangle((0, 0, CARD_W, CARD_H), fill=(0, 0, 0, 75))
    base.alpha_composite(veil)

    draw = ImageDraw.Draw(base)

    avatar_img: Optional[Image.Image] = None
    if raw_avatar:
        try:
            avatar_img = _make_round_avatar(raw_avatar, AVATAR_SIZE)
        except Exception:
            avatar_img = None
    if avatar_img is None:
        avatar_img = _placeholder_avatar(AVATAR_SIZE)
    base.alpha_composite(avatar_img, (AVATAR_X, AVATAR_Y))

    text_x = AVATAR_X + AVATAR_SIZE + 40
    # Pseudo (avec emoji prefix si Pass cosmetic actif). Pilmoji gere le rendu.
    name_font = _font(46, bold=True)
    full_name = (f"{emoji_prefix} {username}" if emoji_prefix else username)
    display = full_name
    while draw.textlength(display, font=name_font) > CARD_W - text_x - 40 and len(display) > 1:
        display = display[:-1]
    if display != full_name:
        display = display[:-1] + "…"
    _draw_text_emoji(base, (text_x, 28), display, font=name_font, fill=TEXT_PRIMARY,
                      emoji_scale=0.75)

    # Titre Pass (sous le pseudo) si actif
    if title:
        f_title = _font(18, bold=False)
        title_text = f"« {title} »"
        draw.text((text_x, 78), title_text, font=f_title, fill=ACCENT)
        sub_y = 108
    else:
        sub_y = 100

    # Niveau / XP : 2 colonnes recentrees dans la moitie droite de la zone
    # texte (au lieu de coller a gauche pres de l'avatar). Police plus grosse.
    f_label = _font(24, bold=True)
    f_value = _font(40, bold=True)
    # Centres des 2 colonnes : Niveau a gauche du centre carte, XP TOTAL a droite.
    # Zone disponible : x = text_x .. CARD_W - 60.
    zone_left  = text_x
    zone_right = CARD_W - 60
    zone_w     = zone_right - zone_left
    # 1/3 et 2/3 de la zone pour bien espacer
    col1_cx = zone_left + int(zone_w * 0.30)
    col2_cx = zone_left + int(zone_w * 0.70)

    def _draw_centered(cx, y, text, font, fill):
        tw = draw.textlength(text, font=font)
        draw.text((cx - tw / 2, y), text, font=font, fill=fill)

    _draw_centered(col1_cx, sub_y,      "NIVEAU",                                  f_label, ACCENT)
    _draw_centered(col1_cx, sub_y + 30, str(level),                                f_value, TEXT_PRIMARY)
    _draw_centered(col2_cx, sub_y,      "XP TOTAL",                                f_label, ACCENT)
    _draw_centered(col2_cx, sub_y + 30, f"{xp_total:,}".replace(",", " "),         f_value, TEXT_PRIMARY)
    if rank:
        # Rang : insertion entre les 2 si presente (rare). On le met en bas a droite.
        f_rank = _font(18, bold=True)
        draw.text((zone_right, sub_y - 4), f"#{rank}", font=f_rank,
                  fill=TEXT_SECONDARY, anchor="rt")

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

    f_mention = _font(11, bold=False)
    mention = "Rendu possible grâce à un achat intégré"
    draw.text((CARD_W - 16, CARD_H - 18), mention, font=f_mention,
              fill=(200, 215, 180, 130), anchor="rb")

    # Output (optimize=False pour vitesse, fichier reste petit)
    buf = io.BytesIO()
    base.convert("RGB").save(buf, "PNG", optimize=False, compress_level=6)
    buf.seek(0)
    return buf


async def render_levelup_card_premium(
    *,
    username: str,
    avatar_url: Optional[str],
    new_level: int,
    percent: float = 0,
    background: str = "default",
) -> io.BytesIO:
    """Carte LEVEL UP premium : avatar fetch async + Pillow dans thread."""
    raw_avatar = await _fetch_avatar_bytes(avatar_url) if avatar_url else None
    return await asyncio.to_thread(
        _render_levelup_sync, username, raw_avatar, new_level, percent, background,
    )


# ===== LEVELUP CARD : taille native compacte =====
# Rendue directement a 384x120 (taille d'affichage Discord finale, identique
# a l'ancienne) au lieu de rendre en 1024x320 puis resize (qui rendait
# l'image compressee/pixelisee). Discord ne grossit pas les images <400px
# donc le rendu reste compact dans le chat.
LU_W, LU_H = 384, 120
LU_AVATAR_SIZE = 88
LU_AVATAR_X = 14
LU_AVATAR_Y = (LU_H - LU_AVATAR_SIZE) // 2


def _draw_up_arrow(draw, cx, cy, size, fill):
    """Fleche bold pointant vers le haut, polygone simple."""
    s = size
    # Triangle pointu + queue rectangulaire
    pts = [
        (cx,            cy - s),         # pointe haute
        (cx + s * 0.75, cy - s * 0.05),  # bord droit
        (cx + s * 0.30, cy - s * 0.05),  # creux droit
        (cx + s * 0.30, cy + s * 0.60),  # bas droit queue
        (cx - s * 0.30, cy + s * 0.60),  # bas gauche queue
        (cx - s * 0.30, cy - s * 0.05),  # creux gauche
        (cx - s * 0.75, cy - s * 0.05),  # bord gauche
    ]
    draw.polygon(pts, fill=fill)


def _render_levelup_sync(username, raw_avatar, new_level, percent, background) -> io.BytesIO:
    # Background rendu directement a la taille finale
    base_full = _load_background(background).convert("RGBA")
    base = base_full.resize((LU_W, LU_H), Image.LANCZOS)

    # Voile sombre pour lisibilite
    veil = Image.new("RGBA", (LU_W, LU_H), (0, 0, 0, 110))
    base.alpha_composite(veil)

    # Fleches vertes UP scattered : 13 fleches de tailles variees, opacite
    # faible, reparties sur toute la carte (motif d'ascension).
    arrows = Image.new("RGBA", (LU_W, LU_H), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arrows)
    arrow_positions = [
        (32,  96,  10, 60),
        (72,  24,  8,  50),
        (120, 104, 11, 70),
        (168, 18,  9,  55),
        (208, 104, 10, 60),
        (256, 24,  12, 75),
        (304, 100, 10, 60),
        (344, 28,  11, 70),
        (12,  56,  7,  45),
        (88,  70,  8,  50),
        (192, 60,  9,  55),
        (280, 64,  10, 60),
        (368, 76,  8,  50),
    ]
    for ax, ay, asize, aalpha in arrow_positions:
        _draw_up_arrow(ad, ax, ay, asize, ACCENT + (aalpha,))
    base.alpha_composite(arrows)

    draw = ImageDraw.Draw(base)

    # Avatar
    avatar_img: Optional[Image.Image] = None
    if raw_avatar:
        try:
            avatar_img = _make_round_avatar(raw_avatar, LU_AVATAR_SIZE)
        except Exception:
            avatar_img = None
    if avatar_img is None:
        avatar_img = _placeholder_avatar(LU_AVATAR_SIZE)
    base.alpha_composite(avatar_img, (LU_AVATAR_X, LU_AVATAR_Y))

    # Zone texte : x apres avatar jusqu'au bord droit
    text_x = LU_AVATAR_X + LU_AVATAR_SIZE + 14
    text_right = LU_W - 14
    text_zone_w = text_right - text_x

    # LEVEL UP ! titre centre haut
    f_title = _font(26, bold=True)
    title = "LEVEL UP !"
    glow = Image.new("RGBA", (LU_W, LU_H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    title_tw = gd.textlength(title, font=f_title)
    title_x = text_x + (text_zone_w - title_tw) / 2
    gd.text((title_x, 8), title, font=f_title, fill=ACCENT + (200,))
    glow = glow.filter(ImageFilter.GaussianBlur(3))
    base.alpha_composite(glow)
    draw.text((title_x, 8), title, font=f_title, fill=(250, 255, 230, 255))

    # Pseudo
    f_user = _font(14, bold=True)
    display = username
    while draw.textlength(display, font=f_user) > text_zone_w and len(display) > 1:
        display = display[:-1]
    if display != username:
        display = display[:-1] + "…"
    user_tw = draw.textlength(display, font=f_user)
    draw.text((text_x + (text_zone_w - user_tw) / 2, 44), display,
              font=f_user, fill=TEXT_SECONDARY)

    # NIVEAU label + valeur grosse, centres horizontalement
    f_label = _font(11, bold=True)
    f_value = _font(36, bold=True)
    lbl = "NIVEAU"
    val = str(new_level)
    lbl_tw = draw.textlength(lbl, font=f_label)
    val_tw = draw.textlength(val, font=f_value)
    combined_w = lbl_tw + 10 + val_tw
    combined_x = text_x + (text_zone_w - combined_w) / 2
    draw.text((combined_x, 78), lbl, font=f_label, fill=ACCENT)
    draw.text((combined_x + lbl_tw + 10, 66), val, font=f_value, fill=TEXT_PRIMARY)

    buf = io.BytesIO()
    base.convert("RGB").save(buf, "PNG", optimize=False, compress_level=6)
    buf.seek(0)
    return buf


def list_available_backgrounds(user_id: str = None) -> list[str]:
    """Retourne la liste des IDs de backgrounds disponibles pour cet utilisateur.

    - Tout le monde voit les BG permanents (assets/niveau_bg/*.png)
    - Si `user_id` correspond a un BG custom owner uploade, l'ID 'owner:<id>'
      est ajoute en debut de liste pour ce user uniquement.
    - Si l'user a debloque des BG saisonniers via le Battle Pass, ils sont
      ajoutes (tant que non expires).
    """
    out: list[str] = []
    # BG owner (perso)
    if user_id:
        owner_path = os.path.join(BG_OWNER_DIR, f"{user_id}.png")
        if os.path.exists(owner_path):
            out.append(f"owner:{user_id}")
    # BG saisonniers debloques via Pass
    if user_id:
        try:
            from database import list_user_pass_unlocks as _ul
            for u in _ul(user_id, type_="bg", include_expired=False):
                bg_id = (u.get("payload") or {}).get("bg_id")
                if bg_id and bg_id not in out:
                    out.append(bg_id)
        except Exception:
            pass
    # BG permanents
    if os.path.isdir(BG_DIR):
        for fn in sorted(os.listdir(BG_DIR)):
            if not fn.lower().endswith(".png"):
                continue
            full = os.path.join(BG_DIR, fn)
            if os.path.isfile(full):
                out.append(os.path.splitext(fn)[0])
    return out


def has_owner_custom_bg(user_id: str) -> bool:
    return os.path.exists(os.path.join(BG_OWNER_DIR, f"{user_id}.png"))


def save_owner_custom_bg(user_id: str, source_image: Image.Image):
    """Sauvegarde un BG custom owner (1024x320, redimensionne si besoin)."""
    if source_image.size != (CARD_W, CARD_H):
        source_image = source_image.convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)
    else:
        source_image = source_image.convert("RGB")
    path = os.path.join(BG_OWNER_DIR, f"{user_id}.png")
    source_image.save(path, "PNG", optimize=False, compress_level=6)
    invalidate_bg_cache(f"owner:{user_id}")


def remove_owner_custom_bg(user_id: str):
    path = os.path.join(BG_OWNER_DIR, f"{user_id}.png")
    if os.path.exists(path):
        os.remove(path)
    invalidate_bg_cache(f"owner:{user_id}")
