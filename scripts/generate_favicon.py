"""Génère le favicon TookBot.

Sortie :
    static/favicon.ico        (multi-resolution 16/32/48)
    static/favicon-32.png     (PNG 32x32 fallback)
    static/favicon-180.png    (Apple touch icon)
    static/favicon-512.png    (PWA / Open Graph)
    landing/favicon.ico       (copie pour la landing)
    landing/favicon-32.png

Identité : hexagone vert lime + "T" minimaliste centré, fond sombre.
"""
from __future__ import annotations

import math
import os
from PIL import Image, ImageDraw, ImageFont

OUT_STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
OUT_LANDING = os.path.join(os.path.dirname(__file__), "..", "landing")
os.makedirs(OUT_STATIC, exist_ok=True)
os.makedirs(OUT_LANDING, exist_ok=True)

ACCENT = (200, 240, 80, 255)        # lime acid
ACCENT_DARK = (140, 180, 40, 255)
BG_DARK = (24, 28, 22, 255)


def _hex_points(cx, cy, r):
    pts = []
    for i in range(6):
        a = math.radians(60 * i - 30)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _find_font(size, bold=True):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_favicon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Hexagone fond sombre
    cx = cy = size / 2
    r_outer = size * 0.46
    pts_outer = _hex_points(cx, cy, r_outer)
    draw.polygon(pts_outer, fill=BG_DARK)

    # Bordure hex lime
    border = max(1, int(size * 0.06))
    draw.line(pts_outer + [pts_outer[0]], fill=ACCENT, width=border)

    # T centre
    if size >= 24:
        # Glyphe via font
        f_size = int(size * 0.55)
        font = _find_font(f_size, bold=True)
        bbox = draw.textbbox((0, 0), "T", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # Décalage vertical : retire l'ascent visuel
        draw.text(
            (cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1] - size * 0.03),
            "T", font=font, fill=ACCENT,
        )
    else:
        # Pour 16x16, dessiner T à la main car font illisible
        bw = max(1, int(size * 0.6))   # barre horizontale du T
        bh = max(1, int(size * 0.18))
        sw = max(1, int(size * 0.16))  # tige verticale
        sh = int(size * 0.55)
        # Barre horizontale
        x1 = cx - bw / 2; y1 = cy - sh / 2
        draw.rectangle((x1, y1, x1 + bw, y1 + bh), fill=ACCENT)
        # Tige verticale
        x2 = cx - sw / 2; y2 = y1
        draw.rectangle((x2, y2, x2 + sw, y2 + sh), fill=ACCENT)

    return img


def main():
    sizes = [16, 32, 48, 180, 512]
    images = {sz: render_favicon(sz) for sz in sizes}

    # ICO multi-resolution (16 + 32 + 48)
    ico_path = os.path.join(OUT_STATIC, "favicon.ico")
    images[48].save(ico_path, format="ICO",
                    sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"  -> {ico_path}")

    # PNG variants
    for sz, label in [(32, "favicon-32.png"),
                      (180, "favicon-180.png"),
                      (512, "favicon-512.png")]:
        path = os.path.join(OUT_STATIC, label)
        images[sz].save(path, "PNG", optimize=True)
        print(f"  -> {path}")

    # Copie aussi pour la landing (servie par nginx)
    images[48].save(os.path.join(OUT_LANDING, "favicon.ico"),
                    format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    images[32].save(os.path.join(OUT_LANDING, "favicon-32.png"), "PNG", optimize=True)
    print("done.")


if __name__ == "__main__":
    main()
