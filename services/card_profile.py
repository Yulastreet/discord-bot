"""Card Profile : image podium des 3 cartes vedettes d'un user (fond transparent).

Milieu plus grand et plus haut, cotes plus petits et plus bas (style podium).
Chaque carte est rendue avec la bordure + etoiles que le user lui a appliquees.
"""
from __future__ import annotations

import os
from PIL import Image

from services.card_render import compose_card_image, _ROOT

_OUT_DIR = os.path.join(_ROOT, "static", "card_profiles")

# Layout (canvas 1120x620). (cle, largeur, hauteur, centre_x, top_y)
_CANVAS_W = 1120
_CANVAS_H = 620
_LAYOUT = [
    ("left",  250, 375, 235, 135),
    ("right", 250, 375, 885, 135),
    ("mid",   324, 486, 560, 10),   # dessine en dernier (au-dessus)
]


def _card_image_for(user_id, card_id):
    """Image PIL de la carte avec bordure + etoiles du user, ou None."""
    if not card_id:
        return None
    from database import (card_get, card_customization_get, card_fusion_get,
                           border_get)
    card = card_get(int(card_id))
    if not card:
        return None
    border_key = card_customization_get(user_id, card_id)
    border = border_get(border_key) if border_key else None
    fusion = card_fusion_get(user_id, card_id)
    return compose_card_image(int(card_id), border, fusion,
                               fallback_url=card.get("image_url"))


def build_profile_image(user_id, profile: dict) -> str | None:
    """Genere l'image podium. profile = {left_id, mid_id, right_id}.
    Retourne URL relative ou None si aucune carte."""
    os.makedirs(_OUT_DIR, exist_ok=True)
    canvas = Image.new("RGBA", (_CANVAS_W, _CANVAS_H), (0, 0, 0, 0))
    placed = 0
    ids = {"left": profile.get("left_id"), "mid": profile.get("mid_id"),
           "right": profile.get("right_id")}
    for key, w, h, cx, top in _LAYOUT:
        img = _card_image_for(user_id, ids.get(key))
        if img is None:
            continue
        img = img.convert("RGBA").resize((w, h), Image.LANCZOS)
        x = cx - w // 2
        canvas.paste(img, (x, top), img)
        placed += 1
    if placed == 0:
        return None
    out_path = os.path.join(_OUT_DIR, f"{user_id}.png")
    canvas.save(out_path, "PNG", optimize=True)
    return f"/static/card_profiles/{user_id}.png"
