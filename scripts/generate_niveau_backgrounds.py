"""Generate 15 backgrounds 1024x320 for the premium /level card.

Each BG is a composite PNG (gradient + overlay shapes) saved to
`assets/niveau_bg/<id>.png`. The module is deliberately dependency-light
(Pillow only) and deterministic (fixed random seed).

Lancement :
    python scripts/generate_niveau_backgrounds.py
"""
from __future__ import annotations

import math
import os
import random
from PIL import Image, ImageDraw, ImageFilter

W, H = 1024, 320
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "niveau_bg")
os.makedirs(OUT_DIR, exist_ok=True)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_rgb(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def diagonal_gradient(c1, c2, angle_deg=20):
    img = Image.new("RGB", (W, H), c1)
    px = img.load()
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    # Project on rotated axis
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


def add_noise(img: Image.Image, strength=8):
    noise = Image.effect_noise((W, H), strength).convert("RGB")
    return Image.blend(img, noise, 0.05)


def add_blobs(img, blobs, blur=80):
    """blobs = list of (cx, cy, radius, color, alpha)."""
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for cx, cy, r, color, alpha in blobs:
        rgba = color + (alpha,)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgba)
    overlay = overlay.filter(ImageFilter.GaussianBlur(blur))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return img.convert("RGB")


def add_stars(img, count=120, seed=0):
    rng = random.Random(seed)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(count):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        size = rng.choice([1, 1, 1, 2, 2, 3])
        alpha = rng.randint(120, 255)
        draw.ellipse((x, y, x + size, y + size), fill=(255, 255, 255, alpha))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return img.convert("RGB")


def add_grid(img, step=40, color=(255, 255, 255), alpha=20):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for x in range(0, W, step):
        draw.line([(x, 0), (x, H)], fill=color + (alpha,), width=1)
    for y in range(0, H, step):
        draw.line([(0, y), (W, y)], fill=color + (alpha,), width=1)
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    return img.convert("RGB")


def vignette(img, strength=0.55):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
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


def save(img, name):
    path = os.path.join(OUT_DIR, f"{name}.png")
    img.save(path, "PNG", optimize=True)
    print(f"  -> {path}")


def bg_default():
    img = diagonal_gradient((22, 24, 30), (38, 42, 52), 25)
    img = add_blobs(img, [
        (200, 80, 220, (90, 100, 130), 90),
        (850, 260, 260, (60, 70, 100), 80),
    ], blur=120)
    img = add_noise(img, 12)
    return vignette(img, 0.4)


def bg_lime():
    img = diagonal_gradient((18, 22, 14), (40, 70, 24), 20)
    img = add_blobs(img, [
        (820, 60, 280, (200, 240, 80), 160),
        (160, 270, 220, (80, 180, 40), 120),
    ], blur=130)
    img = add_grid(img, step=48, color=(220, 255, 120), alpha=14)
    return vignette(img, 0.45)


def bg_sunset():
    img = diagonal_gradient((255, 94, 77), (75, 30, 90), 30)
    img = add_blobs(img, [
        (820, 40, 240, (255, 200, 100), 180),
        (180, 260, 220, (200, 80, 140), 150),
    ], blur=120)
    return vignette(img, 0.5)


def bg_aurora():
    img = diagonal_gradient((22, 18, 50), (12, 30, 60), 15)
    img = add_blobs(img, [
        (220, 120, 280, (90, 220, 200), 150),
        (560, 60, 260, (160, 110, 240), 140),
        (880, 220, 240, (60, 200, 240), 130),
    ], blur=130)
    img = add_stars(img, count=140, seed=2)
    return vignette(img, 0.45)


def bg_midnight():
    img = diagonal_gradient((10, 14, 32), (18, 28, 60), 35)
    img = add_blobs(img, [
        (160, 60, 220, (40, 80, 180), 130),
        (860, 250, 260, (30, 60, 140), 120),
    ], blur=130)
    img = add_stars(img, count=160, seed=5)
    return vignette(img, 0.55)


