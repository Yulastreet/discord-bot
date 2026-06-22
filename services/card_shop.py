"""Card Shop : composite l'image du shop (6 slots) + helpers achat.

Fond = assets/cardrelated/cardshopbg.png (1672x941, grille 3x2 d'emplacements).
Chaque slot enabled affiche l'item (carte ou bordure) + label + prix en Essences.
Sert a la fois pour le post Discord et la preview dashboard.
"""
from __future__ import annotations

import io
import os
import urllib.request
from PIL import Image, ImageDraw, ImageFont

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BG_PATH = os.path.join(_ROOT, "assets", "cardrelated", "cardshopbg.png")
_BORDERS_DIR = os.path.join(_ROOT, "assets", "cardrelated", "borders")
_RENDERS_DIR = os.path.join(_ROOT, "static", "card_renders")
_OUT_DIR = os.path.join(_ROOT, "static", "card_shop")
_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"

# Prix par defaut suggeres (essences). Base E ~40/roll.
# epic=20 rolls, legendary=70, mythic=200, bordure=200.
DEFAULT_CARD_PRICES = {
    "common":    510,
    "rare":      900,
    "epic":      3900,
    "legendary": 5440,
    "mythic":    15400,
    # secret : jamais vendu en boutique (exclu du shuffle)
}
DEFAULT_BORDER_PRICE = 16000


def suggested_price(item_type: str, item_ref) -> int:
    """Prix par defaut suggere selon le type/rarete de l'item."""
    if item_type == "border":
        return DEFAULT_BORDER_PRICE
    if item_type == "card" and item_ref:
        from database import card_get
        try:
            card = card_get(int(item_ref))
        except (ValueError, TypeError):
            card = None
        if card:
            return DEFAULT_CARD_PRICES.get(card.get("rarity", "common"), 200)
    return 0


# Centres des 6 slots (grille 3x2) sur le fond 1672x941
SLOT_CENTERS = [
    (378, 285), (836, 285), (1294, 285),   # rangee haut : slots 1,2,3
    (378, 650), (836, 650), (1294, 650),   # rangee bas  : slots 4,5,6
]
_THUMB_W = 172
_THUMB_H = 258

_FONT_CACHE: dict = {}


def _font(size: int, bold: bool = True):
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


def _download(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return Image.open(io.BytesIO(resp.read())).convert("RGBA")
    except Exception as e:
        print(f"[card_shop] download err {url}: {e}")
        return None


def _card_thumb(card_id, image_url):
    """Charge render local de la carte (webp/png), puis /static local, puis download."""
    img = None
    # 1. render local : webp d'abord (migration), puis png
    for ext in (".webp", ".png"):
        local = os.path.join(_RENDERS_DIR, f"{card_id}{ext}")
        if os.path.exists(local):
            try:
                img = Image.open(local).convert("RGBA")
                break
            except Exception:
                img = None
    # 2. image_url pointant sur un /static/ local (evite un download qui peut 429)
    if img is None and isinstance(image_url, str) and "/static/" in image_url:
        rel = "/static/" + image_url.split("/static/", 1)[1].split("?")[0]
        p = os.path.join(_ROOT, rel.lstrip("/").replace("/", os.sep))
        if os.path.exists(p):
            try:
                img = Image.open(p).convert("RGBA")
            except Exception:
                img = None
    # 3. dernier recours : download distant
    if img is None and image_url and str(image_url).startswith("http"):
        img = _download(image_url)
    if img is None:
        print(f"[card_shop] thumb introuvable card={card_id} url={image_url}")
        return None
    return img.resize((_THUMB_W, _THUMB_H), Image.LANCZOS)


def _border_thumb(filename):
    path = os.path.join(_BORDERS_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:
        return None
    # Pas de fond : bordure transparente, le bois du shop transparait au centre
    return img.resize((_THUMB_W, _THUMB_H), Image.LANCZOS)


def _draw_star(draw, cx, cy, r, fill):
    import math
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    draw.polygon(pts, fill=fill)


def _rounded_text(draw, xy, text, font, fill=(255, 255, 255), shadow=True):
    x, y = xy
    if shadow:
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 200))
    draw.text((x, y), text, font=font, fill=fill)


