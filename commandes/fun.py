import hashlib
import random

import aiohttp
import discord
from discord import app_commands

from services.i18n import ti


def setup_fun_commands(bot):
    @bot.tree.command(name="tweet", description="Generate a tweet image from a message")
    @app_commands.describe(message_id="ID of the message to turn into a tweet (right click > Copy ID, dev mode required)")
    async def tweet(interaction: discord.Interaction, message_id: str):
        try:
            mid = int(message_id.strip())
        except ValueError:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.tweet.invalid_id"),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        # Look for the message in the current channel, then fall back to the other text channels
        msg = None
        try:
            msg = await interaction.channel.fetch_message(mid)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            for ch in interaction.guild.text_channels:
                if ch.id == interaction.channel.id:
                    continue
                try:
                    msg = await ch.fetch_message(mid)
                    break
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
        if msg is None:
            await interaction.followup.send(
                ti(interaction, "utils.fun.tweet.not_found"),
                ephemeral=True,
            )
            return
        # Build the localized timestamp (Twitter-like)
        ts = msg.created_at
        try:
            ts_str = ti(
                interaction, "utils.fun.tweet.timestamp",
                time=f"{ts.hour:02d}:{ts.minute:02d}",
                month=ti(interaction, f"utils.fun.tweet.months.m{ts.month}"),
                day=ts.day,
                year=ts.year,
            )
        except Exception:
            ts_str = ts.strftime("%H:%M · %Y-%m-%d")

        author = msg.author
        display = getattr(author, "display_name", str(author)) or str(author)
        uname = (getattr(author, "name", "") or str(author)).lower().replace(" ", "_")
        avatar_url = str(author.display_avatar.url) if author.display_avatar else None
        text = msg.content or ti(interaction, "utils.fun.tweet.empty_body")

        # Counts derived from the Discord reactions:
        # like = total reactions ; retweet = number of repost reactions, if any
        total_reactions = sum(r.count for r in (msg.reactions or []))
        retweet_count = 0
        for r in (msg.reactions or []):
            emoji_str = str(r.emoji)
            if emoji_str in ("🔁", "🔄", "♻️"):
                retweet_count += r.count
        # Views = number of members in the channel (proxy) or random
        try:
            views_count = len(interaction.channel.members) if hasattr(interaction.channel, "members") else 0
        except Exception:
            views_count = 0
        counts = {
            "reply":   0,  # Discord does not expose the reply count easily
            "retweet": retweet_count,
            "like":    total_reactions,
            "views":   views_count,
        }
        # First image attachment of the message (png/jpg/webp/static gif)
        image_url = None
        for att in (msg.attachments or []):
            ct = (att.content_type or "").lower()
            if ct.startswith("image/"):
                image_url = att.url
                break
        try:
            from cards.tweet import render_tweet_card
            buf = await render_tweet_card(
                display_name=display,
                username=uname,
                avatar_url=avatar_url,
                text=text,
                timestamp_str=ts_str,
                verified=True,
                counts=counts,
                image_url=image_url,
            )
        except Exception as e:
            print(f"[fun/tweet] render err: {e!r}")
            await interaction.followup.send(
                ti(interaction, "utils.fun.tweet.render_failed"),
                ephemeral=True,
            )
            return
        import time as _t
        file = discord.File(buf, filename=f"tweet-{int(_t.time()*1000)}.png")
        await interaction.followup.send(file=file)


    @bot.tree.command(name="8ball", description="Ask the magic 8-ball a question")
    @app_commands.describe(question="Your question")
    async def eight_ball(interaction: discord.Interaction, question: str):
        pick = random.randint(1, 10)
        embed = discord.Embed(
            title=ti(interaction, "utils.fun.eightball.title"),
            color=discord.Color.purple(),
        )
        embed.add_field(name=ti(interaction, "utils.fun.eightball.field_question"),
                        value=question, inline=False)
        embed.add_field(name=ti(interaction, "utils.fun.eightball.field_answer"),
                        value=ti(interaction, f"utils.fun.eightball.a{pick}"), inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="dice", description="Roll a die")
    @app_commands.describe(faces="Number of faces (default: 6)")
    async def dice(interaction: discord.Interaction, faces: int = 6):
        result = random.randint(1, faces)
        await interaction.response.send_message(
            ti(interaction, "utils.fun.dice.result", faces=faces, result=result)
        )

    @bot.tree.command(name="coinflip", description="Heads or tails")
    async def coinflip(interaction: discord.Interaction):
        side_key = random.choice(["heads", "tails"])
        side = ti(interaction, f"utils.fun.coinflip.{side_key}")
        await interaction.response.send_message(
            ti(interaction, "utils.fun.coinflip.result", side=side))

    @bot.tree.command(name="joke", description="A random joke")
    async def joke(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://v2.jokeapi.dev/joke/Any?lang=en") as response:
                    data = await response.json()
            if data["type"] == "single":
                await interaction.followup.send(data["joke"])
            else:
                await interaction.followup.send(ti(
                    interaction, "utils.fun.joke.two_parts",
                    setup=data["setup"], delivery=data["delivery"]))
        except Exception as e:
            print(f"[fun/joke] api err: {e!r}")
            await interaction.followup.send(
                ti(interaction, "utils.fun.joke.failed"), ephemeral=True)

    @bot.tree.command(name="ship", description="Compute the compatibility rate between two members")
    @app_commands.describe(member1="First member", member2="Second member")
    async def ship(interaction: discord.Interaction, member1: discord.Member, member2: discord.Member):
        if member1.id == member2.id:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.ship.self"), ephemeral=True)
            return
        pair = tuple(sorted([str(member1.id), str(member2.id)]))
        seed = int(hashlib.sha256(f"{pair[0]}:{pair[1]}".encode()).hexdigest(), 16)
        pct = seed % 101
        n1, n2 = member1.display_name, member2.display_name
        fused = n1[:max(2, len(n1)//2)] + n2[max(1, len(n2)//2):]
        if pct >= 90:
            verdict_key, col = "v_soulmates", discord.Color.from_rgb(220, 50, 80)
        elif pct >= 70:
            verdict_key, col = "v_chemistry", discord.Color.from_rgb(230, 100, 130)
        elif pct >= 50:
            verdict_key, col = "v_could_work", discord.Color.from_rgb(200, 140, 160)
        elif pct >= 25:
            verdict_key, col = "v_meh", discord.Color.from_rgb(150, 150, 150)
        else:
            verdict_key, col = "v_disaster", discord.Color.from_rgb(120, 120, 120)
        bar_len = 20
        filled = int(pct / 100 * bar_len)
        bar = "#" * filled + "-" * (bar_len - filled)
        embed = discord.Embed(
            title=ti(interaction, "utils.fun.ship.title", a=n1, b=n2), color=col)
        embed.add_field(
            name=ti(interaction, "utils.fun.ship.field_compat"),
            value=ti(interaction, "utils.fun.ship.compat_value", bar=bar, pct=pct),
            inline=False)
        embed.add_field(name=ti(interaction, "utils.fun.ship.field_fused"),
                        value=f"**{fused}**", inline=True)
        embed.add_field(name=ti(interaction, "utils.fun.ship.field_verdict"),
                        value=ti(interaction, f"utils.fun.ship.{verdict_key}"), inline=True)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="choice", description="The bot picks for you between several options")
    @app_commands.describe(options="Your options separated by |")
    async def choice(interaction: discord.Interaction, options: str):
        items = [o.strip() for o in options.split("|") if o.strip()]
        if len(items) < 2:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.choice.need_two"), ephemeral=True)
            return
        if len(items) > 20:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.choice.too_many"), ephemeral=True)
            return
        pick = random.choice(items)
        embed = discord.Embed(
            title=ti(interaction, "utils.fun.choice.title"), color=discord.Color.teal())
        embed.add_field(name=ti(interaction, "utils.fun.choice.field_options"),
                        value=" / ".join(f"`{o}`" for o in items), inline=False)
        embed.add_field(name=ti(interaction, "utils.fun.choice.field_pick"),
                        value=f"**{pick}**", inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="random", description="Draw a random number between two bounds")
    @app_commands.describe(min="Lower bound, included", max="Upper bound, included")
    async def random_cmd(interaction: discord.Interaction, min: int, max: int):
        if min > max:
            min, max = max, min
        if max - min > 1_000_000_000:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.random.range_too_big"), ephemeral=True)
            return
        n = random.randint(min, max)
        await interaction.response.send_message(
            ti(interaction, "utils.fun.random.result", min=min, max=max, n=n))

    @bot.tree.command(name="who", description="The bot picks a random member of the server")
    @app_commands.describe(question="The question")
    async def who(interaction: discord.Interaction, question: str):
        members = [m for m in interaction.guild.members if not m.bot]
        if not members:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.who.no_members"), ephemeral=True)
            return
        pick = random.choice(members)
        embed = discord.Embed(color=discord.Color.gold())
        embed.add_field(name=ti(interaction, "utils.fun.who.field_question"),
                        value=question, inline=False)
        embed.add_field(name=ti(interaction, "utils.fun.who.field_chosen"),
                        value=pick.mention, inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="clap", description="Put clap between every word")
    @app_commands.describe(text="The text to transform")
    async def clap(interaction: discord.Interaction, text: str):
        if len(text) > 800:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.clap.too_long"), ephemeral=True)
            return
        out = " clap ".join(text.split())
        if not out:
            await interaction.response.send_message(
                ti(interaction, "utils.fun.clap.empty"), ephemeral=True)
            return
        await interaction.response.send_message(out)

    @bot.tree.command(name="rate", description="The bot rates something out of 10")
    @app_commands.describe(thing="What you want rated")
    async def rate(interaction: discord.Interaction, thing: str):
        seed = int(hashlib.sha256(thing.lower().strip().encode()).hexdigest(), 16)
        score = seed % 11
        if score >= 9:
            verdict_key = "v_masterpiece"
        elif score >= 7:
            verdict_key = "v_solid"
        elif score >= 5:
            verdict_key = "v_decent"
        elif score >= 3:
            verdict_key = "v_mixed"
        else:
            verdict_key = "v_meh"
        bar = "*" * score + "." * (10 - score)
        embed = discord.Embed(
            title=ti(interaction, "utils.fun.rate.title", thing=thing[:80]),
            color=discord.Color.blue())
        embed.add_field(
            name=ti(interaction, "utils.fun.rate.field_score"),
            value=ti(interaction, "utils.fun.rate.score_value", bar=bar, score=score,
                     verdict=ti(interaction, f"utils.fun.rate.{verdict_key}")),
            inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="quote", description="Show a random quote")
    async def quote(interaction: discord.Interaction):
        n = random.randint(1, 6)
        text = ti(interaction, f"utils.fun.quote.q{n}_text")
        author = ti(interaction, f"utils.fun.quote.q{n}_author")
        embed = discord.Embed(
            description=ti(interaction, "utils.fun.quote.body", text=text),
            color=discord.Color.dark_grey())
        embed.set_footer(text=ti(interaction, "utils.fun.quote.footer", author=author))
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="pp", description="Measure your pp")
    async def pp(interaction: discord.Interaction):
        size = random.randint(1, 25)
        if size >= 23:
            reaction_key = "r_wow"
        elif size >= 19:
            reaction_key = "r_good"
        elif size >= 15:
            reaction_key = "r_decent"
        elif size >= 11:
            reaction_key = "r_average"
        elif size >= 7:
            reaction_key = "r_meh"
        elif size >= 4:
            reaction_key = "r_small"
        else:
            reaction_key = "r_tiny"
        await interaction.response.send_message(ti(
            interaction, "utils.fun.pp.result", size=size,
            reaction=ti(interaction, f"utils.fun.pp.{reaction_key}")))
