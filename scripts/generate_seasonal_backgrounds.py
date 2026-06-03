"""Genere 5 backgrounds saisonniers EXCENTRIQUES pour le Battle Pass.

Sortie : assets/niveau_bg/seasonal/<YYYY-MM>/<name>.png

Differencie des 15 BG permanents par des compositions plus poussees :
overlays geometriques, vortex, vitraux, neon city, liquide chrome.

Chaque mois utilise sa propre palette et un seed offset (cf. seasonal_themes.py)
pour produire un visuel different du mois precedent.

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

# Import du theme du mois (palette + seed offset)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from seasonal_themes import bg_palette, bg_seed_offset  # noqa: E402

W, H = 1024, 320
ROOT_OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "niveau_bg", "seasonal")


def lerp(a, b, t): return a + (b - a) * t


def lerp_rgb(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def shade(c, factor):
    """Multiplie une couleur RGB par un facteur (0..2). Clamp 0..255."""
    return tuple(max(0, min(255, int(v * factor))) for v in c)


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


def bg_crystal_cave(palette, seed):
    primary, secondary, accent = palette
    rng = random.Random(seed)
    img = diagonal_gradient(shade(secondary, 0.4), secondary, 30)
    img = add_blobs(img, [
        (W * 0.2, H * 0.3, 220, primary, 140),
        (W * 0.7, H * 0.7, 260, shade(primary, 0.8), 120),
    ], blur=110)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(18):
        cx = rng.randint(0, W)
        cy = rng.randint(0, H)
        size = rng.randint(40, 130)
        pts = [
            (cx, cy - size),
            (cx + size * 0.5, cy),
            (cx, cy + size),
            (cx - size * 0.5, cy),
        ]
        col = (
            min(255, primary[0] + rng.randint(-20, 30)),
            min(255, primary[1] + rng.randint(-20, 30)),
            min(255, primary[2] + rng.randint(-20, 30)),
            rng.randint(40, 120),
        )
        draw.polygon(pts, fill=col, outline=accent + (160,))
    overlay = overlay.filter(ImageFilter.GaussianBlur(1))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)

    rays = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    for i in range(7):
        x = int(W * (0.1 + i * 0.13))
        rd.polygon([
            (x, 0),
            (x + 80, 0),
            (x - 40, H),
            (x - 120, H),
        ], fill=accent + (18,))
    rays = rays.filter(ImageFilter.GaussianBlur(15))
    img.alpha_composite(rays)
    return vignette(img.convert("RGB"), 0.45)


def bg_liquid_chrome(palette, seed):
    primary, secondary, accent = palette
    rng = random.Random(seed)
    base_col = shade(secondary, 0.7)
    img = Image.new("RGB", (W, H), base_col)
    px = img.load()
    for y in range(H):
        wave = math.sin(y * 0.05 + 2) * 0.3 + 0.5
        wave2 = math.sin(y * 0.018) * 0.4 + 0.5
        r = int(base_col[0] + wave * 60)
        g = int(base_col[1] + wave * 60 + wave2 * 30)
        b = int(base_col[2] + wave * 80)
        for x in range(W):
            mod = math.sin(x * 0.008 + y * 0.002) * 12
            px[x, y] = (
                max(0, min(255, int(r + mod))),
                max(0, min(255, int(g + mod * 0.6))),
                max(0, min(255, int(b + mod * 0.8))),
            )

    img = add_blobs(img, [
        (W * 0.25, H * 0.4, 240, primary, 130),
        (W * 0.75, H * 0.55, 260, accent, 130),
    ], blur=140)
    return vignette(img, 0.35)


def bg_neon_tokyo(palette, seed):
    primary, secondary, accent = palette
    rng = random.Random(seed)
    img = diagonal_gradient(shade(secondary, 0.4), shade(primary, 0.7), 45)
    img = add_blobs(img, [
        (W * 0.85, H * 0.2, 200, primary, 180),
        (W * 0.15, H * 0.6, 180, accent, 150),
    ], blur=100)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    skyline_y = int(H * 0.55)
    x = 0
    while x < W:
        bw = rng.randint(30, 90)
        bh = rng.randint(50, 150)
        col = (10, 8, 30, 220)
        draw.rectangle((x, skyline_y + (160 - bh), x + bw, H), fill=col)
        for fy in range(skyline_y + (160 - bh) + 8, H - 4, 12):
            for fx in range(x + 6, x + bw - 6, 10):
                if rng.random() < 0.5:
                    fc = rng.choice([
                        primary + (220,),
                        accent + (200,),
                        secondary + (180,),
                    ])
                    draw.rectangle((fx, fy, fx + 4, fy + 4), fill=fc)
        x += bw + rng.randint(2, 8)

    for _ in range(20):
        nx = rng.randint(0, W)
        col = rng.choice([
            primary + (180,),
            accent + (180,),
            shade(primary, 1.2) + (180,),
        ])
        draw.line([(nx, 0), (nx, H)], fill=col, width=rng.choice([1, 1, 2]))

    overlay = overlay.filter(ImageFilter.GaussianBlur(0.6))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)

    glow = overlay.filter(ImageFilter.GaussianBlur(8))
    img.alpha_composite(glow)

    return vignette(img.convert("RGB"), 0.45)


def bg_stained_glass(palette, seed):
    primary, secondary, accent = palette
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), shade(secondary, 0.2))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    palette_cycle = [
        primary,
        accent,
        shade(primary, 1.1),
        shade(accent, 0.8),
        shade(primary, 0.7),
        shade(accent, 1.2),
    ]

    cell_w, cell_h = 90, 90
    for cy in range(0, H + cell_h, cell_h):
        for cx in range(0, W + cell_w, cell_w):
            jitter = lambda: rng.randint(-22, 22)
            pts = [
                (cx + jitter(),         cy + jitter()),
                (cx + cell_w + jitter(), cy + jitter()),
                (cx + cell_w + jitter(), cy + cell_h + jitter()),
                (cx + jitter(),         cy + cell_h + jitter()),
            ]
            hue = rng.choice(palette_cycle)
            alpha = rng.randint(160, 220)
            draw.polygon(pts, fill=hue + (alpha,), outline=(20, 18, 30, 255))

    overlay = overlay.filter(ImageFilter.GaussianBlur(0.6))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)

    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((W * 0.2, -200, W * 0.8, H + 200), fill=accent + (80,))
    halo = halo.filter(ImageFilter.GaussianBlur(60))
    img.alpha_composite(halo)

    return vignette(img.convert("RGB"), 0.5)


def bg_cosmic_vortex(palette, seed):
    primary, secondary, accent = palette
    rng = random.Random(seed)
    img = radial_gradient((W * 0.5, H * 0.5), primary, shade(secondary, 0.2), radius=W * 0.7)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

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
                primary + (220,),
                accent + (200,),
                shade(accent, 1.3) + (180,),
            ])
            draw.ellipse((px - sz, py - sz, px + sz, py + sz), fill=col)

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
    palette = bg_palette(month_key)
    seed_base = bg_seed_offset(month_key)
    print(f"Generating 5 seasonal BGs for {month_key} (palette={palette}, seed_base={seed_base}) -> {out_dir}")
    for i, (name, fn) in enumerate(SEASONAL_GENERATORS):
        path = os.path.join(out_dir, f"{name}.png")
        fn(palette, seed_base + i + 1).save(path, "PNG", optimize=False, compress_level=6)
        print(f"  -> {path}")
    print("done.")


if __name__ == "__main__":
    main()
