"""Renders an 'X tweet' style image from a Discord message.

Composes a rectangular image:
- Header: round avatar + display_name + @username + verified tick
- Body: message text between quotes, elegant serif font
- Footer: formatted timestamp (16:42 · 4 Jun 2026 · X)

Returns a PNG BytesIO for discord.File.
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont


# Tweet card dimensions. Fixed width, height computed dynamically from the
# text length (wraps to 8 lines max, then truncates).
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
    """Wrap word by word while respecting the max width."""
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
            # The word itself is wider: hard split
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


def _draw_action_icon(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int,
                       size: int = 18, color=(113, 118, 123)):
    """Draw an X action icon (reply/retweet/like/views/share) with PIL
    primitives for a crisp render (no pilmoji dependency)."""
    s = size
    if kind == "reply":
        # Rounded chat bubble + small tail
        draw.rounded_rectangle((x, y, x + s, y + int(s * 0.78)),
                                radius=int(s * 0.28), outline=color, width=2)
        # Small bottom-left tail triangle
        draw.polygon([
            (x + 4, y + int(s * 0.78)),
            (x + 10, y + int(s * 0.78)),
            (x + 4, y + s),
        ], fill=color)
    elif kind == "retweet":
        # 2 stylized looping arrows (rounded rect outline + 2 chevrons)
        # Simple drawing: 2 triangle polygons + lines
        draw.line([(x + 2, y + 4), (x + s - 2, y + 4)], fill=color, width=2)
        draw.polygon([(x + s - 6, y), (x + s, y + 4), (x + s - 6, y + 8)], fill=color)
        draw.line([(x + s - 2, y + s - 4), (x + 2, y + s - 4)], fill=color, width=2)
        draw.polygon([(x + 6, y + s - 8), (x, y + s - 4), (x + 6, y + s)], fill=color)
    elif kind == "like":
        # Simple heart outline from 2 circles + a triangle
        r = int(s * 0.28)
        draw.ellipse((x, y + 2, x + r * 2, y + r * 2 + 2), outline=color, width=2)
        draw.ellipse((x + r * 2 - 1, y + 2, x + r * 4 - 1, y + r * 2 + 2),
                      outline=color, width=2)
        draw.polygon([
            (x, y + r + 4),
            (x + s, y + r + 4),
            (x + s // 2, y + s),
        ], outline=color, width=2)
    elif kind == "views":
        # Bar chart, 3 bars
        bw = max(2, s // 6)
        for i, h in enumerate([0.4, 0.7, 1.0]):
            bh = int(s * h)
            bx = x + i * (bw + 3)
            by = y + (s - bh)
            draw.rectangle((bx, by, bx + bw, y + s), fill=color)
    elif kind == "share":
        # Arrow rising out of a box (upload style)
        # Box
        draw.rectangle((x, y + s // 2, x + s, y + s), outline=color, width=2)
        # Arrow
        draw.line((x + s // 2, y + 2, x + s // 2, y + s - 4), fill=color, width=2)
        draw.polygon([
            (x + s // 2 - 4, y + 6),
            (x + s // 2 + 4, y + 6),
            (x + s // 2,     y + 2),
        ], fill=color)


def _draw_verified_tick(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int = 9):
    """Small round blue badge with a white check."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=VERIFIED)
    # Stylized check
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
    counts: Optional[dict] = None,
    image_url: Optional[str] = None,
) -> io.BytesIO:
    """Render the tweet card -> PNG BytesIO.

    `timestamp_str`: already formatted (e.g. '16:42 · 4 Jun 2026').
    `counts`: optional dict {reply, retweet, like, views} for the actions.
    `image_url`: URL of an image attached to the message, embedded under the text.
    """
    raw_avatar = await _fetch_bytes(avatar_url) if avatar_url else None
    raw_image = await _fetch_bytes(image_url) if image_url else None
    return await asyncio.to_thread(
        _render_tweet_sync,
        display_name, username, raw_avatar, text, timestamp_str, verified,
        counts or {}, raw_image,
    )