def build_shop_image(out_name: str = "shop_current.png") -> str | None:
    """Genere l'image du shop avec les slots enabled. Retourne URL relative."""
    from database import card_shop_get_slots, card_get, border_get
    if not os.path.exists(_BG_PATH):
        print(f"[card_shop] bg manquant {_BG_PATH}")
        return None
    os.makedirs(_OUT_DIR, exist_ok=True)
    bg = Image.open(_BG_PATH).convert("RGBA")
    draw = ImageDraw.Draw(bg)
    f_label = _font(30, bold=True)
    f_price = _font(34, bold=True)
    slots = card_shop_get_slots()
    for s in slots:
        slot_n = int(s["slot"])
        if slot_n < 1 or slot_n > 6:
            continue
        cx, cy = SLOT_CENTERS[slot_n - 1]
        if not s.get("enabled") or not s.get("item_type") or not s.get("item_ref"):
            continue
        thumb = None
        label = s.get("label") or ""
        if s["item_type"] == "card":
            try:
                card = card_get(int(s["item_ref"]))
            except (ValueError, TypeError):
                card = None
            if card:
                thumb = _card_thumb(card["id"], card.get("image_url"))
                if not label:
                    label = card.get("name") or ""
        elif s["item_type"] == "border":
            border = border_get(s["item_ref"])
            if border:
                thumb = _border_thumb(border["filename"])
                if not label:
                    label = border.get("name") or ""
        if thumb is not None:
            tx = cx - _THUMB_W // 2
            ty = cy - _THUMB_H // 2 - 10
            bg.paste(thumb, (tx, ty), thumb)
        # Label au-dessus
        if label:
            lf = _font(28, bold=True)
            tw = draw.textlength(label, font=lf)
            _rounded_text(draw, (cx - tw / 2, cy - _THUMB_H // 2 - 48), label, lf,
                          fill=(255, 240, 180))
        # Prix en dessous + etoile
        price = int(s.get("price") or 0)
        ptext = f"{price:,}".replace(",", " ")
        pw = draw.textlength(ptext, font=f_price)
        star_r = 16
        total_w = pw + star_r * 2 + 12
        px = cx - total_w / 2
        py = cy + _THUMB_H // 2 - 6
        _rounded_text(draw, (px, py), ptext, f_price, fill=(180, 242, 58))
        _draw_star(draw, px + pw + 18, py + 18, star_r, (255, 230, 90))
    out_path = os.path.join(_OUT_DIR, out_name)
    bg.convert("RGB").save(out_path, "PNG", optimize=True)
    return f"/static/card_shop/{out_name}"


def purchase_slot(user_id: int, slot: int) -> dict:
    """Achete l'item du slot pour user. Retourne dict {ok, error, item_name, new_balance}."""
    from database import (card_shop_get_slot, currency_get, currency_spend,
                           card_get, border_get, user_border_add,
                           user_card_add_with_flag)
    s = card_shop_get_slot(slot)
    if not s or not s.get("enabled") or not s.get("item_type") or not s.get("item_ref"):
        return {"ok": False, "error": "Slot vide ou désactivé."}
    price = int(s.get("price") or 0)
    item_type = s["item_type"]
    item_name = s.get("label") or ""
    # Verifs specifiques
    if item_type == "border":
        border = border_get(s["item_ref"])
        if not border:
            return {"ok": False, "error": "Bordure introuvable."}
        item_name = item_name or border["name"]
    elif item_type == "card":
        try:
            card = card_get(int(s["item_ref"]))
        except (ValueError, TypeError):
            card = None
        if not card:
            return {"ok": False, "error": "Carte introuvable."}
        item_name = item_name or card["name"]
    else:
        return {"ok": False, "error": "Type d'item inconnu."}
    # Solde + debit atomique
    bal = currency_get(user_id)
    if bal < price:
        return {"ok": False, "error": f"Solde insuffisant ({bal} / {price} ✨)."}
    if not currency_spend(user_id, price):
        return {"ok": False, "error": "Débit échoué (solde insuffisant)."}
    # Livraison
    if item_type == "border":
        user_border_add(user_id, s["item_ref"])
    elif item_type == "card":
        user_card_add_with_flag(user_id, int(s["item_ref"]), not_tradeable=False)
    new_bal = currency_get(user_id)
    return {"ok": True, "item_name": item_name, "item_type": item_type,
            "price": price, "new_balance": new_bal}
