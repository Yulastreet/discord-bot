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
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_level = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except:
        font_big = ImageFont.load_default()
        font_level = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((150, 15), "LEVEL UP !", fill=(255, 215, 0), font=font_big)
    draw.text((150, 60), f"{member.display_name}", fill=(255, 255, 255), font=font_level)
    draw.text((150, 95), f"Niveau {level}", fill=(200, 200, 200), font=font_small)

    bar_x, bar_y = 150, 125
    bar_w, bar_h = 400, 22
    filled_w = int((percent / 100) * bar_w)

    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=11, fill=(80, 80, 80))
    if filled_w > 0:
        draw.rounded_rectangle([bar_x, bar_y, bar_x + filled_w, bar_y + bar_h], radius=11, fill=(88, 101, 242))

    draw.text((490, 133), f"{percent}%", fill=(255, 255, 255), font=font_small)

    output = io.BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output