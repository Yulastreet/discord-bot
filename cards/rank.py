from PIL import Image, ImageDraw, ImageFont
import aiohttp
import io

async def generate_rank_card(member, level, xp, progress_xp, needed_xp, percent):
    async with aiohttp.ClientSession() as session:
        async with session.get(str(member.display_avatar.url)) as resp:
            avatar_bytes = await resp.read()

    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((110, 110))

    card = Image.new("RGBA", (600, 170), color=(44, 47, 51))
    draw = ImageDraw.Draw(card)

    # Avatar rond
    mask = Image.new("L", (110, 110), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, 110, 110), fill=255)
    card.paste(avatar, (20, 30), mask)

    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
        font_level = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except:
        font_name = ImageFont.load_default()
        font_level = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Nom
    draw.text((150, 18), f"{member.display_name}", fill=(255, 255, 255), font=font_name)
    # Niveau
    draw.text((150, 60), f"Niveau {level}", fill=(88, 101, 242), font=font_level)
    # XP total
    draw.text((280, 63), f"•  {xp} XP total", fill=(200, 200, 200), font=font_small)

    # Barre de progression
    bar_x, bar_y = 150, 105
    bar_w, bar_h = 400, 22
    filled_w = int((percent / 100) * bar_w)

    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=11, fill=(80, 80, 80))
    if filled_w > 0:
        draw.rounded_rectangle([bar_x, bar_y, bar_x + filled_w, bar_y + bar_h], radius=11, fill=(88, 101, 242))

    # XP progression + pourcentage
    draw.text((150, 133), f"{progress_xp} / {needed_xp} XP", fill=(180, 180, 180), font=font_small)
    draw.text((490, 133), f"{percent}%", fill=(255, 255, 255), font=font_small)

    output = io.BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output


async def generate_levelup_card(member, level, percent):
    """Carte LEVEL UP f2p, taille native 300x85 (pas de resize destructif).

    Composition identique au premium : LEVEL UP! centre haut, pseudo
    centre milieu, NIVEAU X centre bas. Pas de barre de progression.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(str(member.display_avatar.url)) as resp:
            avatar_bytes = await resp.read()

    W, H = 300, 85
    AVATAR_SIZE = 60
    AVATAR_X = 10
    AVATAR_Y = (H - AVATAR_SIZE) // 2

    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize(
        (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS,
    )

    card = Image.new("RGBA", (W, H), color=(44, 47, 51, 255))
    draw = ImageDraw.Draw(card)

    # Avatar rond
    mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR_SIZE, AVATAR_SIZE), fill=255)
    card.paste(avatar, (AVATAR_X, AVATAR_Y), mask)

    def _font(size, bold=True):
        path_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        path_reg  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        try:
            return ImageFont.truetype(path_bold if bold else path_reg, size)
        except Exception:
            return ImageFont.load_default()

    f_title = _font(20, bold=True)
    f_user  = _font(11, bold=True)
    f_label = _font(9,  bold=True)
    f_value = _font(26, bold=True)

    # Zone texte
    text_x = AVATAR_X + AVATAR_SIZE + 10
    text_right = W - 10
    zone_w = text_right - text_x

    def _center(y, text, font, fill):
        tw = draw.textlength(text, font=font)
        draw.text((text_x + (zone_w - tw) / 2, y), text, font=font, fill=fill)

    # LEVEL UP !
    _center(5, "LEVEL UP !", f_title, (255, 215, 0))

    # Pseudo
    name = member.display_name
    while draw.textlength(name, font=f_user) > zone_w and len(name) > 1:
        name = name[:-1]
    if name != member.display_name:
        name = name[:-1] + "…"
    _center(30, name, f_user, (255, 255, 255))

    # NIVEAU X : label + valeur cote a cote, centres
    lbl = "NIVEAU"
    val = str(level)
    lbl_tw = draw.textlength(lbl, font=f_label)
    val_tw = draw.textlength(val, font=f_value)
    combined_w = lbl_tw + 8 + val_tw
    cx = text_x + (zone_w - combined_w) / 2
    draw.text((cx, 55), lbl, font=f_label, fill=(88, 101, 242))
    draw.text((cx + lbl_tw + 8, 45), val, font=f_value, fill=(255, 255, 255))

    output = io.BytesIO()
    card.convert("RGB").save(output, format="PNG")
    output.seek(0)
    return output