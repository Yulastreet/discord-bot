import discord
from discord.ext import commands
import os
import random
import json
import aiohttp
from dotenv import load_dotenv
from rank_card import generate_levelup_card, generate_rank_card

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Chargement des réactions
REACTIONS_FILE = "reactions.json"

def load_reactions():
    if os.path.exists(REACTIONS_FILE):
        with open(REACTIONS_FILE, "r") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    return {}

def save_reactions():
    with open(REACTIONS_FILE, "w") as f:
        json.dump(USER_REACTIONS, f)

USER_REACTIONS = load_reactions()

# ===== XP =====
XP_FILE = "xp.json"

def load_xp():
    if os.path.exists(XP_FILE):
        with open(XP_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    return {}

def save_xp(data):
    with open(XP_FILE, "w") as f:
        json.dump(data, f)

def get_level(xp):
    return int(xp ** 0.2)

def get_progress(xp):
    level = get_level(xp)
    current_level_xp = int(level ** (1 / 0.2))
    next_level_xp = int((level + 1) ** (1 / 0.2))
    progress_xp = xp - current_level_xp
    needed_xp = next_level_xp - current_level_xp
    percent = min(int((progress_xp / needed_xp) * 100), 100)
    return level, progress_xp, needed_xp, percent

# ===== BIENVENUE =====
WELCOME_FILE = "welcome.json"

def load_welcome():
    if os.path.exists(WELCOME_FILE):
        with open(WELCOME_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    return {}

def save_welcome(data):
    with open(WELCOME_FILE, "w") as f:
        json.dump(data, f)

# ===== BOT =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")
    print(f"👀 Surveillance de {len(USER_REACTIONS)} utilisateur(s)")

@bot.event
async def on_member_join(member):
    data = load_welcome()
    channel_id = data.get(str(member.guild.id))
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel:
        embed = discord.Embed(
            title=f"👋 Bienvenue {member.name} !",
            description=f"Bienvenue sur **{member.guild.name}** ! Tu es le membre numéro **{member.guild.member_count}**.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.author.id in USER_REACTIONS:
        emoji = USER_REACTIONS[message.author.id]
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException as e:
            print(f"❌ Erreur réaction : {e}")
            pass
    await bot.process_commands(message)
    
    if not message.author.bot:
        xp_data = load_xp()
        user_id = str(message.author.id)
        if user_id not in xp_data:
            xp_data[user_id] = 0
        old_level = get_level(xp_data[user_id])
        xp_data[user_id] += random.randint(1, 5)
        new_level = get_level(xp_data[user_id])
        save_xp(xp_data)

        if new_level > old_level:
            level, progress_xp, needed_xp, percent = get_progress(xp_data[user_id])
            image = await generate_levelup_card(message.author, new_level, percent)
            await message.channel.send(
                content=f"🎉 {message.author.mention}",
                file=discord.File(image, filename="levelup.png")
            )

    await bot.process_commands(message)

# ===== INFOS =====

@bot.command()
async def ping(ctx):
    latence = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong ! Latence : **{latence}ms**")

@bot.command()
async def userinfo(ctx, membre: discord.Member = None):
    membre = membre or ctx.author
    embed = discord.Embed(title=f"Infos de {membre.name}", color=membre.color)
    embed.set_thumbnail(url=membre.display_avatar.url)
    embed.add_field(name="📛 Nom", value=membre.name)
    embed.add_field(name="🆔 ID", value=membre.id)
    embed.add_field(name="📅 Compte créé le", value=membre.created_at.strftime("%d/%m/%Y"))
    embed.add_field(name="📥 A rejoint le", value=membre.joined_at.strftime("%d/%m/%Y"))
    embed.add_field(name="🎖️ Rôle principal", value=membre.top_role.mention)
    await ctx.send(embed=embed)

@bot.command()
async def serverinfo(ctx):
    serveur = ctx.guild
    embed = discord.Embed(title=f"Infos de {serveur.name}", color=discord.Color.blue())
    embed.set_thumbnail(url=serveur.icon.url if serveur.icon else None)
    embed.add_field(name="👑 Propriétaire", value=serveur.owner)
    embed.add_field(name="👥 Membres", value=serveur.member_count)
    embed.add_field(name="📅 Créé le", value=serveur.created_at.strftime("%d/%m/%Y"))
    embed.add_field(name="💬 Salons", value=len(serveur.channels))
    embed.add_field(name="🎭 Rôles", value=len(serveur.roles))
    await ctx.send(embed=embed)

@bot.command(name='commandes')
async def commandes(ctx):
    embed = discord.Embed(
        title="📋 Liste des commandes",
        description="Voici toutes les commandes disponibles !",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🛡️ Modération",
        value="""
`!clear <nombre>` - Supprimer des messages
`!kick <membre> [raison]` - Expulser un membre
`!ban <membre> [raison]` - Bannir un membre
`!poll <question> <opt1> <opt2>` - Créer un sondage
        """,
        inline=False
    )

    embed.add_field(name="\u200b", value="\u200b", inline=False)  # Saut de ligne

    embed.add_field(
        name="🎉 Fun",
        value="""
`!8ball <question>` - Boule magique 🎱
`!dé` - Lancer un dé 🎲
`!coinflip` - Pile ou face 🪙
`!blague` - Blague aléatoire 😂
        """,
        inline=False
    )

    embed.add_field(name="\u200b", value="\u200b", inline=False)  # Saut de ligne

    embed.add_field(
        name="⭐ Niveaux & XP",
        value="""
`!niveau` - Voir ton niveau et ton XP
`!leaderboard` - Classement du serveur
        """,
        inline=False
    )

    embed.add_field(name="\u200b", value="\u200b", inline=False)  # Saut de ligne

    embed.add_field(
        name="🔧 Utilitaires",
        value="""
`!avatar [membre]` - Afficher un avatar
`!userinfo [membre]` - Infos sur un membre
`!serverinfo` - Infos sur le serveur
`!ping` - Latence du bot
`!setwelcome <salon>` - Définir le salon de bienvenue
`!commandes` - Afficher ce message
        """,
        inline=False
    )

    embed.set_footer(text="Bot créé par toi 😎")
    await ctx.send(embed=embed)

# ===== FUN =====

@bot.command(name="8ball")
async def eight_ball(ctx, *, question):
    reponses = [
        "Oui, absolument !", "Non, pas du tout.", "Peut-être...",
        "C'est certain !", "Je ne pense pas.", "Sans aucun doute !",
        "Très probablement.", "Les signes pointent vers non.",
        "Concentrate et redemande.", "C'est flou, réessaie."
    ]
    embed = discord.Embed(title="🎱 8Ball", color=discord.Color.purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Réponse", value=random.choice(reponses), inline=False)
    await ctx.send(embed=embed)

@eight_ball.error
async def eight_ball_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage : `!8ball <ta question>`")

@bot.command(name="dé")
async def de(ctx, faces: int = 6):
    resultat = random.randint(1, faces)
    await ctx.send(f"🎲 Tu as lancé un dé à {faces} faces et obtenu : **{resultat}**")

@bot.command()
async def coinflip(ctx):
    resultat = random.choice(["Pile 🪙", "Face 🟡"])
    await ctx.send(f"La pièce tombe sur : **{resultat}**")

@bot.command()
async def blague(ctx):
    async with aiohttp.ClientSession() as session:
        async with session.get("https://v2.jokeapi.dev/joke/Any?lang=fr") as response:
            data = await response.json()
            if data["type"] == "single":
                await ctx.send(f"😂 {data['joke']}")
            else:
                await ctx.send(f"😂 **{data['setup']}**\n||{data['delivery']}||")

                # ===== REACTIONS AUTOMATIQUES =====

@bot.group(name='reaction', invoke_without_command=True)
async def reaction(ctx):
    embed = discord.Embed(
        title="😄 Réactions automatiques",
        description="Gère les réactions automatiques du bot",
        color=discord.Color.orange()
    )
    embed.add_field(
        name="📋 Commandes",
        value="""
`!reaction add <membre> <emoji>` - Ajouter une réaction automatique
`!reaction remove <membre>` - Supprimer la réaction d'un membre
`!reaction list` - Voir toutes les réactions actives
        """,
        inline=False
    )
    await ctx.send(embed=embed)

@reaction.command(name='add')
@commands.has_permissions(administrator=True)
async def reaction_add(ctx, membre: discord.Member, emoji: str):
    USER_REACTIONS[membre.id] = emoji
    save_reactions()
    await ctx.send(f"✅ Le bot réagira avec {emoji} aux messages de **{membre.name}**")

@reaction.command(name='remove')
@commands.has_permissions(administrator=True)
async def reaction_remove(ctx, membre: discord.Member):
    if membre.id in USER_REACTIONS:
        del USER_REACTIONS[membre.id]
        save_reactions()
        await ctx.send(f"✅ Réaction supprimée pour **{membre.name}**")
    else:
        await ctx.send(f"❌ Aucune réaction configurée pour **{membre.name}**")

@reaction.command(name='list')
async def reaction_list(ctx):
    if not USER_REACTIONS:
        await ctx.send("❌ Aucune réaction automatique configurée")
        return

    embed = discord.Embed(
        title="📋 Réactions automatiques actives",
        color=discord.Color.orange()
    )

    for user_id, emoji in USER_REACTIONS.items():
        membre = ctx.guild.get_member(user_id)
        nom = membre.name if membre else f"Utilisateur inconnu ({user_id})"
        embed.add_field(name=nom, value=emoji, inline=True)

    await ctx.send(embed=embed)

# ===== MODÉRATION =====

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, membre: discord.Member, *, raison="Aucune raison fournie"):
    await membre.kick(reason=raison)
    await ctx.send(f"👢 **{membre.name}** a été expulsé. Raison : {raison}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, membre: discord.Member, *, raison="Aucune raison fournie"):
    await membre.ban(reason=raison)
    await ctx.send(f"🔨 **{membre.name}** a été banni. Raison : {raison}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, nombre: int):
    await ctx.channel.purge(limit=nombre + 1)
    msg = await ctx.send(f"🗑️ **{nombre}** messages supprimés !")
    await msg.delete(delay=3)

@kick.error
async def kick_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Tu n'as pas les permissions nécessaires !")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage : `!kick @membre <raison>`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Membre introuvable !")

@ban.error
async def ban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Tu n'as pas les permissions nécessaires !")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage : `!ban @membre <raison>`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Membre introuvable !")

@clear.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Tu n'as pas les permissions nécessaires !")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage : `!clear <nombre>`")

# ===== AUTRE =====

@bot.command()
async def avatar(ctx, membre: discord.Member = None):
    membre = membre or ctx.author
    embed = discord.Embed(title=f"Avatar de {membre.name}", color=discord.Color.blue())
    embed.set_image(url=membre.display_avatar.url)
    await ctx.send(embed=embed)

@bot.command()
async def poll(ctx, question, *options):
    if len(options) < 2:
        await ctx.send('❌ Donne au moins 2 options ! Exemple : `!poll "Question" "Option1" "Option2"`')
        return
    if len(options) > 9:
        await ctx.send("❌ Maximum 9 options !")
        return

    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
    description = "\n".join([f"{emojis[i]} {option}" for i, option in enumerate(options)])

    embed = discord.Embed(title=f"📊 {question}", description=description, color=discord.Color.gold())
    embed.set_footer(text=f"Sondage créé par {ctx.author.name}")
    poll_msg = await ctx.send(embed=embed)

    for i in range(len(options)):
        await poll_msg.add_reaction(emojis[i])

@poll.error
async def poll_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('❌ Usage : `!poll "Question" "Option1" "Option2"`')

# ===== BIENVENUE =====

@bot.command()
@commands.has_permissions(administrator=True)
async def setwelcome(ctx, salon: discord.TextChannel = None):
    salon = salon or ctx.channel
    data = load_welcome()
    data[str(ctx.guild.id)] = salon.id
    save_welcome(data)
    await ctx.send(f"✅ Salon de bienvenue défini sur {salon.mention} !")

@setwelcome.error
async def setwelcome_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Tu n'as pas les permissions nécessaires !")

# ===== NIVEAUX/XP =====

@bot.command()
async def niveau(ctx, member: discord.Member = None):
    member = member or ctx.author
    xp_data = load_xp()
    user_id = str(member.id)
    xp = xp_data.get(user_id, 0)
    level, progress_xp, needed_xp, percent = get_progress(xp)

    # Barre de progression
    filled = int(percent / 5)
    bar = "█" * filled + "░" * (20 - filled)

    embed = discord.Embed(
        title=f"📊 {member.display_name}",
        color=discord.Color.blurple()
    )
    embed.add_field(name="🏆 Niveau", value=f"**{level}**", inline=True)
    embed.add_field(name="⭐ XP Total", value=f"**{xp}**", inline=True)
    embed.add_field(
        name="📈 Progression",
        value=f"`{bar}` **{percent}%**\n`{progress_xp} / {needed_xp} XP`",
        inline=False
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx):
    xp_data = load_xp()
    if not xp_data:
        await ctx.send("Personne n'a encore d'XP !")
        return

    sorted_users = sorted(xp_data.items(), key=lambda x: x[1], reverse=True)[:10]
    embed = discord.Embed(title="🏆 Classement XP", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    description = ""
    for i, (user_id, xp) in enumerate(sorted_users):
        try:
            user = await bot.fetch_user(int(user_id))
            name = user.name
        except:
            name = "Utilisateur inconnu"
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        description += f"{medal} {name} — **{xp} XP** (Niveau {get_level(xp)})\n"

    embed.description = description
    await ctx.send(embed=embed)

bot.run(TOKEN)
