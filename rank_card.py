from PIL import Image, ImageDraw, ImageFont
import aiohttp
import io

async def generate_levelup_card(member, level, percent):
    async with aiohttp.ClientSession() as session:
        async with session.get(str(member.display_avatar.url)) as resp:
            avatar_bytes = await resp.read()

    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((100, 100))

    card = Image.new("RGBA", (500, 150), color=(44, 47, 51))
    draw = ImageDraw.Draw(card)

    # Avatar rond
    mask = Image.new("L", (100, 100), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, 100, 100), fill=255)
    card.paste(avatar, (25, 25), mask)

    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((145, 20), "LEVEL UP !", fill=(255, 215, 0), font=font_big)
    draw.text((145, 55), f"{member.display_name}", fill=(255, 255, 255), font=font_small)
    draw.text((145, 80), f"Niveau {level}", fill=(200, 200, 200), font=font_small)

    bar_x, bar_y = 145, 110
    bar_w, bar_h = 320, 18
    filled_w = int((percent / 100) * bar_w)

    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=9, fill=(80, 80, 80))
    if filled_w > 0:
        draw.rounded_rectangle([bar_x, bar_y, bar_x + filled_w, bar_y + bar_h], radius=9, fill=(88, 101, 242))
    draw.text((bar_x + bar_w + 8, bar_y), f"{percent}%", fill=(255, 255, 255), font=font_small)

    output = io.BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output


async def generate_rank_card(member, xp, level, progress_xp, needed_xp, percent):
    async with aiohttp.ClientSession() as session:
        async with session.get(str(member.display_avatar.url)) as resp:
            avatar_bytes = await resp.read()

    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((100, 100))

    card = Image.new("RGBA", (500, 150), color=(44, 47, 51))
    draw = ImageDraw.Draw(card)

    # Avatar rond
    mask = Image.new("L", (100, 100), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, 100, 100), fill=255)
    card.paste(avatar, (25, 25), mask)

    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((145, 20), f"{member.display_name}", fill=(255, 255, 255), font=font_big)
    draw.text((145, 60), f"Niveau {level}  •  {xp} XP", fill=(200, 200, 200), font=font_small)

    bar_x, bar_y = 145, 95
    bar_w, bar_h = 300, 18
    filled_w = int((percent / 100) * bar_w)

    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=9, fill=(80, 80, 80))
    if filled_w > 0:
        draw.rounded_rectangle([bar_x, bar_y, bar_x + filled_w, bar_y + bar_h], radius=9, fill=(88, 101, 242))

    draw.text((145, 118), f"{progress_xp} / {needed_xp} XP", fill=(180, 180, 180), font=font_small)
    draw.text((bar_x + bar_w + 8, bar_y), f"{percent}%", fill=(255, 255, 255), font=font_small)

    output = io.BytesIO()
    card.save(output, format="PNG")
    output.seek(0)
    return output