def bg_cyberpunk():
    img = diagonal_gradient((20, 8, 40), (60, 8, 80), 25)
    img = add_blobs(img, [
        (180, 80, 260, (255, 60, 200), 170),
        (820, 240, 280, (60, 220, 255), 160),
    ], blur=110)
    img = add_grid(img, step=32, color=(255, 80, 200), alpha=22)
    return vignette(img, 0.4)


def bg_pastel_pink():
    img = diagonal_gradient((255, 200, 220), (210, 160, 240), 25)
    img = add_blobs(img, [
        (200, 80, 280, (255, 230, 240), 200),
        (850, 230, 260, (200, 180, 255), 180),
    ], blur=130)
    return vignette(img, 0.25)


def bg_forest():
    img = diagonal_gradient((10, 28, 18), (30, 70, 40), 25)
    img = add_blobs(img, [
        (200, 80, 240, (60, 140, 70), 140),
        (840, 260, 280, (40, 100, 50), 120),
    ], blur=130)
    return vignette(img, 0.5)


def bg_ocean():
    img = diagonal_gradient((10, 30, 60), (20, 100, 160), 20)
    img = add_blobs(img, [
        (200, 80, 240, (80, 200, 240), 150),
        (820, 240, 260, (40, 120, 200), 140),
    ], blur=130)
    return vignette(img, 0.45)


def bg_galaxy():
    img = radial_gradient((W * 0.7, H * 0.4), (60, 30, 110), (8, 6, 24), radius=W * 0.7)
    img = add_blobs(img, [
        (760, 100, 220, (180, 100, 220), 150),
        (240, 240, 200, (90, 60, 180), 130),
    ], blur=120)
    img = add_stars(img, count=220, seed=11)
    return vignette(img, 0.5)


def bg_fire():
    img = diagonal_gradient((40, 8, 8), (220, 70, 20), 25)
    img = add_blobs(img, [
        (200, 240, 260, (255, 160, 40), 170),
        (820, 80, 240, (255, 100, 30), 150),
    ], blur=120)
    return vignette(img, 0.5)


def bg_ice():
    img = diagonal_gradient((180, 220, 240), (90, 150, 200), 20)
    img = add_blobs(img, [
        (200, 80, 260, (240, 250, 255), 180),
        (820, 240, 240, (140, 200, 240), 150),
    ], blur=130)
    return vignette(img, 0.3)


def bg_royal():
    img = diagonal_gradient((30, 20, 8), (90, 60, 20), 20)
    img = add_blobs(img, [
        (820, 60, 280, (240, 200, 80), 180),
        (180, 250, 220, (180, 140, 40), 140),
    ], blur=130)
    img = add_grid(img, step=64, color=(255, 220, 130), alpha=18)
    return vignette(img, 0.5)


def bg_matrix():
    img = diagonal_gradient((4, 14, 6), (10, 30, 14), 25)
    img = add_blobs(img, [
        (200, 80, 220, (40, 200, 80), 140),
        (820, 240, 260, (20, 140, 60), 120),
    ], blur=130)
    img = add_grid(img, step=28, color=(80, 255, 130), alpha=24)
    return vignette(img, 0.5)


def bg_lavender():
    img = diagonal_gradient((230, 220, 255), (180, 160, 230), 25)
    img = add_blobs(img, [
        (200, 80, 260, (250, 240, 255), 180),
        (820, 240, 240, (200, 180, 240), 160),
    ], blur=130)
    return vignette(img, 0.25)


GENERATORS = [
    ("default",       bg_default),
    ("lime",          bg_lime),
    ("sunset",        bg_sunset),
    ("aurora",        bg_aurora),
    ("midnight",      bg_midnight),
    ("cyberpunk",     bg_cyberpunk),
    ("pastel_pink",   bg_pastel_pink),
    ("forest",        bg_forest),
    ("ocean",         bg_ocean),
    ("galaxy",        bg_galaxy),
    ("fire",          bg_fire),
    ("ice",           bg_ice),
    ("royal",         bg_royal),
    ("matrix",        bg_matrix),
    ("lavender",      bg_lavender),
]


def main():
    print(f"Generating {len(GENERATORS)} backgrounds in {OUT_DIR}")
    for name, fn in GENERATORS:
        print(f"[{name}]")
        save(fn(), name)
    print("done.")


if __name__ == "__main__":
    main()
