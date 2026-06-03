"""Helpers PIL communs aux generateurs thematiques."""
from __future__ import annotations
import math
import random
from PIL import Image, ImageDraw, ImageFilter

W, H = 1024, 320


def lerp(a, b, t): return a + (b - a) * t


def lerp_rgb(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def shade(c, factor):
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
