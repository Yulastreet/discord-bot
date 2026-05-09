"""Génère le banner produit Discord SKU 680x240 (ratio 17:6).

Sortie : assets/sku_niveau_premium.png
À uploader manuellement dans Discord Developer Portal -> SKU -> Image.
"""
from __future__ import annotations

import math
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 680, 240
OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "sku_niveau_premium.png")
os.makedirs(os.path.dirname(OUT), exist_ok=True)


def lerp(a, b, t):
    return a + (b - a) * t


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


def find_font(size, bold=False):
    candidates = [
        # Linux usual paths
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # Windows
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


def main():
    # Fond gradient lime acide -> dark
    img = diagonal_gradient((18, 26, 14), (38, 70, 22), 20)
    img = add_blobs(img, [
        (W * 0.85, H * 0.2, 200, (200, 240, 80), 200),
        (W * 0.15, H * 0.8, 180, (90, 200, 60), 160),
    ], blur=110)

    # Carte centrale (faux apercu /niveau)
    card_w, card_h = 360, 130
    cx = (W - card_w) // 2
    cy = (H - card_h) // 2
    card = Image.new("RGBA", (card_w, card_h), (16, 18, 22, 230))
    cdraw = ImageDraw.Draw(card)
    # Avatar mock
    av_r = 44
    av_x, av_y = 28, card_h // 2 - av_r
    cdraw.ellipse((av_x, av_y, av_x + av_r * 2, av_y + av_r * 2), fill=(60, 70, 90, 255))
    # Texte mock
    f_big = find_font(20, bold=True)
    f_mid = find_font(14, bold=True)
    f_sm  = find_font(12, bold=False)
    cdraw.text((av_x + av_r * 2 + 18, 22), "Niveau 27", font=f_big, fill=(230, 245, 200, 255))
    cdraw.text((av_x + av_r * 2 + 18, 50), "12 480 XP", font=f_mid, fill=(180, 200, 160, 255))
    # XP bar
    bar_x = av_x + av_r * 2 + 18
    bar_y = 80
    bar_w = card_w - bar_x - 24
    bar_h = 14
    cdraw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=7, fill=(40, 46, 56, 255))
    cdraw.rounded_rectangle((bar_x, bar_y, bar_x + int(bar_w * 0.62), bar_y + bar_h), radius=7, fill=(200, 240, 80, 255))
    # Badge premium
    cdraw.rounded_rectangle((card_w - 80, 10, card_w - 14, 30), radius=10, fill=(200, 240, 80, 255))
    cdraw.text((card_w - 74, 13), "★ PREMIUM", font=f_sm, fill=(20, 30, 8, 255))

    # Coller carte
    img = img.convert("RGBA")
    img.alpha_composite(card, (cx, cy))
    img = img.convert("RGB")
    img = vignette(img, 0.35)

    # Titre haut + base
    f_title = find_font(34, bold=True)
    f_sub   = find_font(16, bold=False)
    d = ImageDraw.Draw(img)
    d.text((W / 2, 28), "/niveau Premium", font=f_title, fill=(240, 252, 220), anchor="mt")
    d.text((W / 2, H - 36), "Carte XP stylée · Backgrounds exclusifs · Achat unique", font=f_sub, fill=(220, 235, 200), anchor="mt")

    img.save(OUT, "PNG", optimize=True)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
