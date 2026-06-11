"""Render carte + bordure custom (cosmetique joueur).

Base = render local static/card_renders/<card_id>.png (450x675).
Bordure = PNG RGBA dans assets/cardrelated/borders/, resize + offset selon
config owner (offset_x, offset_y, scale_pct). Output dans
static/card_customs/<user_id>_<card_id>.png.
"""
from __future__ import annotations

import io
import os
import urllib.request
from PIL import Image

_CARD_W = 450
_CARD_H = 675
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RENDERS_DIR = os.path.join(_ROOT, "static", "card_renders")
_CUSTOMS_DIR = os.path.join(_ROOT, "static", "card_customs")
_BORDERS_DIR = os.path.join(_ROOT, "assets", "cardrelated", "borders")
_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"

_border_cache: dict[str, Image.Image] = {}


def _load_base(card_id: int, fallback_url: str | None = None) -> Image.Image | None:
    """Charge le render local de la carte. Fallback : download image_url remote."""
    local = os.path.join(_RENDERS_DIR, f"{card_id}.png")
    if os.path.exists(local):
        try:
            return Image.open(local).convert("RGBA")
        except Exception:
            pass
    if fallback_url and fallback_url.startswith("http"):
        try:
            req = urllib.request.Request(fallback_url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            return img.resize((_CARD_W, _CARD_H), Image.LANCZOS)
        except Exception as e:
            print(f"[card_render] base download err {fallback_url}: {e}")
    return None


def _load_border(filename: str) -> Image.Image | None:
    if filename in _border_cache:
        return _border_cache[filename]
    path = os.path.join(_BORDERS_DIR, filename)
    if not os.path.exists(path):
        print(f"[card_render] border missing {path}")
        return None
    try:
        img = Image.open(path).convert("RGBA")
        _border_cache[filename] = img
        return img
    except Exception as e:
        print(f"[card_render] border load err {path}: {e}")
        return None


def composite_border_preview(base: Image.Image, border_img: Image.Image,
                              offset_x: int = 0, offset_y: int = 0,
                              scale_pct: int = 100) -> Image.Image:
    """Composite bordure sur base (450x675). Retourne nouvelle image RGBA."""
    if base.size != (_CARD_W, _CARD_H):
        base = base.resize((_CARD_W, _CARD_H), Image.LANCZOS)
    canvas = base.copy().convert("RGBA")
    scale = max(10, min(300, int(scale_pct or 100))) / 100.0
    bw = int(_CARD_W * scale)
    bh = int(_CARD_H * scale)
    bimg = border_img.resize((bw, bh), Image.LANCZOS)
    # Centre + offset
    px = (_CARD_W - bw) // 2 + int(offset_x or 0)
    py = (_CARD_H - bh) // 2 + int(offset_y or 0)
    layer = Image.new("RGBA", (_CARD_W, _CARD_H), (0, 0, 0, 0))
    layer.paste(bimg, (px, py), bimg)
    return Image.alpha_composite(canvas, layer)


def render_user_card(user_id: int, card_id: int, border: dict,
                      fallback_url: str | None = None) -> str | None:
    """Genere render carte + bordure pour un user. Retourne URL relative ou None.

    border = dict de la table borders (filename, offset_x, offset_y, scale_pct)."""
    os.makedirs(_CUSTOMS_DIR, exist_ok=True)
    base = _load_base(card_id, fallback_url)
    if base is None:
        return None
    bimg = _load_border(border["filename"])
    if bimg is None:
        return None
    out = composite_border_preview(
        base, bimg,
        offset_x=border.get("offset_x", 0),
        offset_y=border.get("offset_y", 0),
        scale_pct=border.get("scale_pct", 100),
    )
    out_path = os.path.join(_CUSTOMS_DIR, f"{user_id}_{card_id}.png")
    out.convert("RGB").save(out_path, "PNG", optimize=True)
    return f"/static/card_customs/{user_id}_{card_id}.png"


def render_border_preview_file(border_key: str, filename: str,
                                offset_x: int = 0, offset_y: int = 0,
                                scale_pct: int = 100,
                                placeholder_card_id: int | None = None) -> str | None:
    """Genere preview placement bordure sur une carte placeholder (dashboard owner).
    Output static/card_customs/_preview_<border_key>.png."""
    os.makedirs(_CUSTOMS_DIR, exist_ok=True)
    base = None
    if placeholder_card_id:
        base = _load_base(placeholder_card_id)
    if base is None:
        # Placeholder gris uni
        base = Image.new("RGBA", (_CARD_W, _CARD_H), (40, 42, 48, 255))
    bimg = _load_border(filename)
    if bimg is None:
        return None
    out = composite_border_preview(base, bimg, offset_x, offset_y, scale_pct)
    out_path = os.path.join(_CUSTOMS_DIR, f"_preview_{border_key}.png")
    out.convert("RGB").save(out_path, "PNG", optimize=True)
    return f"/static/card_customs/_preview_{border_key}.png"
