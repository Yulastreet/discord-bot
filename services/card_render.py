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
_STARS_PATH = os.path.join(_ROOT, "assets", "cardrelated", "stars.png")
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_border_cache: dict[str, Image.Image] = {}
_star_cache: dict[tuple, Image.Image] = {}

# Etoiles par rareté (assets/cardrelated). secret -> fichier mythic (pas de fichier dedie).
_STARS_BY_RARITY = {
    "common":    "starscommune.png",
    "rare":      "starsrare.png",
    "epic":      "starsepic.png",
    "legendary": "starslegendaire.png",
    "mythic":    "starsmythic.png",
    "secret":    "starsmythic.png",
}


def _star_path(rarity: str | None) -> str:
    fname = _STARS_BY_RARITY.get((rarity or "").lower())
    if fname:
        p = os.path.join(_ROOT, "assets", "cardrelated", fname)
        if os.path.exists(p):
            return p
    return _STARS_PATH  # fallback ancien sprite unique


def _get_star(rarity: str | None, size: int) -> Image.Image | None:
    key = ((rarity or "").lower(), size)
    if key in _star_cache:
        return _star_cache[key]
    path = _star_path(rarity)
    if not os.path.exists(path):
        return None
    try:
        img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        _star_cache[key] = img
        return img
    except Exception:
        return None


def _overlay_stars(canvas: Image.Image, level: int, rarity: str | None = None) -> Image.Image:
    """Pose une rangee de `level` etoiles (sprite selon rarete) en bas-centre.
    L'etoile du milieu est agrandie UNIQUEMENT a 5 etoiles ; sinon toutes egales."""
    level = max(0, min(5, int(level or 0)))
    if level <= 0:
        return canvas
    path = _star_path(rarity)
    if not os.path.exists(path):
        print(f"[card_render] sprite etoiles introuvable: {path}")
        return canvas
    base_size = 46
    mid_size = 64        # etoile centrale agrandie (5 etoiles seulement)
    gap = 6
    center_y = int(_CARD_H * 0.80)  # un peu plus bas, sans toucher le cadre
    if level >= 5:
        mid_index = level // 2      # etoile du milieu (5 -> index 2)
        sizes = [mid_size if i == mid_index else base_size for i in range(level)]
    else:
        sizes = [base_size] * level  # avant 5 : toutes la meme taille
    total_w = sum(sizes) + gap * (level - 1)
    out = canvas.convert("RGBA")
    x = (_CARD_W - total_w) // 2
    for s in sizes:
        star = _get_star(rarity, s)
        if star is not None:
            y = center_y - s // 2  # centre vertical aligne
            out.paste(star, (x, y), star)
        x += s + gap
    return out


