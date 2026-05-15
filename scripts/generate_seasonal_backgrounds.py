"""Génère 5 backgrounds saisonniers EXCENTRIQUES pour le Battle Pass.

Sortie : assets/niveau_bg/seasonal/<YYYY-MM>/<name>.png

Différencié des 15 BG permanents par des compositions plus poussées :
overlays géométriques, vortex, vitraux, néon city, liquide chrome.

Usage :
    python scripts/generate_seasonal_backgrounds.py            # mois courant
    python scripts/generate_seasonal_backgrounds.py 2026-06    # mois specifique
"""
from __future__ import annotations

import math
import os
import random
import sys
from datetime import datetime
from PIL import Image, ImageDraw, ImageFilter

W, H = 1024, 320
ROOT_OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "niveau_bg", "seasonal")


def lerp(a, b, t): return a + (b - a) * t


def lerp_rgb(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def diagonal_gradient(c1, c2, angle_deg=20):
    img = Image.new("RGB", (W, H), c1)
    px = img.load()
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    max_proj = abs(W * cos_a) + abs(H * sin_a)
    for y in range(H):
        for x in range(W):
            t = (x * cos_a + y * sin_a) / max_proj
            t = max(0.0, min(1.0, t))
            px[x, y] = lerp_rgb(c1, c2, t)
    return img


def radial_gradient(center, c_inner, c_outer, radius=None):
    img = Image.new("RGB", (W, H), c_outer)
    px = img.load()
    cx, cy = center
    r = radius or max(W, H) * 0.7
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - cx, y - cy) / r
            t = max(0.0, min(1.0, d))
            px[x, y] = lerp_rgb(c_inner, c_outer, t)
    return img


def add_blobs(img, blobs, blur=80):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for cx, cy, r, color, alpha in blobs:
        rgba = color + (alpha,)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgba)
    overlay = overlay.filter(ImageFilter.GaussianBlur(blur))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return img.convert("RGB")


def vignette(img, strength=0.4):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cx, cy = W / 2, H / 2
    max_d = math.hypot(cx, cy)
    px = overlay.load()
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - cx, y - cy) / max_d
            a = int(255 * strength * (d ** 2))
            px[x, y] = (0, 0, 0, min(255, a))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return img.convert("RGB")


def bg_crystal_cave(seed=1):
    rng = random.Random(seed)
    img = diagonal_gradient((10, 30, 50), (40, 60, 100), 30)
    img = add_blobs(img, [
        (W * 0.2, H * 0.3, 220, (120, 200, 255), 140),
        (W * 0.7, H * 0.7, 260, (90, 150, 220), 120),
    ], blur=110)

    # Cristaux geometriques semi-transparents
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(18):
        cx = rng.randint(0, W)
        cy = rng.randint(0, H)
        size = rng.randint(40, 130)
        rot  = rng.uniform(0, 60)
        # Diamant
        pts = [
            (cx, cy - size),
            (cx + size * 0.5, cy),
            (cx, cy + size),
            (cx - size * 0.5, cy),
        ]
        col = (
            rng.randint(180, 240),
            rng.randint(220, 255),
            rng.randint(240, 255),
            rng.randint(40, 120),
        )
        draw.polygon(pts, fill=col, outline=(255, 255, 255, 160))
    overlay = overlay.filter(ImageFilter.GaussianBlur(1))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)

    # Light rays
    rays = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    for i in range(7):
        x = int(W * (0.1 + i * 0.13))
        rd.polygon([
            (x, 0),
            (x + 80, 0),
            (x - 40, H),
            (x - 120, H),
        ], fill=(255, 255, 255, 18))
    rays = rays.filter(ImageFilter.GaussianBlur(15))
    img.alpha_composite(rays)
    return vignette(img.convert("RGB"), 0.45)


def bg_liquid_chrome(seed=2):
    rng = random.Random(seed)
    # Bandes horizontales de gris metallise + reflets violets/cyan
    img = Image.new("RGB", (W, H), (40, 40, 60))
    px = img.load()
    for y in range(H):
        # Onde sinusoidale verticale -> luminosite oscillante
        wave = math.sin(y * 0.05 + 2) * 0.3 + 0.5
        wave2 = math.sin(y * 0.018) * 0.4 + 0.5
        r = int(120 + wave * 60)
        g = int(120 + wave * 60 + wave2 * 30)
        b = int(140 + wave * 80)
        for x in range(W):
            # Modulation horizontale legere
            mod = math.sin(x * 0.008 + y * 0.002) * 12
            px[x, y] = (
                max(0, min(255, int(r + mod))),
                max(0, min(255, int(g + mod * 0.6))),
                max(0, min(255, int(b + mod * 0.8))),
            )

    # Reflets colorimetriques
    img = add_blobs(img, [
        (W * 0.25, H * 0.4, 240, (220, 100, 220), 130),
        (W * 0.75, H * 0.55, 260, (100, 220, 220), 130),
    ], blur=140)
    return vignette(img, 0.35)


