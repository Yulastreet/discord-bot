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
    """Halo solaire diffus : centre lumineux avec rayons irreguliers en
    longueurs variees, particules dorees flottantes. Atmosphere de halo
    de lumiere plutot que d'embleme heraldique."""
    primary, secondary, accent = palette
    rng = random.Random(seed)

    # Fond degrade radial : centre jaune chaud, bord brun-rouge profond
    img = radial_gradient((W * 0.5, H * 0.5), accent, shade(secondary, 0.4),
                          radius=W * 0.65)
    img = img.convert("RGBA")

    cx, cy = W // 2, H // 2

    # Rayons LUMINEUX irreguliers : 60 rayons fins de longueurs et
    # opacites variees, traces avec un blur leger pour donner un effet
    # de lumiere divergente plutot que de triangles rigides.
    rays_far = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd_far = ImageDraw.Draw(rays_far)
    for _ in range(70):
        ang = rng.uniform(0, 2 * math.pi)
        length = rng.uniform(140, 360)
        width = rng.choice([1, 1, 1, 2, 2, 3])
        alpha = rng.randint(40, 130)
        ex = cx + math.cos(ang) * length
        ey = cy + math.sin(ang) * length
        rd_far.line([(cx, cy), (ex, ey)],
                    fill=(255, 230, 160, alpha), width=width)
    rays_far = rays_far.filter(ImageFilter.GaussianBlur(2.5))
    img.alpha_composite(rays_far)

    # Halo central tres flou (brouillard chaud)
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((cx - 220, cy - 220, cx + 220, cy + 220),
               fill=(255, 220, 150, 160))
    halo = halo.filter(ImageFilter.GaussianBlur(60))
    img.alpha_composite(halo)

    # Disque solaire flou (pas de contour net)
    disk = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disk)
    dd.ellipse((cx - 80, cy - 80, cx + 80, cy + 80),
               fill=(255, 245, 210, 255))
    disk = disk.filter(ImageFilter.GaussianBlur(6))
    img.alpha_composite(disk)
    # Coeur brillant net plus petit
    core = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    cd.ellipse((cx - 40, cy - 40, cx + 40, cy + 40),
               fill=(255, 255, 240, 255))
    img.alpha_composite(core)

    # Particules dorees flottantes en suspension
    parts = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(parts)
    for _ in range(220):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        d_to_center = math.hypot(x - cx, y - cy)
        if d_to_center < 60:
            continue  # pas au coeur
        sz = rng.choice([1, 1, 2, 2, 3])
        a = max(20, int(220 - d_to_center * 0.4))
        pd.ellipse((x, y, x + sz, y + sz),
                   fill=(255, 235, 170, a))
    parts = parts.filter(ImageFilter.GaussianBlur(0.8))
    img.alpha_composite(parts)

    return vignette(img.convert("RGB"), 0.42)


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
    """Soleil couchant atmospherique : gradient continu violet -> orange -> jaune,
    mer en bas avec reflet ondulant, nuages striees diffus, silhouettes
    d'oiseaux. Pas de bandes droites."""
    primary, secondary, accent = palette
    rng = random.Random(seed)

    # Ciel : gradient continu sans bandes (5 stops interpoles smooth)
    stops = [
        (0.00, (40,  15,  70)),    # haut violet profond
        (0.25, (110, 50,  110)),   # violet-rose
        (0.45, (210, 95,  100)),   # rose chaud
        (0.62, (255, 150, 80)),    # orange ardent
        (0.74, (255, 210, 130)),   # jaune-orange
    ]
    sea_start = 0.74
    sea_end_col = (15, 12, 30)

    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        if t <= sea_start:
            # Interpole entre les stops
            col = stops[0][1]
            for i in range(len(stops) - 1):
                p0, c0 = stops[i]
                p1, c1 = stops[i + 1]
                if p0 <= t <= p1:
                    local_t = (t - p0) / (p1 - p0)
                    # Easing smooth
                    local_t = local_t * local_t * (3 - 2 * local_t)
                    col = lerp_rgb(c0, c1, local_t)
                    break
        else:
            # Mer : du dernier stop vers fond sombre
            local_t = (t - sea_start) / (1 - sea_start)
            local_t = local_t ** 1.4
            col = lerp_rgb(stops[-1][1], sea_end_col, local_t)
        for x in range(W):
            px[x, y] = col

    img = img.convert("RGBA")

    # Nuages striees diffus dans le ciel (longues bandes flouttees, opacite faible)
    clouds = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cld = ImageDraw.Draw(clouds)
    for _ in range(7):
        y0 = rng.randint(int(H * 0.10), int(H * 0.55))
        thickness = rng.randint(8, 22)
        x0 = rng.randint(-100, 100)
        x1 = rng.randint(W - 100, W + 100)
        col_var = rng.choice([
            (255, 200, 160, 90),
            (255, 170, 130, 80),
            (180, 100, 130, 70),
        ])
        cld.ellipse((x0, y0, x1, y0 + thickness), fill=col_var)
    clouds = clouds.filter(ImageFilter.GaussianBlur(18))
    img.alpha_composite(clouds)

    # Halo soleil diffus (sans bord net)
    sun_x = int(W * 0.5)
    sun_y = int(H * 0.70)
    big_halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bhd = ImageDraw.Draw(big_halo)
    bhd.ellipse((sun_x - 320, sun_y - 320, sun_x + 320, sun_y + 320),
                fill=(255, 200, 130, 120))
    big_halo = big_halo.filter(ImageFilter.GaussianBlur(80))
    img.alpha_composite(big_halo)

    # Soleil
    sun_r = 60
    sun_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun_layer)
    sd.ellipse((sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r),
               fill=(255, 240, 180, 255))
    sun_layer = sun_layer.filter(ImageFilter.GaussianBlur(3))
    img.alpha_composite(sun_layer)

    # Reflet ondulant sur la mer (pas de rectangles : utilise petites
    # ellipses horizontales avec largeur variable + sinusoide)
    refl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rfd = ImageDraw.Draw(refl)
    for i, y in enumerate(range(sun_y + 30, H, 5)):
        # Largeur grandissante puis decroissante par ondulation
        base_w = max(10, int((H - y) * 0.65))
        wobble = int(math.sin(i * 0.45) * 8)
        w = base_w + wobble
        ox = int(math.cos(i * 0.3) * 6)
        alpha = max(60, 200 - i * 4)
        rfd.ellipse((sun_x - w + ox, y, sun_x + w + ox, y + 4),
                    fill=(255, 220, 160, alpha))
    refl = refl.filter(ImageFilter.GaussianBlur(2))
    img.alpha_composite(refl)

    # Silhouettes oiseaux (3-5 petits V) dans le ciel
    birds = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(birds)
    for _ in range(rng.randint(3, 5)):
        bx = rng.randint(int(W * 0.1), int(W * 0.9))
        by = rng.randint(int(H * 0.15), int(H * 0.45))
        sz = rng.randint(8, 14)
        # Forme en V (2 segments)
        bd.line([(bx - sz, by), (bx, by - sz // 2)],
                fill=(20, 10, 25, 220), width=2)
        bd.line([(bx, by - sz // 2), (bx + sz, by)],
                fill=(20, 10, 25, 220), width=2)
    img.alpha_composite(birds)

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
