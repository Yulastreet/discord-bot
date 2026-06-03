"""Theme Solaire (juin). 5 BG vraiment distincts autour du soleil/feu/desert.

Style IDs (clefs gardees stables avec generic, pour que les bg_id seasonal:
2026-06:<style> restent valides) :
  - crystal_cave  -> Aurore Solaire  : lever de soleil sur horizon doré
  - liquid_chrome -> Coulee de Lave  : flux de magma incandescent
  - neon_tokyo    -> Couronne        : soleil avec rayons stylises
  - stained_glass -> Mirage du Desert: dunes + soleil voilé brumeux
  - cosmic_vortex -> Soleil Couchant : ciel orange/violet avec silhouette
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


def _bg_aurore_solaire(palette, seed):
    """Lever de soleil sur l'horizon : ciel degrade rose-orange-jaune,
    soleil disque au centre-droite, rayons doux, ondulations de chaleur en bas."""
    primary, secondary, accent = palette
    rng = random.Random(seed)

    # Ciel : haut = rose tendre, bas = jaune chaud
    sky_top = shade(primary, 0.85)
    sky_bot = (255, 230, 150)
    img = Image.new("RGB", (W, H), sky_top)
    px = img.load()
    for y in range(H):
        t = y / H
        col = lerp_rgb(sky_top, sky_bot, t ** 1.2)
        for x in range(W):
            px[x, y] = col

    img = img.convert("RGBA")

    # Disque soleil
    sun_x, sun_y = int(W * 0.7), int(H * 0.55)
    sun_r = 90
    sun_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun_layer)
    sd.ellipse((sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r),
               fill=(255, 240, 200, 255))
    # Halo flou autour
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((sun_x - sun_r * 2, sun_y - sun_r * 2,
                sun_x + sun_r * 2, sun_y + sun_r * 2),
               fill=accent + (140,))
    halo = halo.filter(ImageFilter.GaussianBlur(50))
    img.alpha_composite(halo)
    img.alpha_composite(sun_layer)

    # Rayons fins
    rays = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    for i in range(16):
        ang = i * math.pi / 8 + rng.uniform(-0.1, 0.1)
        ex = sun_x + math.cos(ang) * W
        ey = sun_y + math.sin(ang) * H
        rd.line([(sun_x, sun_y), (ex, ey)], fill=(255, 240, 200, 30), width=2)
    rays = rays.filter(ImageFilter.GaussianBlur(2))
    img.alpha_composite(rays)

    # Ondulations chaleur (lignes ondulees horizontales en bas)
    heat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd2 = ImageDraw.Draw(heat)
    for j in range(8):
        y0 = int(H * (0.75 + j * 0.03))
        pts = []
        for x in range(0, W, 8):
            y = y0 + int(math.sin(x * 0.04 + j) * 4)
            pts.append((x, y))
        hd2.line(pts, fill=(255, 220, 180, 80), width=1)
    img.alpha_composite(heat)

    return vignette(img.convert("RGB"), 0.3)


def _bg_coulee_de_lave(palette, seed):
    """Flux de lave incandescent : noir profond avec coulees rouge/orange
    qui craquellent comme du magma refroidi."""
    primary, secondary, accent = palette
    rng = random.Random(seed)

    # Fond noir tres sombre
    img = Image.new("RGB", (W, H), (12, 8, 6))
    img = img.convert("RGBA")

    # Coulees de lave : forme organique avec un masque flou
    lava = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lava)
    # 3-4 grosses coulees en serpentin
    for _ in range(4):
        x0 = rng.randint(0, W)
        y0 = rng.randint(0, H)
        for step in range(120):
            angle = rng.uniform(-math.pi/2 + 0.2, math.pi/2 - 0.2)
            x0 += math.cos(angle) * rng.uniform(3, 8)
            y0 += abs(math.sin(angle)) * rng.uniform(2, 5)
            x0 = max(0, min(W, x0))
            y0 = max(0, min(H, y0))
            r = rng.randint(14, 28)
            ld.ellipse((x0 - r, y0 - r, x0 + r, y0 + r),
                       fill=(255, 100, 30, 200))
    lava = lava.filter(ImageFilter.GaussianBlur(12))

    # Coeur brillant blanc/jaune par-dessus
    core = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    for _ in range(40):
        cx = rng.randint(0, W)
        cy = rng.randint(0, H)
        cd.ellipse((cx - 5, cy - 5, cx + 5, cy + 5),
                   fill=(255, 240, 180, 220))
    core = core.filter(ImageFilter.GaussianBlur(4))

    img.alpha_composite(lava)
    img.alpha_composite(core)

    # Etincelles
    sparks = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sparks)
    for _ in range(180):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        sd.ellipse((x, y, x + 2, y + 2), fill=(255, 220, 140, 220))
    img.alpha_composite(sparks)

    return vignette(img.convert("RGB"), 0.55)


def _bg_couronne(palette, seed):
    """Soleil styliste avec couronne de rayons triangulaires reguliers,
    style affiche-emblème dore sur fond degrade."""
    primary, secondary, accent = palette
    rng = random.Random(seed)

    # Fond degrade radial : centre jaune chaud, bord rouge sombre
    img = radial_gradient((W * 0.5, H * 0.5), accent, shade(secondary, 0.5),
                          radius=W * 0.6)
    img = img.convert("RGBA")

    cx, cy = W // 2, H // 2

    # Rayons triangulaires en couronne
    rays = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    n_rays = 24
    r_inner = 90
    r_outer = 280
    for i in range(n_rays):
        ang = (i / n_rays) * 2 * math.pi
        # 2 sommets a r_inner, 1 a r_outer (triangle pointu vers ext)
        a1 = ang - math.pi / n_rays * 0.6
        a2 = ang + math.pi / n_rays * 0.6
        pts = [
            (cx + math.cos(a1) * r_inner, cy + math.sin(a1) * r_inner),
            (cx + math.cos(a2) * r_inner, cy + math.sin(a2) * r_inner),
            (cx + math.cos(ang) * r_outer, cy + math.sin(ang) * r_outer),
        ]
        col = (255, 220, 100, 200) if i % 2 == 0 else (255, 180, 60, 220)
        rd.polygon(pts, fill=col)
    img.alpha_composite(rays)

    # Disque central
    disk = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disk)
    dd.ellipse((cx - 80, cy - 80, cx + 80, cy + 80),
               fill=(255, 240, 200, 255), outline=(255, 200, 120, 255), width=3)
    img.alpha_composite(disk)

    # Petits cercles points sur le disque (motif decoratif)
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dco = ImageDraw.Draw(deco)
    for i in range(12):
        ang = i * (2 * math.pi / 12)
        px = cx + math.cos(ang) * 55
        py = cy + math.sin(ang) * 55
        dco.ellipse((px - 4, py - 4, px + 4, py + 4),
                    fill=(255, 180, 80, 255))
    img.alpha_composite(deco)

    return vignette(img.convert("RGB"), 0.45)


def _bg_mirage_desert(palette, seed):
    """Dunes du desert avec soleil voile brumeux : ciel terne ocre, dunes en
    vagues paralleles, halo solaire flou."""
    primary, secondary, accent = palette
    rng = random.Random(seed)

    # Ciel ocre brumeux
    sky_top = (220, 180, 100)
    sky_bot = (240, 210, 140)
    img = Image.new("RGB", (W, H), sky_top)
    px = img.load()
    for y in range(H):
        t = y / H
        col = lerp_rgb(sky_top, sky_bot, t)
        for x in range(W):
            px[x, y] = col
    img = img.convert("RGBA")

    # Soleil voile : disque flou
    sun_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun_layer)
    sx, sy = int(W * 0.3), int(H * 0.35)
    sd.ellipse((sx - 70, sy - 70, sx + 70, sy + 70),
               fill=(255, 240, 200, 200))
    sun_layer = sun_layer.filter(ImageFilter.GaussianBlur(25))
    img.alpha_composite(sun_layer)

    # Dunes : 4-5 vagues paralleles avec couleur degradant
    dunes = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dunes)
    dune_colors = [
        (210, 160, 90, 255),
        (190, 140, 75, 255),
        (170, 120, 65, 255),
        (150, 105, 55, 255),
        (130, 90, 45, 255),
    ]
    for i, col in enumerate(dune_colors):
        y_base = int(H * (0.55 + i * 0.10))
        amp = 22 + i * 4
        offset = rng.randint(0, 200)
        pts = [(0, H), (0, y_base)]
        for x in range(0, W + 10, 10):
            y = y_base + int(math.sin((x + offset) * 0.012) * amp)
            pts.append((x, y))
        pts.append((W, H))
        dd.polygon(pts, fill=col)
    img.alpha_composite(dunes)

    # Petits points (sable en mouvement / brume)
    grains = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grains)
    for _ in range(220):
        x = rng.randint(0, W - 1)
        y = rng.randint(int(H * 0.5), H - 1)
        gd.point((x, y), fill=(255, 240, 200, 180))
    img.alpha_composite(grains)

    return vignette(img.convert("RGB"), 0.35)


def _bg_soleil_couchant(palette, seed):
    """Soleil couchant : ciel bande horizontales (haut violet -> bas orange),
    mer sombre en bas avec reflet vertical du soleil."""
    primary, secondary, accent = palette
    rng = random.Random(seed)

    # Bandes de ciel
    bands = [
        (60,  20,  90,  int(H * 0.20)),   # haut profond violet
        (140, 60,  120, int(H * 0.40)),   # rose-violet
        (220, 100, 100, int(H * 0.55)),   # rose-rouge
        (255, 150, 80,  int(H * 0.70)),   # orange chaud
        (255, 200, 100, int(H * 0.78)),   # jaune-orange
    ]
    img = Image.new("RGB", (W, H), bands[0][:3])
    px = img.load()
    last_y = 0
    last_col = bands[0][:3]
    for r, g, b, end_y in bands[1:]:
        col = (r, g, b)
        for y in range(last_y, end_y):
            t = (y - last_y) / max(1, end_y - last_y)
            interp = lerp_rgb(last_col, col, t)
            for x in range(W):
                px[x, y] = interp
        last_y = end_y
        last_col = col
    # Bas : mer sombre
    sea_top = last_col
    sea_bot = (20, 15, 35)
    for y in range(last_y, H):
        t = (y - last_y) / max(1, H - last_y)
        interp = lerp_rgb(sea_top, sea_bot, t ** 1.3)
        for x in range(W):
            px[x, y] = interp
    img = img.convert("RGBA")

    # Disque soleil dans la bande orange, tangent a la mer
    sun_y = int(H * 0.72)
    sun_x = W // 2
    sun_r = 70
    sun_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun_layer)
    sd.ellipse((sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r),
               fill=(255, 240, 180, 255))
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((sun_x - sun_r * 2, sun_y - sun_r * 2,
                sun_x + sun_r * 2, sun_y + sun_r * 2),
               fill=(255, 200, 120, 150))
    halo = halo.filter(ImageFilter.GaussianBlur(45))
    img.alpha_composite(halo)
    img.alpha_composite(sun_layer)

    # Reflet vertical sur la mer : suite de petits rectangles flous
    refl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rfd = ImageDraw.Draw(refl)
    for y in range(sun_y + sun_r // 2, H, 6):
        w = max(8, int((H - y) * 0.6))
        rfd.rectangle((sun_x - w, y, sun_x + w, y + 3),
                      fill=(255, 220, 160, 150))
    refl = refl.filter(ImageFilter.GaussianBlur(2))
    img.alpha_composite(refl)

    return vignette(img.convert("RGB"), 0.4)


# Mapping vers les style IDs canoniques (memes que generic) pour ne pas
# casser les bg_id existants ('seasonal:2026-06:<style>').
_GENERATORS = [
    ("crystal_cave",  _bg_aurore_solaire),
    ("liquid_chrome", _bg_coulee_de_lave),
    ("neon_tokyo",    _bg_couronne),
    ("stained_glass", _bg_mirage_desert),
    ("cosmic_vortex", _bg_soleil_couchant),
]


def generate(out_dir: str, palette: tuple, seed_base: int):
    """Genere les 5 BG du theme dans `out_dir`."""
    for i, (style, fn) in enumerate(_GENERATORS):
        path = os.path.join(out_dir, f"{style}.png")
        fn(palette, seed_base + i + 1).save(path, "PNG", optimize=False, compress_level=6)
        print(f"  -> {path}")