def bg_neon_tokyo(seed=3):
    rng = random.Random(seed)
    # Ciel violet -> rose chaud
    img = diagonal_gradient((15, 8, 40), (90, 20, 90), 45)
    img = add_blobs(img, [
        (W * 0.85, H * 0.2, 200, (255, 80, 200), 180),
        (W * 0.15, H * 0.6, 180, (60, 220, 255), 150),
    ], blur=100)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Silhouette city bas
    skyline_y = int(H * 0.55)
    x = 0
    while x < W:
        bw = rng.randint(30, 90)
        bh = rng.randint(50, 150)
        col = (10, 8, 30, 220)
        draw.rectangle((x, skyline_y + (160 - bh), x + bw, H), fill=col)
        # Fenetres lumineuses
        for fy in range(skyline_y + (160 - bh) + 8, H - 4, 12):
            for fx in range(x + 6, x + bw - 6, 10):
                if rng.random() < 0.5:
                    fc = rng.choice([
                        (255, 220, 100, 220),
                        (180, 240, 255, 200),
                        (255, 120, 200, 180),
                    ])
                    draw.rectangle((fx, fy, fx + 4, fy + 4), fill=fc)
        x += bw + rng.randint(2, 8)

    # Stries verticales neon
    for _ in range(20):
        nx = rng.randint(0, W)
        col = rng.choice([
            (255, 80, 200, 180),
            (80, 220, 255, 180),
            (255, 200, 100, 180),
        ])
        draw.line([(nx, 0), (nx, H)], fill=col, width=rng.choice([1, 1, 2]))

    overlay = overlay.filter(ImageFilter.GaussianBlur(0.6))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)

    # Glow over neon
    glow = overlay.filter(ImageFilter.GaussianBlur(8))
    img.alpha_composite(glow)

    return vignette(img.convert("RGB"), 0.45)


def bg_stained_glass(seed=4):
    rng = random.Random(seed)
    # Fond noir doux
    img = Image.new("RGB", (W, H), (15, 12, 18))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Pavage de quadrilateres irreguliers
    cell_w, cell_h = 90, 90
    for cy in range(0, H + cell_h, cell_h):
        for cx in range(0, W + cell_w, cell_w):
            # Sommets perturbes
            jitter = lambda: rng.randint(-22, 22)
            pts = [
                (cx + jitter(),         cy + jitter()),
                (cx + cell_w + jitter(), cy + jitter()),
                (cx + cell_w + jitter(), cy + cell_h + jitter()),
                (cx + jitter(),         cy + cell_h + jitter()),
            ]
            # Couleur saturee
            hue = rng.choice([
                (220, 60, 80), (240, 200, 70), (60, 180, 220),
                (140, 60, 220), (60, 220, 140), (220, 100, 60),
            ])
            alpha = rng.randint(160, 220)
            draw.polygon(pts, fill=hue + (alpha,), outline=(20, 18, 30, 255))

    overlay = overlay.filter(ImageFilter.GaussianBlur(0.6))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)

    # Halo central lumineux
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((W * 0.2, -200, W * 0.8, H + 200), fill=(255, 230, 180, 80))
    halo = halo.filter(ImageFilter.GaussianBlur(60))
    img.alpha_composite(halo)

    return vignette(img.convert("RGB"), 0.5)


def bg_cosmic_vortex(seed=5):
    rng = random.Random(seed)
    img = radial_gradient((W * 0.5, H * 0.5), (90, 30, 140), (5, 4, 18), radius=W * 0.7)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Spirales : trace plein de petits arcs courbes
    cx, cy = W / 2, H / 2
    for arm in range(4):
        offset = arm * (math.pi / 2)
        for i in range(120):
            r = i * 4 + rng.randint(0, 10)
            theta = offset + i * 0.18
            px = cx + math.cos(theta) * r * 1.4
            py = cy + math.sin(theta) * r * 0.6
            sz = max(1, int(8 - i * 0.05))
            col = rng.choice([
                (200, 160, 255, 220),
                (140, 200, 255, 200),
                (255, 220, 200, 180),
            ])
            draw.ellipse((px - sz, py - sz, px + sz, py + sz), fill=col)

    # Etoiles
    for _ in range(220):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        sz = rng.choice([1, 1, 1, 2, 2, 3])
        a  = rng.randint(150, 255)
        draw.ellipse((x, y, x + sz, y + sz), fill=(255, 255, 255, a))

    overlay = overlay.filter(ImageFilter.GaussianBlur(1.2))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)

    return vignette(img.convert("RGB"), 0.55)


SEASONAL_GENERATORS = [
    ("crystal_cave",  bg_crystal_cave),
    ("liquid_chrome", bg_liquid_chrome),
    ("neon_tokyo",    bg_neon_tokyo),
    ("stained_glass", bg_stained_glass),
    ("cosmic_vortex", bg_cosmic_vortex),
]


def main():
    if len(sys.argv) > 1:
        month_key = sys.argv[1]
    else:
        month_key = datetime.utcnow().strftime("%Y-%m")
    out_dir = os.path.join(ROOT_OUT, month_key)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Generating 5 seasonal BGs for {month_key} -> {out_dir}")
    for name, fn in SEASONAL_GENERATORS:
        path = os.path.join(out_dir, f"{name}.png")
        fn().save(path, "PNG", optimize=False, compress_level=6)
        print(f"  -> {path}")
    print("done.")


if __name__ == "__main__":
    main()
