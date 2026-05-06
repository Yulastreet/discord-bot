import discord
from discord.ext import commands, tasks
import os
import random
import aiohttp
import yt_dlp
import asyncio
import sqlite3
from dotenv import load_dotenv
from rank_card import generate_levelup_card, generate_rank_card
from database import (init_db, get_xp, set_xp, get_leaderboard, 
                      get_all_reactions, set_reaction, remove_reaction, 
                      get_welcome, set_welcome,
                      get_duel_profil, creer_duel_profil, ajouter_tookcoins,
                      ajouter_victoire, ajouter_defaite, changer_sabre_equipe,
                      ajouter_sabre, get_collection_sabres, possede_sabre, sauvegarder_duel)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Init DB au démarrage
init_db()

# Chargement des réactions en mémoire
USER_REACTIONS = get_all_reactions()

# Duels en cours {user_id: user_id}
duels_en_cours = {}

# ===== SABRES =====
SABRES = {
    "bleu":    {"nom": "Sabre Bleu",    "emoji": "🔵", "degats": (15, 25), "prix": 0},
    "rouge":   {"nom": "Sabre Rouge",   "emoji": "🔴", "degats": (18, 28), "prix": 500},
    "vert":    {"nom": "Sabre Vert",    "emoji": "🟢", "degats": (20, 30), "prix": 800},
    "violet":  {"nom": "Sabre Violet",  "emoji": "🟣", "degats": (22, 32), "prix": 1200},
    "noir":    {"nom": "Sabre Noir",    "emoji": "⚫", "degats": (25, 40), "prix": 2000},
    "or":      {"nom": "Sabre Doré",    "emoji": "🟡", "degats": (30, 45), "prix": 5000},
}

# ===== XP =====
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

# ===== MUSIQUE =====
music_queues = {}

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

async def get_audio_info(query):
    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        if not query.startswith("http"):
            query = f"ytsearch:{query}"
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
        if 'entries' in info:
            info = info['entries'][0]
        return info['url'], info['title']

async def play_next(ctx):
    guild_id = ctx.guild.id
    if guild_id in music_queues and music_queues[guild_id]:
        url, title = music_queues[guild_id].pop(0)
        source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
        ctx.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        )
        await ctx.send(f"🎵 En cours : **{title}**")
    else:
        await ctx.send("✅ File d'attente terminée !")

# ===== BOT =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")
    print(f"👀 Surveillance de {len(USER_REACTIONS)} utilisateur(s)")
    reload_reactions.start()

@tasks.loop(seconds=5)
async def reload_reactions():
    global USER_REACTIONS
    USER_REACTIONS = get_all_reactions()

@bot.event
async def on_member_join(member):
    data = get_welcome(member.guild.id)
    if not data:
        return
    channel = bot.get_channel(data)
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

    if not message.author.bot:
        xp = get_xp(message.author.id)
        old_level = get_level(xp)
        xp += random.randint(1, 5)
        set_xp(message.author.id, xp, username=message.author.name)
        
        new_level = get_level(xp)
        if new_level > old_level:
            level, progress_xp, needed_xp, percent = get_progress(xp)
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
`!reaction` - Afficher l'aide des réactions
`!reaction add <membre> <emoji>` - Ajouter une réaction automatique
`!reaction remove <membre>` - Supprimer la réaction d'un membre
`!reaction list` - Voir toutes les réactions actives
        """,
        inline=False
    )
    embed.add_field(name="\u200b", value="\u200b", inline=False)
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
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(
        name="⭐ Niveaux & XP",
        value="""
`!niveau` - Voir ton niveau et ton XP
`!leaderboard` - Classement du serveur
        """,
        inline=False
    )
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(
        name="⚔️ Duel",
        value="""
`!duel @membre` - Défier un membre
`!profil` - Voir ton profil duel
`!shop` - Voir la boutique de sabres
`!acheter <sabre>` - Acheter un sabre
`!equiper <sabre>` - Équiper un sabre
`!collection` - Voir tes sabres
`!historique` - Voir tes derniers duels
        """,
        inline=False
    )
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(
        name="🎵 Musique",
        value="""