def _fmt_count(n: int) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def _render_tweet_sync(display_name, username, raw_avatar, text,
                       timestamp_str, verified, counts, raw_image=None) -> io.BytesIO:
    # Pre-compute the wraps to determine the height
    f_name = _font(22, bold=True)
    f_user = _font(18, bold=False)
    f_body = _font(26, bold=False)
    f_meta = _font(15, bold=False)

    # Temporary draw surface for textlength computations
    tmp = Image.new("RGB", (TW_W, 400), BG)
    td = ImageDraw.Draw(tmp)

    body_text = f'"{text}"' if text else '" "'
    body_lines = _wrap_text(body_text, f_body, TW_W - TW_PAD * 2, td)
    # Limit to 10 lines (truncate)
    if len(body_lines) > 10:
        body_lines = body_lines[:10]
        body_lines[-1] = body_lines[-1].rstrip() + "..."

    line_h = 36
    header_h = TW_PAD + TW_AVATAR + 20
    body_h = len(body_lines) * line_h + 20
    # Footer = timestamp line + separator + actions row
    footer_h = 28 + 18 + 38 + TW_PAD

    # Embedded image: resized to width = TW_W - 2*TW_PAD, keeps the ratio,
    # height capped at 500px (otherwise too big for a tweet card).
    img_block_h = 0
    img_resized = None
    if raw_image:
        try:
            img = Image.open(io.BytesIO(raw_image)).convert("RGBA")
            target_w = TW_W - TW_PAD * 2
            ratio = target_w / img.width
            new_h = int(img.height * ratio)
            if new_h > 500:
                # Cap: keep the ratio but shrink the height
                new_h = 500
                target_w = int(img.width * (new_h / img.height))
            img_resized = img.resize((target_w, new_h), Image.LANCZOS)
            img_block_h = new_h + 16  # margin before
        except Exception as _e:
            img_resized = None
            img_block_h = 0

    total_h = header_h + body_h + img_block_h + footer_h

    base = Image.new("RGB", (TW_W, total_h), BG)
    draw = ImageDraw.Draw(base)

    # === Header: avatar + name/user ===
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

    # === Embedded image (after body) ===
    if img_resized is not None:
        img_y = header_h + body_h - 4
        img_x = TW_PAD
        # Rounded corners: apply a mask
        mask = Image.new("L", img_resized.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, img_resized.size[0], img_resized.size[1]),
            radius=16, fill=255,
        )
        base.paste(img_resized.convert("RGB"), (img_x, img_y), mask)

    # === Footer: timestamp ===
    tsy = header_h + body_h + img_block_h + 4
    draw.text((TW_PAD, tsy),
              f"{timestamp_str} · TookBot",
              font=f_meta, fill=TEXT_MUTED)

    # Separator line
    sep_y = tsy + 26
    draw.line((TW_PAD, sep_y, TW_W - TW_PAD, sep_y),
              fill=(38, 51, 64), width=1)

    # === Actions row: reply / retweet / like / views / share ===
    icon_y = sep_y + 16
    action_specs = [
        ("reply",   counts.get("reply", 0)),
        ("retweet", counts.get("retweet", 0)),
        ("like",    counts.get("like", 0)),
        ("views",   counts.get("views", 0)),
        ("share",   None),  # no count for share
    ]
    n_actions = len(action_specs)
    icon_zone_w = TW_W - TW_PAD * 2
    col_w = icon_zone_w // n_actions
    icon_size = 20
    f_count = _font(14, bold=False)
    for i, (kind, n) in enumerate(action_specs):
        col_cx = TW_PAD + i * col_w + col_w // 2
        icon_x = col_cx - 30
        _draw_action_icon(draw, kind, icon_x, icon_y,
                           size=icon_size, color=TEXT_MUTED)
        # Count to the right of the icon
        if n:
            label = _fmt_count(int(n))
            draw.text((icon_x + icon_size + 8, icon_y + 2),
                      label, font=f_count, fill=TEXT_MUTED)

    # Mini X logo (top right)
    x_size = 24
    x_x = TW_W - TW_PAD - x_size
    x_y = TW_PAD + 6
    f_x = _font(28, bold=True)
    draw.text((x_x, x_y), "𝕏", font=f_x, fill=TEXT)

    buf = io.BytesIO()
    base.save(buf, "PNG", optimize=False, compress_level=6)
    buf.seek(0)
    return buf
