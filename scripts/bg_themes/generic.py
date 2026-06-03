"""Generateur generique de BG saisonniers (fallback).

Utilise pour les mois qui n'ont pas encore leur module thematique dedie.
Reprend les 5 styles abstraits (crystal_cave, liquid_chrome, neon_tokyo,
stained_glass, cosmic_vortex) parameters par la palette du mois.

Lorsqu'un mois recoit son propre module thematique (ex: solaire pour juin),
il remplace ce fallback dans `bg_themes/__init__.py:_MONTH_TO_MODULE`.
"""
from __future__ import annotations
import math
import os
import random
from PIL import Image, ImageDraw, ImageFilter

from ._helpers import (
    W, H, shade, lerp_rgb, diagonal_gradient, radial_gradient,
    vignette, add_blobs,
)


def _bg_crystal_cave(palette, seed):
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
    return vignette(img.convert("RGB"), 0.45)


def _bg_liquid_chrome(palette, seed):
    primary, secondary, accent = palette
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


def _bg_neon_tokyo(palette, seed):
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
        draw.rectangle((x, skyline_y + (160 - bh), x + bw, H), fill=(10, 8, 30, 220))
        for fy in range(skyline_y + (160 - bh) + 8, H - 4, 12):
            for fx in range(x + 6, x + bw - 6, 10):
                if rng.random() < 0.5:
                    fc = rng.choice([primary + (220,), accent + (200,), secondary + (180,)])
                    draw.rectangle((fx, fy, fx + 4, fy + 4), fill=fc)
        x += bw + rng.randint(2, 8)
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return vignette(img.convert("RGB"), 0.45)


def _bg_stained_glass(palette, seed):
    primary, secondary, accent = palette
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), shade(secondary, 0.2))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    palette_cycle = [
        primary, accent, shade(primary, 1.1), shade(accent, 0.8),
        shade(primary, 0.7), shade(accent, 1.2),
    ]
    cell_w, cell_h = 90, 90
    for cy in range(0, H + cell_h, cell_h):
        for cx in range(0, W + cell_w, cell_w):
            jitter = lambda: rng.randint(-22, 22)
            pts = [
                (cx + jitter(),          cy + jitter()),
                (cx + cell_w + jitter(), cy + jitter()),
                (cx + cell_w + jitter(), cy + cell_h + jitter()),
                (cx + jitter(),          cy + cell_h + jitter()),
            ]
            hue = rng.choice(palette_cycle)
            draw.polygon(pts, fill=hue + (rng.randint(160, 220),),
                         outline=(20, 18, 30, 255))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return vignette(img.convert("RGB"), 0.5)


def _bg_cosmic_vortex(palette, seed):
    primary, secondary, accent = palette
    rng = random.Random(seed)
    img = radial_gradient((W * 0.5, H * 0.5), primary, shade(secondary, 0.2),
                          radius=W * 0.7)
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
                primary + (220,), accent + (200,),
                shade(accent, 1.3) + (180,),
            ])
            draw.ellipse((px - sz, py - sz, px + sz, py + sz), fill=col)
    for _ in range(220):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        sz = rng.choice([1, 1, 1, 2, 2, 3])
        a  = rng.randint(150, 255)
        draw.ellipse((x, y, x + sz, y + sz), fill=(255, 255, 255, a))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return vignette(img.convert("RGB"), 0.55)


_GENERATORS = [
    ("crystal_cave",  _bg_crystal_cave),
    ("liquid_chrome", _bg_liquid_chrome),
    ("neon_tokyo",    _bg_neon_tokyo),
    ("stained_glass", _bg_stained_glass),
    ("cosmic_vortex", _bg_cosmic_vortex),
]


def generate(out_dir: str, palette: tuple, seed_base: int):
    for i, (style, fn) in enumerate(_GENERATORS):
        path = os.path.join(out_dir, f"{style}.png")
        fn(palette, seed_base + i + 1).save(path, "PNG", optimize=False, compress_level=6)
        print(f"  -> {path}")
