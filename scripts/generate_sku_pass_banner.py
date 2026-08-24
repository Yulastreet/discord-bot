"""Generate the Discord SKU banner 680x240 for the Battle Pass.

Sortie : assets/sku_pass.png
À uploader manuellement dans Discord Developer Portal -> SKU "Battle Pass" -> Image.
"""
from __future__ import annotations

import math
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 680, 240
OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "sku_pass.png")
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
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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
    # Fond bleu nuit -> violet profond
    img = diagonal_gradient((20, 22, 50), (60, 30, 110), 25)
    img = add_blobs(img, [
        (W * 0.85, H * 0.2, 220, (220, 240, 100), 200),
        (W * 0.15, H * 0.8, 200, (140, 100, 240), 180),
    ], blur=110)

    # Roadmap visuelle : 12 points horizontaux centres
    img = img.convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cy = H * 0.55
    n_dots = 12
    pad = 80
    span = W - 2 * pad
    for i in range(n_dots):
        cx = pad + (span * i / (n_dots - 1))
        # Gradient rempli a 60%
        if i < int(n_dots * 0.6):
            color = (200, 240, 80, 250)  # lime acide (tier rempli)
            r = 8
        else:
            color = (110, 80, 160, 200)  # violet (tier vide)
            r = 6
        # Ligne entre points
        if i > 0:
            prev_cx = pad + (span * (i - 1) / (n_dots - 1))
            line_color = (200, 240, 80, 220) if i <= int(n_dots * 0.6) else (110, 80, 160, 180)
            od.line([(prev_cx, cy), (cx, cy)], fill=line_color, width=3)
        od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color, outline=(255, 255, 255, 220), width=2)
    img.alpha_composite(overlay)
    img = img.convert("RGB")

    img = vignette(img, 0.4)

    # Titre + sub
    f_title = find_font(38, bold=True)
    f_sub   = find_font(15, bold=False)
    f_tag   = find_font(13, bold=True)
    d = ImageDraw.Draw(img)

    d.text((W / 2, 38), "🎟️  TookBot Battle Pass", font=f_title,
           fill=(245, 250, 220), anchor="mt")
    d.text((W / 2, 88), "30 paliers · backgrounds exclusifs · sabres saisonniers · boosts XP",
           font=f_sub, fill=(200, 215, 180), anchor="mt")
    # Tag prix en haut droite
    tag = "3,99 € / mois"
    bbox = d.textbbox((0, 0), tag, font=f_tag)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 12, 6
    tag_x = W - tw - pad_x * 2 - 24
    tag_y = 24
    d.rounded_rectangle(
        (tag_x, tag_y, tag_x + tw + pad_x * 2, tag_y + th + pad_y * 2),
        radius=12, fill=(200, 240, 80),
    )
    d.text((tag_x + pad_x, tag_y + pad_y - 2), tag, font=f_tag, fill=(20, 30, 8))

    # Footer
    d.text((W / 2, H - 36),
           "Monthly renewal - rewards rotate every season",
           font=f_sub, fill=(220, 235, 200), anchor="mt")

    img.save(OUT, "PNG", optimize=True)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
