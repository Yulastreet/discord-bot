import discord
from discord.ext import commands
import os
import random
import aiohttp
import yt_dlp
import asyncio
from dotenv import load_dotenv
from rank_card import generate_levelup_card, generate_rank_card
from database import init_db, get_xp, set_xp, get_leaderboard, get_all_reactions, set_reaction, remove_reaction, get_welcome, set_welcome

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Init DB au démarrage
init_db()

# Chargement des réactions en mémoire
USER_REACTIONS = get_all_reactions()

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
        set_xp(message.author.id, xp)
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