`!join` - Rejoindre ton salon vocal
`!play <titre ou lien>` - Jouer une musique
`!skip` - Passer à la suivante
`!queue` - Voir la file d'attente
`!stop` - Stopper et vider la file
`!leave` - Quitter le salon vocal
        """,
        inline=False
    )
    embed.add_field(name="\u200b", value="\u200b", inline=False)
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
    set_reaction(membre.id, emoji)
    await ctx.send(f"✅ Le bot réagira avec {emoji} aux messages de **{membre.name}**")

@reaction.command(name='remove')
@commands.has_permissions(administrator=True)
async def reaction_remove(ctx, membre: discord.Member):
    if membre.id in USER_REACTIONS:
        del USER_REACTIONS[membre.id]
        remove_reaction(membre.id)
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
    set_welcome(ctx.guild.id, salon.id)
    await ctx.send(f"✅ Salon de bienvenue défini sur {salon.mention} !")

@setwelcome.error
async def setwelcome_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Tu n'as pas les permissions nécessaires !")

# ===== NIVEAUX/XP =====

@bot.command()
async def niveau(ctx, member: discord.Member = None):
    member = member or ctx.author
    xp = get_xp(member.id)
    level, progress_xp, needed_xp, percent = get_progress(xp)

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
    sorted_users = get_leaderboard()
    if not sorted_users:
        await ctx.send("Personne n'a encore d'XP !")
        return
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

# ===== DUEL =====

def get_or_create_profil(user_id, username):
    profil = get_duel_profil(user_id)
    if not profil:
        creer_duel_profil(user_id, username)
        ajouter_sabre(user_id, "bleu")
        profil = get_duel_profil(user_id)
    return profil

@bot.command()
async def profil(ctx, membre: discord.Member = None):
    membre = membre or ctx.author
    profil = get_or_create_profil(membre.id, membre.name)
    collection = get_collection_sabres(membre.id)
    sabre_equipe = profil["sabre_equipe"]
    sabre_info = SABRES.get(sabre_equipe, SABRES["bleu"])

    total = profil["victoires"] + profil["defaites"]
    ratio = f"{profil['victoires']}/{total}" if total > 0 else "0/0"

    embed = discord.Embed(
        title=f"⚔️ Profil de {membre.display_name}",
        color=discord.Color.red()
    )
    embed.set_thumbnail(url=membre.display_avatar.url)
    embed.add_field(name="💰 TookCoins", value=f"**{profil['tookcoins']}** 🪙", inline=True)
    embed.add_field(name="🏆 Victoires", value=f"**{profil['victoires']}**", inline=True)
    embed.add_field(name="💀 Défaites", value=f"**{profil['defaites']}**", inline=True)
    embed.add_field(name="📊 Ratio", value=ratio, inline=True)
    embed.add_field(name="⚔️ Sabre équipé", value=f"{sabre_info['emoji']} {sabre_info['nom']}", inline=True)
    embed.add_field(name="🗂️ Collection", value=f"**{len(collection)}** sabre(s)", inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def shop(ctx):
    embed = discord.Embed(
        title="🛒 Boutique de sabres",
        description="Achète de nouveaux sabres avec tes TookCoins !",
        color=discord.Color.gold()
    )
    for sabre_id, info in SABRES.items():
        if info["prix"] == 0:
            prix_str = "Gratuit (de base)"
        else:
            prix_str = f"{info['prix']} 🪙"
        degats = info["degats"]
        embed.add_field(
            name=f"{info['emoji']} {info['nom']} (`{sabre_id}`)",
            value=f"💥 Dégâts : {degats[0]}-{degats[1]}\n💰 Prix : {prix_str}",
            inline=True
        )
    embed.set_footer(text="Utilise !acheter <nom> pour acheter un sabre")
    await ctx.send(embed=embed)

@bot.command()
async def acheter(ctx, sabre_id: str):
    sabre_id = sabre_id.lower()
    if sabre_id not in SABRES:
        await ctx.send(f"❌ Sabre inconnu ! Utilise `!shop` pour voir les sabres disponibles.")
        return

    profil = get_or_create_profil(ctx.author.id, ctx.author.name)
    info = SABRES[sabre_id]

    if possede_sabre(ctx.author.id, sabre_id):
        await ctx.send(f"❌ Tu possèdes déjà le **{info['nom']}** !")
        return

    if profil["tookcoins"] < info["prix"]:
        await ctx.send(f"❌ Tu n'as pas assez de TookCoins ! Il te faut **{info['prix']}** 🪙 (tu en as **{profil['tookcoins']}**)")
        return

    ajouter_tookcoins(ctx.author.id, -info["prix"])
    ajouter_sabre(ctx.author.id, sabre_id)
    await ctx.send(f"✅ Tu as acheté le **{info['emoji']} {info['nom']}** pour **{info['prix']}** 🪙 !")

@bot.command()
async def equiper(ctx, sabre_id: str):
    sabre_id = sabre_id.lower()
    if sabre_id not in SABRES:
        await ctx.send(f"❌ Sabre inconnu ! Utilise `!shop` pour voir les sabres disponibles.")
        return

    if not possede_sabre(ctx.author.id, sabre_id):
        await ctx.send(f"❌ Tu ne possèdes pas ce sabre ! Achète-le avec `!acheter {sabre_id}`")
        return

    changer_sabre_equipe(ctx.author.id, sabre_id)
    info = SABRES[sabre_id]
    await ctx.send(f"✅ Tu as équipé le **{info['emoji']} {info['nom']}** !")

@bot.command()
async def collection(ctx, membre: discord.Member = None):
    membre = membre or ctx.author
    sabres = get_collection_sabres(membre.id)

    if not sabres:
        await ctx.send(f"❌ {membre.display_name} n'a aucun sabre !")
        return

    profil = get_duel_profil(membre.id)
    sabre_equipe = profil["sabre_equipe"] if profil else "bleu"

    embed = discord.Embed(
        title=f"🗡️ Collection de {membre.display_name}",
        color=discord.Color.blue()
    )
    description = ""
    for sabre_id in sabres:
        info = SABRES.get(sabre_id, {"nom": sabre_id, "emoji": "⚔️"})
        equipe = " ← équipé" if sabre_id == sabre_equipe else ""
        description += f"{info['emoji']} **{info['nom']}**{equipe}\n"
    embed.description = description
    await ctx.send(embed=embed)

@bot.command()
async def historique(ctx, membre: discord.Member = None):
    from database import get_historique
    membre = membre or ctx.author
    duels = get_historique(membre.id, limit=5)

    if not duels:
        await ctx.send(f"❌ Aucun duel dans l'historique pour {membre.display_name} !")
        return

    embed = discord.Embed(
        title=f"📜 Historique de {membre.display_name}",
        color=discord.Color.blurple()
    )
    description = ""
    for duel in duels:
        gagne = str(duel["gagnant_id"]) == str(membre.id)
        result = "✅ Victoire" if gagne else "❌ Défaite"
        adversaire_id = duel["user_id_2"] if str(duel["user_id_1"]) == str(membre.id) else duel["user_id_1"]
        try:
            adversaire = await bot.fetch_user(int(adversaire_id))
            adversaire_nom = adversaire.name
        except:
            adversaire_nom = f"Inconnu ({adversaire_id})"
        coins = duel["tookcoins_gagnant"] if gagne else duel["tookcoins_perdant"]
        description += f"{result} vs **{adversaire_nom}** — {coins} 🪙 | {duel['date'][:10]}\n"
    embed.description = description
    await ctx.send(embed=embed)

@bot.command()
async def duel(ctx, adversaire: discord.Member):
    # Vérifications
    if adversaire == ctx.author:
        await ctx.send("❌ Tu ne peux pas te défier toi-même !")
        return
    if adversaire.bot:
        await ctx.send("❌ Tu ne peux pas défier un bot !")
        return
    if ctx.author.id in duels_en_cours or adversaire.id in duels_en_cours:
        await ctx.send("❌ Un joueur est déjà en duel !")
        return

    # Création des profils si besoin
    profil_challenger = get_or_create_profil(ctx.author.id, ctx.author.name)
    profil_adversaire = get_or_create_profil(adversaire.id, adversaire.name)

    # Demande d'acceptation
    embed = discord.Embed(
        title="⚔️ Défi lancé !",
        description=f"{ctx.author.mention} défie {adversaire.mention} en duel !\n\n{adversaire.mention}, réagis avec ✅ pour accepter ou ❌ pour refuser !",
        color=discord.Color.orange()
    )
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check(reaction, user):
        return user == adversaire and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id

    try:
        reaction, user = await bot.wait_for("reaction_add", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send(f"⏰ {adversaire.display_name} n'a pas répondu à temps. Duel annulé !")
        return

    if str(reaction.emoji) == "❌":
        await ctx.send(f"❌ {adversaire.display_name} a refusé le duel !")
        return

       # Marquer les joueurs comme en duel
    duels_en_cours[ctx.author.id] = adversaire.id
    duels_en_cours[adversaire.id] = ctx.author.id

    # ===== FONCTIONS UTILITAIRES =====
    def barre_hp(hp, max_hp=250):
        filled = int((hp / max_hp) * 10)
        return f"{'🟥' * filled}{'⬛' * (10 - filled)} **{hp}/250**"

    # ===== SETUP COMBAT =====
    sabre_c = SABRES.get(profil_challenger["sabre_equipe"], SABRES["bleu"])
    sabre_a = SABRES.get(profil_adversaire["sabre_equipe"], SABRES["bleu"])

    joueurs = [
        {
            "membre": ctx.author,
            "sabre": sabre_c,
            "hp": 250,
            "parade_active": False,
            "parade_cooldown": 0,
            "defense_active": False,
            "special_utilise": False
        },
        {
            "membre": adversaire,
            "sabre": sabre_a,
            "hp": 250,
            "parade_active": False,
            "parade_cooldown": 0,
            "defense_active": False,
            "special_utilise": False
        }
    ]

    ACTIONS_EMOJI = {
        "⚔️": "attaque",
        "🛡️": "parade",
        "🔰": "defense",
        "👊": "coup_bas",
        "✨": "special"
    }

    # ===== JET DE DÉ INITIAL =====
    while True:
        de1 = random.randint(1, 6)
        de2 = random.randint(1, 6)
        if de1 > de2:
            ordre = [0, 1]
            premier = ctx.author.display_name
            break
        elif de2 > de1:
            ordre = [1, 0]
            premier = adversaire.display_name
            break

    embed_principal = discord.Embed(
        title="🎲 Jet de dé initial !",
        description=(
            f"{ctx.author.mention} : **{de1}** 🎲\n"
            f"{adversaire.mention} : **{de2}** 🎲\n\n"
            f"➡️ **{premier} commence !**\n\n"
            f"❤️ {joueurs[0]['membre'].display_name} : {barre_hp(joueurs[0]['hp'])}\n"
            f"❤️ {joueurs[1]['membre'].display_name} : {barre_hp(joueurs[1]['hp'])}"
        ),
        color=discord.Color.blurple()
    )
    msg_principal = await ctx.send(embed=embed_principal)
    await asyncio.sleep(3)

    tour = 1
    MAX_TOURS = 20

    while joueurs[0]["hp"] > 0 and joueurs[1]["hp"] > 0 and tour <= MAX_TOURS:

        choix = [None, None]

        # ===== PHASE DE CHOIX À L'AVEUGLE =====
        # Les deux joueurs choisissent leur action, chacun leur tour sur le même message
        for idx in [0, 1]:
            j = joueurs[idx]

            # Construire liste actions disponibles
            actions_dispos = ["⚔️"]
            if j["parade_cooldown"] == 0:
                actions_dispos.append("🛡️")
            actions_dispos.append("🔰")
            actions_dispos.append("👊")
            if not j["special_utilise"]:
                actions_dispos.append("✨")

            # Texte des actions
            lignes_actions = "⚔️ Attaque"
            if j["parade_cooldown"] == 0:
                lignes_actions += " | 🛡️ Parade"
            else:
                lignes_actions += f" | 🛡️ Parade (cooldown {j['parade_cooldown']})"
            lignes_actions += " | 🔰 Défense | 👊 Coup bas"
            if not j["special_utilise"]:
                lignes_actions += " | ✨ Spéciale"
            else:
                lignes_actions += " | ✨ ~~Spéciale (utilisée)~~"

            embed_principal.title = f"⚔️ Tour {tour} — {j['membre'].display_name}, choisis ton action !"
            embed_principal.description = (
                f"{lignes_actions}\n\n"
                f"❤️ {joueurs[0]['membre'].display_name} : {barre_hp(joueurs[0]['hp'])}\n"
                f"❤️ {joueurs[1]['membre'].display_name} : {barre_hp(joueurs[1]['hp'])}\n\n"
                f"⏱️ **30 secondes pour choisir !**"
            )
            embed_principal.color = discord.Color.orange()
            await msg_principal.edit(embed=embed_principal)
            await msg_principal.clear_reactions()
            for emoji in actions_dispos:
                await msg_principal.add_reaction(emoji)

            def check_action(reaction, user, joueur=j, dispos=actions_dispos):
                return (
                    user == joueur["membre"]
                    and str(reaction.emoji) in dispos
                    and reaction.message.id == msg_principal.id
                )

            try:
                reaction, user = await bot.wait_for("reaction_add", timeout=30.0, check=check_action)
                choix[idx] = ACTIONS_EMOJI[str(reaction.emoji)]
            except asyncio.TimeoutError:
                choix[idx] = "attaque"

        # ===== RÉSOLUTION DU TOUR =====
        description_result = (
            f"**{joueurs[ordre[0]]['membre'].display_name}** → **{choix[ordre[0]].upper()}**\n"
            f"**{joueurs[ordre[1]]['membre'].display_name}** → **{choix[ordre[1]].upper()}**\n\n"
        )

        for atk_idx, def_idx in [(ordre[0], ordre[1]), (ordre[1], ordre[0])]:
            attaquant = joueurs[atk_idx]
            defenseur = joueurs[def_idx]
            action_atk = choix[atk_idx]

            if joueurs[0]["hp"] <= 0 or joueurs[1]["hp"] <= 0:
                break

            if action_atk == "attaque":
                dmg = random.randint(*attaquant["sabre"]["degats"])
                crit = random.random() < 0.25
                if crit:
                    dmg = int(dmg * 1.5)

                if defenseur["parade_active"]:
                    attaquant["hp"] = max(0, attaquant["hp"] - dmg)
                    defenseur["parade_active"] = False
                    description_result += f"🔄 La parade de **{defenseur['membre'].display_name}** renvoie **{dmg}** dégâts à **{attaquant['membre'].display_name}** !\n"
                elif defenseur["defense_active"]:
                    dmg_reduit = int(dmg * 0.4)
                    defenseur["hp"] = max(0, defenseur["hp"] - dmg_reduit)
                    defenseur["defense_active"] = False
                    description_result += f"🔰 **{defenseur['membre'].display_name}** se défend et réduit les dégâts à **{dmg_reduit}** !\n"
                else:
                    defenseur["hp"] = max(0, defenseur["hp"] - dmg)
                    if crit:
                        description_result += f"💥 CRITIQUE ! **{attaquant['membre'].display_name}** inflige **{dmg}** dégâts !\n"
                    else:
                        description_result += f"⚔️ **{attaquant['membre'].display_name}** inflige **{dmg}** dégâts à **{defenseur['membre'].display_name}** !\n"

            elif action_atk == "parade":
                if attaquant["parade_cooldown"] > 0:
                    description_result += f"❌ **{attaquant['membre'].display_name}** est en cooldown de parade !\n"
                else:
                    attaquant["parade_active"] = True
                    attaquant["parade_cooldown"] = 5
                    description_result += f"🛡️ **{attaquant['membre'].display_name}** se met en parade !\n"

            elif action_atk == "defense":
                attaquant["defense_active"] = True
                description_result += f"🔰 **{attaquant['membre'].display_name}** prend une posture défensive (-60% dégâts) !\n"

            elif action_atk == "coup_bas":
                if defenseur["parade_active"]:
                    dmg = random.randint(*attaquant["sabre"]["degats"]) * 2
                    defenseur["hp"] = max(0, defenseur["hp"] - dmg)
                    defenseur["parade_active"] = False
                    description_result += f"💥 **{attaquant['membre'].display_name}** brise la parade ! **{dmg}** dégâts critiques !\n"
                else:
                    dmg = max(1, random.randint(*attaquant["sabre"]["degats"]) // 2)
                    defenseur["hp"] = max(0, defenseur["hp"] - dmg)
                    description_result += f"👊 **{attaquant['membre'].display_name}** fait un coup bas pour **{dmg}** dégâts.\n"

            elif action_atk == "special":
                if not attaquant["special_utilise"]:
                    attaquant["special_utilise"] = True
                    dmg = random.randint(*attaquant["sabre"]["degats"]) + 15
                    defenseur["hp"] = max(0, defenseur["hp"] - dmg)
                    description_result += f"✨ **{attaquant['membre'].display_name}** utilise sa SPÉCIALE pour **{dmg}** dégâts ! (usage unique)\n"
                else:
                    description_result += f"❌ **{attaquant['membre'].display_name}** a déjà utilisé sa spéciale !\n"

            # Réduire cooldown parade
            if attaquant["parade_cooldown"] > 0 and action_atk != "parade":
                attaquant["parade_cooldown"] -= 1

        # ===== AFFICHER RÉSULTAT DU TOUR =====
        description_result += (
            f"\n❤️ {joueurs[0]['membre'].display_name} : {barre_hp(joueurs[0]['hp'])}\n"
            f"❤️ {joueurs[1]['membre'].display_name} : {barre_hp(joueurs[1]['hp'])}\n\n"
        )

        if joueurs[0]["hp"] > 0 and joueurs[1]["hp"] > 0 and tour < MAX_TOURS:
            description_result += "*Réagis avec ▶️ pour passer au tour suivant !*"

        embed_principal.title = f"⚔️ Tour {tour} — Résultat"
        embed_principal.description = description_result
        embed_principal.color = discord.Color.red()
        await msg_principal.clear_reactions()
        await msg_principal.edit(embed=embed_principal)

        if joueurs[0]["hp"] > 0 and joueurs[1]["hp"] > 0 and tour < MAX_TOURS:
            await msg_principal.add_reaction("▶️")

            def check_next(reaction, user):
                return (
                    user in [ctx.author, adversaire]
                    and str(reaction.emoji) == "▶️"
                    and reaction.message.id == msg_principal.id
                )

            try:
                await bot.wait_for("reaction_add", timeout=60.0, check=check_next)
            except asyncio.TimeoutError:
                pass

        if joueurs[0]["hp"] <= 0 or joueurs[1]["hp"] <= 0:
            break

        tour += 1

    # ===== FIN DU COMBAT =====
    if joueurs[0]["hp"] > joueurs[1]["hp"]:
        gagnant = joueurs[0]["membre"]
        perdant = joueurs[1]["membre"]
        hp_gagnant = joueurs[0]["hp"]
    elif joueurs[1]["hp"] > joueurs[0]["hp"]:
        gagnant = joueurs[1]["membre"]
        perdant = joueurs[0]["membre"]
        hp_gagnant = joueurs[1]["hp"]
    else:
        embed_principal.title = "🤝 ÉGALITÉ !"
        embed_principal.description = "Les deux combattants tombent simultanément !"
        embed_principal.color = discord.Color.greyple()
        await msg_principal.clear_reactions()
        await msg_principal.edit(embed=embed_principal)
        del duels_en_cours[ctx.author.id]
        del duels_en_cours[adversaire.id]
        return

    # Récompenses
    xp_gain = 50
    coins_gain = 100
    ajouter_victoire(gagnant.id)
    ajouter_defaite(perdant.id)
    ajouter_tookcoins(gagnant.id, coins_gain)
    sauvegarder_duel(ctx.author.id, adversaire.id, gagnant.id, coins_gain, 0)

    del duels_en_cours[ctx.author.id]
    del duels_en_cours[adversaire.id]

    embed_principal.title = "🏆 FIN DU DUEL !"
    embed_principal.description = (
        f"**{gagnant.display_name}** remporte le duel avec **{hp_gagnant} HP** restants !\n\n"
        f"🎖️ +{xp_gain} XP\n"
        f"🪙 +{coins_gain} TookCoins pour **{gagnant.display_name}**\n\n"
        f"❤️ {joueurs[0]['membre'].display_name} : {barre_hp(joueurs[0]['hp'])}\n"
        f"❤️ {joueurs[1]['membre'].display_name} : {barre_hp(joueurs[1]['hp'])}"
    )
    embed_principal.color = discord.Color.gold()
    await msg_principal.clear_reactions()
    await msg_principal.edit(embed=embed_principal)

@duel.error
async def duel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage : `!duel @membre`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Membre introuvable !")


@duel.error
async def duel_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage : `!duel @membre`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Membre introuvable !")

# ===== MUSIQUE =====

@bot.command()
async def join(ctx):
    if not ctx.author.voice:
        await ctx.send("❌ Tu dois être dans un salon vocal !")
        return
    channel = ctx.author.voice.channel
    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()
    await ctx.send(f"✅ Connecté à **{channel.name}** !")

@bot.command()
async def play(ctx, *, query):
    if not ctx.author.voice:
        await ctx.send("❌ Tu dois être dans un salon vocal !")
        return
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
    guild_id = ctx.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = []
    await ctx.send(f"🔍 Recherche de **{query}**...")
    try:
        url, title = await get_audio_info(query)
    except Exception as e:
        await ctx.send(f"❌ Erreur lors de la recherche : {e}")
        return
    music_queues[guild_id].append((url, title))
    await ctx.send(f"✅ Ajouté à la file : **{title}**")
    if not ctx.voice_client.is_playing():
        await play_next(ctx)

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Musique passée !")
    else:
        await ctx.send("❌ Aucune musique en cours !")

@bot.command()
async def queue(ctx):
    guild_id = ctx.guild.id
    q = music_queues.get(guild_id, [])
    if not q:
        await ctx.send("📭 La file d'attente est vide !")
        return
    embed = discord.Embed(title="🎵 File d'attente", color=discord.Color.blurple())
    description = ""
    for i, (url, title) in enumerate(q):
        description += f"**{i+1}.** {title}\n"
    embed.description = description
    await ctx.send(embed=embed)

@bot.command()
async def stop(ctx):
    guild_id = ctx.guild.id
    if guild_id in music_queues:
        music_queues[guild_id] = []
    if ctx.voice_client:
        ctx.voice_client.stop()
    await ctx.send("⏹️ Musique stoppée et file vidée !")

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        guild_id = ctx.guild.id
        if guild_id in music_queues:
            music_queues[guild_id] = []
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Déconnecté du salon vocal !")
    else:
        await ctx.send("❌ Je ne suis pas dans un salon vocal !")

bot.run(TOKEN)