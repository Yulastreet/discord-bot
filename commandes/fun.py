import hashlib
import random

import aiohttp
import discord
from discord import app_commands


def setup_fun_commands(bot):
    @bot.tree.command(name="8ball", description="Pose une question a la boule magique")
    @app_commands.describe(question="Ta question")
    async def eight_ball(interaction: discord.Interaction, question: str):
        reponses = [
            "Oui, absolument !", "Non, pas du tout.", "Peut-etre...",
            "C'est certain !", "Je ne pense pas.", "Sans aucun doute !",
            "Tres probablement.", "Les signes pointent vers non.",
            "Concentre-toi et redemande.", "C'est flou, reessaie.",
        ]
        embed = discord.Embed(title="8Ball", color=discord.Color.purple())
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Reponse", value=random.choice(reponses), inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="dé", description="Lancer un de")
    @app_commands.describe(faces="Nombre de faces (defaut : 6)")
    async def de(interaction: discord.Interaction, faces: int = 6):
        resultat = random.randint(1, faces)
        await interaction.response.send_message(
            f"Tu as lance un de a {faces} faces et obtenu : **{resultat}**"
        )

    @bot.tree.command(name="coinflip", description="Pile ou face")
    async def coinflip(interaction: discord.Interaction):
        resultat = random.choice(["Pile", "Face"])
        await interaction.response.send_message(f"La piece tombe sur : **{resultat}**")

    @bot.tree.command(name="blague", description="Une blague aleatoire")
    async def blague(interaction: discord.Interaction):
        await interaction.response.defer()
        async with aiohttp.ClientSession() as session:
            async with session.get("https://v2.jokeapi.dev/joke/Any?lang=fr") as response:
                data = await response.json()
                if data["type"] == "single":
                    await interaction.followup.send(data["joke"])
                else:
                    await interaction.followup.send(f"**{data['setup']}**\n||{data['delivery']}||")

    @bot.tree.command(name="ship", description="Calcule le taux de compatibilite entre deux membres")
    @app_commands.describe(membre1="Premier membre", membre2="Deuxieme membre")
    async def ship(interaction: discord.Interaction, membre1: discord.Member, membre2: discord.Member):
        if membre1.id == membre2.id:
            await interaction.response.send_message("Tu ne peux pas ship quelqu'un avec lui-meme.", ephemeral=True)
            return
        pair = tuple(sorted([str(membre1.id), str(membre2.id)]))
        seed = int(hashlib.sha256(f"{pair[0]}:{pair[1]}".encode()).hexdigest(), 16)
        pct = seed % 101
        n1, n2 = membre1.display_name, membre2.display_name
        fused = n1[:max(2, len(n1)//2)] + n2[max(1, len(n2)//2):]
        if pct >= 90:
            verdict, col = "Ames soeurs.", discord.Color.from_rgb(220, 50, 80)
        elif pct >= 70:
            verdict, col = "Belle alchimie.", discord.Color.from_rgb(230, 100, 130)
        elif pct >= 50:
            verdict, col = "Ca pourrait marcher.", discord.Color.from_rgb(200, 140, 160)
        elif pct >= 25:
            verdict, col = "Mouais.", discord.Color.from_rgb(150, 150, 150)
        else:
            verdict, col = "Catastrophe.", discord.Color.from_rgb(120, 120, 120)
        bar_len = 20
        filled = int(pct / 100 * bar_len)
        bar = "#" * filled + "-" * (bar_len - filled)
        embed = discord.Embed(title=f"{n1} x {n2}", color=col)
        embed.add_field(name="Compatibilite", value=f"`{bar}` **{pct}%**", inline=False)
        embed.add_field(name="Nom fusion", value=f"**{fused}**", inline=True)
        embed.add_field(name="Verdict", value=verdict, inline=True)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="choix", description="Le bot choisit pour toi entre plusieurs options")
    @app_commands.describe(options="Tes options separees par des |")
    async def choix(interaction: discord.Interaction, options: str):
        items = [o.strip() for o in options.split("|") if o.strip()]
        if len(items) < 2:
            await interaction.response.send_message("Donne au moins 2 options separees par `|`.", ephemeral=True)
            return
        if len(items) > 20:
            await interaction.response.send_message("Maximum 20 options.", ephemeral=True)
            return
        pick = random.choice(items)
        embed = discord.Embed(title="Le bot a choisi", color=discord.Color.teal())
        embed.add_field(name="Options", value=" / ".join(f"`{o}`" for o in items), inline=False)
        embed.add_field(name="Choix", value=f"**{pick}**", inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="random", description="Tire un nombre aleatoire entre deux bornes")
    @app_commands.describe(min="Borne min incluse", max="Borne max incluse")
    async def random_cmd(interaction: discord.Interaction, min: int, max: int):
        if min > max:
            min, max = max, min
        if max - min > 1_000_000_000:
            await interaction.response.send_message("Plage trop grande (max 1 milliard).", ephemeral=True)
            return
        n = random.randint(min, max)
        await interaction.response.send_message(f"Entre **{min}** et **{max}** : **{n}**")

    @bot.tree.command(name="qui", description="Le bot designe un membre du serveur au hasard")
    @app_commands.describe(question="La question")
    async def qui(interaction: discord.Interaction, question: str):
        members = [m for m in interaction.guild.members if not m.bot]
        if not members:
            await interaction.response.send_message("Aucun membre humain trouve sur ce serveur.", ephemeral=True)
            return
        pick = random.choice(members)
        embed = discord.Embed(color=discord.Color.gold())
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Designe", value=pick.mention, inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="clap", description="Met clap entre chaque mot")
    @app_commands.describe(texte="Le texte a transformer")
    async def clap(interaction: discord.Interaction, texte: str):
        if len(texte) > 800:
            await interaction.response.send_message("Texte trop long (max 800 caracteres).", ephemeral=True)
            return
        out = " clap ".join(texte.split())
        if not out:
            await interaction.response.send_message("Texte vide.", ephemeral=True)
            return
        await interaction.response.send_message(out)

    @bot.tree.command(name="rate", description="Le bot note quelque chose sur 10")
    @app_commands.describe(truc="Ce que tu veux noter")
    async def rate(interaction: discord.Interaction, truc: str):
        seed = int(hashlib.sha256(truc.lower().strip().encode()).hexdigest(), 16)
        note = seed % 11
        if note >= 9:
            avis = "Chef-d'oeuvre."
        elif note >= 7:
            avis = "Solide."
        elif note >= 5:
            avis = "Honnete."
        elif note >= 3:
            avis = "Mitige."
        else:
            avis = "Bof."
        bar = "*" * note + "." * (10 - note)
        embed = discord.Embed(title=f"Evaluation : {truc[:80]}", color=discord.Color.blue())
        embed.add_field(name="Note", value=f"{bar}\n**{note}/10** - {avis}", inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="citation", description="Affiche une citation au hasard")
    async def citation(interaction: discord.Interaction):
        citations = [
            ("Le doute est le commencement de la sagesse.", "Aristote"),
            ("Connais-toi toi-meme.", "Socrate"),
            ("Ce qui ne nous tue pas nous rend plus forts.", "Friedrich Nietzsche"),
            ("Sois le changement que tu veux voir dans le monde.", "Gandhi"),
            ("Je pense, donc je suis.", "Rene Descartes"),
            ("La simplicite est la sophistication supreme.", "Leonard de Vinci"),
        ]
        texte, auteur = random.choice(citations)
        embed = discord.Embed(description=f"_{texte}_", color=discord.Color.dark_grey())
        embed.set_footer(text=f"- {auteur}")
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="zgeg", description="Mesure ton zgeg")
    async def zgeg(interaction: discord.Interaction):
        taille = random.randint(1, 25)
        if taille >= 23:
            reaction = "Wow ! Impressionnant."
        elif taille >= 19:
            reaction = "Pas mal du tout."
        elif taille >= 15:
            reaction = "Honnete."
        elif taille >= 11:
            reaction = "Dans la moyenne."
        elif taille >= 7:
            reaction = "Bon..."
        elif taille >= 4:
            reaction = "Ahah..."
        else:
            reaction = "Mes condoleances."
        await interaction.response.send_message(f"Ton zgeg mesure **{taille} cm**. {reaction}")