def _load_base(card_id: int, fallback_url: str | None = None) -> Image.Image | None:
    """Charge le render local de la carte. Fallback : download image_url remote
    (UA navigateur + cache local pour eviter de re-telecharger / 429)."""
    for _ext in (".webp", ".png"):
        local = os.path.join(_RENDERS_DIR, f"{card_id}{_ext}")
        if os.path.exists(local):
            try:
                return Image.open(local).convert("RGBA")
            except Exception:
                pass
    if fallback_url and fallback_url.startswith("http"):
        try:
            req = urllib.request.Request(fallback_url, headers={
                "User-Agent": _USER_AGENT,
                "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
                "Referer": "https://tookbot.click/",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            im = Image.open(io.BytesIO(data))
            try:
                im.seek(0)  # GIF/WEBP animé (cartes secretes) : 1ere frame
            except Exception:
                pass
            img = im.convert("RGBA")
            img = img.resize((_CARD_W, _CARD_H), Image.LANCZOS)
            # Cache en local render (webp leger) pour ne plus retelecharger (evite 429)
            try:
                os.makedirs(_RENDERS_DIR, exist_ok=True)
                img.convert("RGB").save(os.path.join(_RENDERS_DIR, f"{card_id}.webp"),
                                        "WEBP", quality=92, method=6)
            except Exception:
                pass
            return img
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
                              scale_pct: int = 100, card_scale_pct: int = 100) -> Image.Image:
    """Composite bordure sur base (450x675). Retourne nouvelle image RGBA.

    card_scale_pct redimensionne la carte dans le cadre (fond rempli par
    la carte plein-cadre derriere les marges)."""
    if base.size != (_CARD_W, _CARD_H):
        base = base.resize((_CARD_W, _CARD_H), Image.LANCZOS)
    canvas = base.copy().convert("RGBA")
    # Echelle de la carte dans le cadre
    cs = max(20, min(200, int(card_scale_pct or 100))) / 100.0
    if abs(cs - 1.0) > 0.001:
        cw, ch = int(_CARD_W * cs), int(_CARD_H * cs)
        scaled = base.resize((cw, ch), Image.LANCZOS)
        px = (_CARD_W - cw) // 2
        py = (_CARD_H - ch) // 2
        if cs < 1.0:
            # Marges transparentes (pas de fond) + carte scalee centree
            canvas = Image.new("RGBA", (_CARD_W, _CARD_H), (0, 0, 0, 0))
            canvas.paste(scaled, (px, py), scaled if scaled.mode == "RGBA" else None)
        else:
            # carte plus grande : crop au centre
            canvas = scaled.crop((-px, -py, -px + _CARD_W, -py + _CARD_H)).convert("RGBA")
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


def _load_alt(card_id: int) -> Image.Image | None:
    """Charge le render du skin ALT (card_renders/<id>_alt.png|webp) ou None."""
    for _ext in (".webp", ".png"):
        p = os.path.join(_RENDERS_DIR, f"{card_id}_alt{_ext}")
        if os.path.exists(p):
            try:
                return Image.open(p).convert("RGBA")
            except Exception:
                pass
    return None


def compose_card_image(card_id: int, border: dict | None = None,
                        fusion_level: int = 0,
                        fallback_url: str | None = None,
                        alt: bool = False) -> Image.Image | None:
    """Compose la carte (bordure et/ou etoiles) et retourne l'image PIL RGBA, ou None.
    alt=True : utilise le skin alternatif comme base (les event cards n'ont pas de
    bordure ; on garde juste les etoiles de fusion sur l'art alt)."""
    base = (_load_alt(card_id) if alt else None) or _load_base(card_id, fallback_url)
    if base is None:
        print(f"[card_render] base introuvable card={card_id} fallback={fallback_url}")
        return None
    if border and not alt:
        bimg = _load_border(border["filename"])
        if bimg is None:
            print(f"[card_render] bordure introuvable: {border.get('filename')}")
            out = base.convert("RGBA")
        else:
            out = composite_border_preview(
                base, bimg,
                offset_x=border.get("offset_x", 0),
                offset_y=border.get("offset_y", 0),
                scale_pct=border.get("scale_pct", 100),
                card_scale_pct=border.get("card_scale_pct", 100),
            )
    else:
        out = base.convert("RGBA")
    if fusion_level and fusion_level > 0:
        rarity = None
        try:
            from database import card_get
            _c = card_get(int(card_id))
            rarity = _c.get("rarity") if _c else None
        except Exception:
            rarity = None
        out = _overlay_stars(out, fusion_level, rarity)
    return out


def render_user_card(user_id: int, card_id: int, border: dict | None = None,
                      fusion_level: int = 0,
                      fallback_url: str | None = None,
                      alt: bool = False) -> str | None:
    """Genere render carte custom (bordure et/ou etoiles fusion). URL relative ou None.
    alt=True : compose sur le skin alternatif (etoiles seulement)."""
    os.makedirs(_CUSTOMS_DIR, exist_ok=True)
    out = compose_card_image(card_id, border, fusion_level, fallback_url, alt=alt)
    if out is None:
        return None
    suffix = "_alt" if alt else ""
    out_path = os.path.join(_CUSTOMS_DIR, f"{user_id}_{card_id}{suffix}.png")
    out.save(out_path, "PNG", optimize=True)
    return f"/static/card_customs/{user_id}_{card_id}{suffix}.png"


def render_border_preview_file(border_key: str, filename: str,
                                offset_x: int = 0, offset_y: int = 0,
                                scale_pct: int = 100, card_scale_pct: int = 100,
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
    out = composite_border_preview(base, bimg, offset_x, offset_y, scale_pct,
                                    card_scale_pct=card_scale_pct)
    out_path = os.path.join(_CUSTOMS_DIR, f"_preview_{border_key}.png")
    out.save(out_path, "PNG", optimize=True)
    return f"/static/card_customs/_preview_{border_key}.png"
