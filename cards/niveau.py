"""Premium /niveau card rendering (Pillow).

Composes a 1024x320 image:
- Background chosen by the user (assets/niveau_bg/<id>.png)
- Round Discord avatar
- Username + Level + total XP
- XP progress bar
- Premium badge
- Discreet "made possible by an in-app purchase" note

Returns a PNG BytesIO, ready to be passed to `discord.File`.
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

# pilmoji renders color emojis (Twemoji) on Pillow images.
# Graceful fallback if the lib is not available.
try:
    from pilmoji import Pilmoji
    _HAS_PILMOJI = True
except Exception:
    _HAS_PILMOJI = False
    Pilmoji = None  # type: ignore


CARD_W, CARD_H = 1024, 320
# Module moved into cards/, so go up one level to point at assets/
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG_DIR = os.path.join(_REPO_ROOT, "assets", "niveau_bg")
# Custom owner BG: 1 per owner_id (personal, never exposed to others).
BG_OWNER_DIR = os.path.join(BG_DIR, "owner")
os.makedirs(BG_OWNER_DIR, exist_ok=True)
AVATAR_SIZE = 200
AVATAR_X, AVATAR_Y = 60, (CARD_H - AVATAR_SIZE) // 2

ACCENT = (200, 240, 80)            # TookBot acid lime
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


# Shared HTTP session (TLS reuse + connection pool).
_HTTP_SESSION: Optional[aiohttp.ClientSession] = None
_HTTP_SESSION_LOCK = asyncio.Lock()

# Avatar bytes cache: { url: (expires_ts, bytes) }. Short TTL because Discord
# avatars change rarely but we still want to follow renames; 10 min is enough.
_AVATAR_CACHE: dict[str, tuple[float, bytes]] = {}
_AVATAR_TTL = 600  # seconds
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


# Backgrounds cached in RAM: { id: (mtime_disk, Image RGB) }.
# The key includes the source file mtime; when the file is modified (owner
# upload, seasonal regeneration) the entry is invalidated automatically, which
# makes the cache transparent across pm2 processes (bot and web).
_BG_CACHE: dict[str, tuple[float, Image.Image]] = {}


def _resolve_bg_path(bg_id: str) -> Optional[str]:
    """Resolve a BG ID to a disk path.

    Supports:
    - 'owner:<owner_id>' -> assets/niveau_bg/owner/<owner_id>.png  (owner only)
    - 'seasonal:<YYYY-MM>:<name>' -> assets/niveau_bg/seasonal/<YYYY-MM>/<name>.png
    - plain '<name>' -> assets/niveau_bg/<name>.png  (permanent BG)
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
    """Load the requested BG (mtime-aware RAM cache), fallback default then gradient.

    The cache stores (mtime_disk, Image). If the file on disk has a more recent
    mtime than the cached one, it is reloaded automatically - useful to
    propagate an owner BG upload or the seasonal BG regeneration across pm2
    processes (bot + web each have their own cache).

    Returns a COPY so the following operations (alpha_composite, draw) do not
    mutate the cached image.
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
    # Procedural fallback if no file is available
    return Image.new("RGB", (CARD_W, CARD_H), (22, 24, 30))


def invalidate_bg_cache(bg_id: str = None):
    """Call after uploading/replacing a BG to purge the RAM cache."""
    if bg_id is None:
        _BG_CACHE.clear()
    else:
        _BG_CACHE.pop(bg_id, None)


def preload_backgrounds():
    """Call at boot to avoid the first disk decode latency."""
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
    "\U0001F1E6-\U0001F1FF"  # regional flags
    "\U0001F300-\U0001FAFF"  # extended symbols & pictograms
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F000-\U0001F0FF"  # mahjong/dominoes/cards
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U00002190-\U000021FF"  # arrows
    "\U00002B00-\U00002BFF"  # misc arrows & stars
    "\U0000200D"             # zero width joiner
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    """Strip unicode emojis and clean up leftover spaces (fallback without pilmoji)."""
    return _EMOJI_RE.sub("", text).strip()


def _draw_text_emoji(image: Image.Image, xy, text: str, font, fill,
                      emoji_scale: float = 1.0):
    """Draw text that may contain color emojis.

    Uses pilmoji when available (clean Twemoji rendering). Otherwise falls back
    to Pillow, which strips the emojis (avoids the default font's tofu box).
    `emoji_scale` tunes the emoji glyph size (vertical alignment).
    """
    if _HAS_PILMOJI:
        try:
            with Pilmoji(image) as pj:
                pj.text(xy, text, font=font, fill=fill, emoji_scale_factor=emoji_scale)
            return
        except Exception:
            pass  # fallback below
    # No pilmoji: strip the emojis so we don't render tofu boxes.
    ImageDraw.Draw(image).text(xy, _strip_emojis(text), font=font, fill=fill)


def _draw_xp_bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, percent: float):
    percent = max(0.0, min(100.0, float(percent)))
    radius = h // 2
    # Track
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=(20, 22, 30, 220))
    # Inner shadow line (clean look)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, outline=(0, 0, 0, 100), width=1)
    # Fill
    fill_w = int(w * percent / 100)
    if fill_w >= 4:
        draw.rounded_rectangle((x, y, x + fill_w, y + h), radius=radius, fill=ACCENT + (255,))
        # Top highlight
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
    """Generate the card and return a PNG BytesIO.

    `title` (str): Pass title shown under the username (e.g. "Master").
    `emoji_prefix` (str): Pass emoji shown before the username (e.g. "🌟").
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

    # Dark veil under the text (from the avatar zone on the left to the right)
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
    # Username (with emoji prefix if a cosmetic Pass is active). Pilmoji handles rendering.
    name_font = _font(46, bold=True)
    full_name = (f"{emoji_prefix} {username}" if emoji_prefix else username)
    display = full_name
    while draw.textlength(display, font=name_font) > CARD_W - text_x - 40 and len(display) > 1:
        display = display[:-1]
    if display != full_name:
        display = display[:-1] + "…"
    _draw_text_emoji(base, (text_x, 28), display, font=name_font, fill=TEXT_PRIMARY,
                      emoji_scale=0.75)

    # Pass title (under the username) if active
    if title:
        f_title = _font(18, bold=False)
        title_text = f"« {title} »"
        draw.text((text_x, 78), title_text, font=f_title, fill=ACCENT)
        sub_y = 108
    else:
        sub_y = 100

    # Level / XP: 2 columns re-centered in the right half of the text zone
    # (instead of hugging the left next to the avatar). Bigger font.
    f_label = _font(24, bold=True)
    f_value = _font(40, bold=True)
    # Centers of the 2 columns: Level left of the card center, TOTAL XP right.
    # Available zone: x = text_x .. CARD_W - 60.
    zone_left  = text_x
    zone_right = CARD_W - 60
    zone_w     = zone_right - zone_left
    # 1/3 and 2/3 of the zone for good spacing
    col1_cx = zone_left + int(zone_w * 0.30)
    col2_cx = zone_left + int(zone_w * 0.70)

    def _draw_centered(cx, y, text, font, fill):
        tw = draw.textlength(text, font=font)
        draw.text((cx - tw / 2, y), text, font=font, fill=fill)

    _draw_centered(col1_cx, sub_y,      "LEVEL",                                   f_label, ACCENT)
    _draw_centered(col1_cx, sub_y + 30, str(level),                                f_value, TEXT_PRIMARY)
    _draw_centered(col2_cx, sub_y,      "TOTAL XP",                                f_label, ACCENT)
    _draw_centered(col2_cx, sub_y + 30, f"{xp_total:,}".replace(",", " "),         f_value, TEXT_PRIMARY)
    if rank:
        # Rank: inserted between the 2 when present (rare). Put it bottom right.
        f_rank = _font(18, bold=True)
        draw.text((zone_right, sub_y - 4), f"#{rank}", font=f_rank,
                  fill=TEXT_SECONDARY, anchor="rt")

    bar_x = text_x
    bar_y = 200
    bar_w = CARD_W - bar_x - 60
    bar_h = 26
    pct = (xp_in_level / xp_needed * 100) if xp_needed > 0 else 0
    _draw_xp_bar(draw, bar_x, bar_y, bar_w, bar_h, pct)
    # Text under the bar
    f_xp = _font(18, bold=False)
    xp_text = f"{xp_in_level:,} / {xp_needed:,} XP".replace(",", " ")
    draw.text((bar_x, bar_y + bar_h + 8), xp_text, font=f_xp, fill=TEXT_SECONDARY)
    pct_text = f"{pct:.0f}%"
    pct_w = draw.textlength(pct_text, font=f_xp)
    draw.text((bar_x + bar_w - pct_w, bar_y + bar_h + 8), pct_text, font=f_xp, fill=TEXT_SECONDARY)

    f_mention = _font(11, bold=False)
    mention = "Made possible by an in-app purchase"
    draw.text((CARD_W - 16, CARD_H - 18), mention, font=f_mention,
              fill=(200, 215, 180, 130), anchor="rb")

    # Output (optimize=False for speed, the file stays small)
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
    """Premium LEVEL UP card: async avatar fetch + Pillow in a thread."""
    raw_avatar = await _fetch_avatar_bytes(avatar_url) if avatar_url else None
    return await asyncio.to_thread(
        _render_levelup_sync, username, raw_avatar, new_level, percent, background,
    )


# ===== LEVELUP CARD: compact native size =====
# Rendered directly at 384x120 (final Discord display size, same as the old
# one) instead of rendering at 1024x320 then resizing (which made the image
# compressed/pixelated). Discord does not upscale images under 400px so the
# render stays compact in chat.
LU_W, LU_H = 384, 120
LU_AVATAR_SIZE = 88
LU_AVATAR_X = 14
LU_AVATAR_Y = (LU_H - LU_AVATAR_SIZE) // 2


def _draw_up_arrow(draw, cx, cy, size, fill):
    """Bold arrow pointing up, simple polygon."""
    s = size
    # Sharp triangle + rectangular tail
    pts = [
        (cx,            cy - s),         # top point
        (cx + s * 0.75, cy - s * 0.05),  # right edge
        (cx + s * 0.30, cy - s * 0.05),  # right notch
        (cx + s * 0.30, cy + s * 0.60),  # tail bottom right
        (cx - s * 0.30, cy + s * 0.60),  # tail bottom left
        (cx - s * 0.30, cy - s * 0.05),  # left notch
        (cx - s * 0.75, cy - s * 0.05),  # left edge
    ]
    draw.polygon(pts, fill=fill)


def _render_levelup_sync(username, raw_avatar, new_level, percent, background) -> io.BytesIO:
    # Background rendered directly at the final size
    base_full = _load_background(background).convert("RGBA")
    base = base_full.resize((LU_W, LU_H), Image.LANCZOS)

    # Dark veil for readability
    veil = Image.new("RGBA", (LU_W, LU_H), (0, 0, 0, 110))
    base.alpha_composite(veil)

    # Scattered green UP arrows: 13 arrows of varied sizes, low opacity,
    # spread over the whole card (ascension pattern).
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

    # Text zone: x after the avatar up to the right edge
    text_x = LU_AVATAR_X + LU_AVATAR_SIZE + 14
    text_right = LU_W - 14
    text_zone_w = text_right - text_x

    # LEVEL UP! title centered at the top
    f_title = _font(26, bold=True)
    title = "LEVEL UP!"
    glow = Image.new("RGBA", (LU_W, LU_H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    title_tw = gd.textlength(title, font=f_title)
    title_x = text_x + (text_zone_w - title_tw) / 2
    gd.text((title_x, 8), title, font=f_title, fill=ACCENT + (200,))
    glow = glow.filter(ImageFilter.GaussianBlur(3))
    base.alpha_composite(glow)
    draw.text((title_x, 8), title, font=f_title, fill=(250, 255, 230, 255))

    # Username
    f_user = _font(14, bold=True)
    display = username
    while draw.textlength(display, font=f_user) > text_zone_w and len(display) > 1:
        display = display[:-1]
    if display != username:
        display = display[:-1] + "…"
    user_tw = draw.textlength(display, font=f_user)
    draw.text((text_x + (text_zone_w - user_tw) / 2, 44), display,
              font=f_user, fill=TEXT_SECONDARY)

    # LEVEL label + big value, horizontally centered
    f_label = _font(11, bold=True)
    f_value = _font(36, bold=True)
    lbl = "LEVEL"
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
    """Return the list of background IDs available to this user.

    - Everyone sees the permanent BGs (assets/niveau_bg/*.png)
    - If `user_id` matches an uploaded custom owner BG, the ID 'owner:<id>'
      is prepended to the list for that user only.
    - If the user unlocked seasonal BGs via the Battle Pass, they are added
      (as long as they are not expired).
    """
    out: list[str] = []
    # Owner BG (personal)
    if user_id:
        owner_path = os.path.join(BG_OWNER_DIR, f"{user_id}.png")
        if os.path.exists(owner_path):
            out.append(f"owner:{user_id}")
    # Seasonal BGs unlocked via the Pass
    if user_id:
        try:
            from database import list_user_pass_unlocks as _ul
            for u in _ul(user_id, type_="bg", include_expired=False):
                bg_id = (u.get("payload") or {}).get("bg_id")
                if bg_id and bg_id not in out:
                    out.append(bg_id)
        except Exception:
            pass
    # Permanent BGs
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
    """Save a custom owner BG (1024x320, resized if needed)."""
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
