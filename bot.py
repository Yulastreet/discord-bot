import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import random
import aiohttp
import yt_dlp
import asyncio
from dotenv import load_dotenv
from rank_card import generate_levelup_card, generate_rank_card
from database import (init_db, get_xp, set_xp, get_leaderboard,
                      get_all_reactions, set_reaction, remove_reaction,
                      get_welcome, set_welcome,
                      get_duel_profil, creer_duel_profil, ajouter_tookcoins,
                      ajouter_victoire, ajouter_defaite, changer_sabre_equipe,
                      ajouter_sabre, get_collection_sabres, possede_sabre,
                      sauvegarder_duel, get_historique,
                      add_combat_xp_db, attribuer_stat_db)
from duel_commands import setup_duel_commands

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

init_db()
USER_REACTIONS = get_all_reactions()


# ===== DB WRAPPER POUR DUEL_COMMANDS =====
class DuelDB:
    def get_profil(self, user_id):
        profil = get_duel_profil(user_id)
        if not profil:
            return None
        profil["sabres"] = get_collection_sabres(user_id) or ["bleu"]
        return profil

    def ensure_profil(self, user_id, username):
        if not get_duel_profil(user_id):
            creer_duel_profil(user_id, username)
            ajouter_sabre(user_id, "bleu")
        return self.get_profil(user_id)

    def add_xp(self, user_id, amount):
        xp = get_xp(user_id)
        set_xp(user_id, xp + amount)

    def add_tookcoins(self, user_id, amount):
        ajouter_tookcoins(user_id, amount)

    def add_victoire(self, user_id):
        ajouter_victoire(user_id)

    def add_defaite(self, user_id):
        ajouter_defaite(user_id)

    def sauvegarder(self, uid1, uid2, gagnant_id, coins_gagnant, coins_perdant):
        sauvegarder_duel(uid1, uid2, gagnant_id, coins_gagnant, coins_perdant)

    def update_profil(self, user_id, data):
        if "sabre_equipe" in data:
            changer_sabre_equipe(user_id, data["sabre_equipe"])
        if "sabres" in data:
            existing = set(get_collection_sabres(user_id))
            for sabre_id in data["sabres"]:
                if sabre_id not in existing:
                    ajouter_sabre(user_id, sabre_id)

    def add_combat_xp(self, user_id, amount):
        """Ajoute de l'XP de combat. Retourne (nouveau_niveau, a_monte_de_niveau)."""
        return add_combat_xp_db(user_id, amount)

    def attribuer_stat(self, user_id, stat):
        """Attribue 1 point à une stat. Retourne True si succès."""
        return attribuer_stat_db(user_id, stat)


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

async def play_next(voice_client, channel, guild_id):
    if guild_id in music_queues and music_queues[guild_id]:
        url, title = music_queues[guild_id].pop(0)
        source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
        voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(voice_client, channel, guild_id), bot.loop
            )
        )
        await channel.send(f"🎵 En cours : **{title}**")
    else:
        await channel.send("✅ File d'attente terminée !")


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
    await bot.tree.sync()
    print("✅ Slash commands synchronisées")

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


# ===== RÉACTIONS AUTOMATIQUES (prefix commands — intentionnel) =====

@bot.group(name='reaction', invoke_without_command=True)
async def reaction(ctx):
    embed = discord.Embed(
        title="😄 Réactions automatiques",
        description="Gère les réactions automatiques du bot",
        color=discord.Color.orange()
    )
    embed.add_field(
        name="📋 Commandes",
        value=(
            "`!reaction add <membre> <emoji>` — Ajouter une réaction automatique\n"
            "`!reaction remove <membre>` — Supprimer la réaction d'un membre\n"
            "`!reaction list` — Voir toutes les réactions actives"
        ),
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
    embed = discord.Embed(title="📋 Réactions automatiques actives", color=discord.Color.orange())
    for user_id, emoji in USER_REACTIONS.items():
        membre = ctx.guild.get_member(user_id)
        nom = membre.name if membre else f"Utilisateur inconnu ({user_id})"
        embed.add_field(name=nom, value=emoji, inline=True)
    await ctx.send(embed=embed)


# ===== UTILITAIRES =====

@bot.tree.command(name="ping", description="Voir la latence du bot")
async def ping(interaction: discord.Interaction):
    latence = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong ! Latence : **{latence}ms**")

@bot.tree.command(name="userinfo", description="Infos sur un membre")
@app_commands.describe(membre="Le membre dont tu veux voir les infos")
async def userinfo(interaction: discord.Interaction, membre: discord.Member = None):
    membre = membre or interaction.user
    embed = discord.Embed(title=f"Infos de {membre.name}", color=membre.color)
    embed.set_thumbnail(url=membre.display_avatar.url)
    embed.add_field(name="📛 Nom", value=membre.name)
    embed.add_field(name="🆔 ID", value=membre.id)
    embed.add_field(name="📅 Compte créé le", value=membre.created_at.strftime("%d/%m/%Y"))
    embed.add_field(name="📥 A rejoint le", value=membre.joined_at.strftime("%d/%m/%Y"))
    embed.add_field(name="🎖️ Rôle principal", value=membre.top_role.mention)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo", description="Infos sur le serveur")
async def serverinfo(interaction: discord.Interaction):
    serveur = interaction.guild
    embed = discord.Embed(title=f"Infos de {serveur.name}", color=discord.Color.blue())
    embed.set_thumbnail(url=serveur.icon.url if serveur.icon else None)
    embed.add_field(name="👑 Propriétaire", value=serveur.owner)
    embed.add_field(name="👥 Membres", value=serveur.member_count)
    embed.add_field(name="📅 Créé le", value=serveur.created_at.strftime("%d/%m/%Y"))
    embed.add_field(name="💬 Salons", value=len(serveur.channels))
    embed.add_field(name="🎭 Rôles", value=len(serveur.roles))
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Afficher l'avatar d'un membre")
@app_commands.describe(membre="Le membre dont tu veux voir l'avatar")
async def avatar(interaction: discord.Interaction, membre: discord.Member = None):
    membre = membre or interaction.user
    embed = discord.Embed(title=f"Avatar de {membre.name}", color=discord.Color.blue())
    embed.set_image(url=membre.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="commandes", description="Liste de toutes les commandes")
async def commandes(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Liste des commandes",
        description="Voici toutes les commandes disponibles !",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🛡️ Modération",
        value="`/clear` `/kick` `/ban` `/poll` `/setwelcome`\n`!reaction add/remove/list` *(préfixe uniquement)*",
        inline=False
    )
    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(
        name="🎉 Fun",
        value="`/8ball` `/dé` `/coinflip` `/blague`",
        inline=False
    )
    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(
        name="⭐ Niveaux & XP",
        value="`/niveau` `/leaderboard`",
        inline=False
    )
    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(
        name="⚔️ Duel",
        value="`/duel` `/profil` `/boutique_sabres` `/acheter_sabre` `/equiper_sabre` `/mon_sabre` `/collection` `/historique`",
        inline=False
    )
    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(
        name="🎵 Musique",
        value="`/join` `/play` `/skip` `/queue` `/stop` `/leave`",
        inline=False
    )
    embed.add_field(name="​", value="​", inline=False)
    embed.add_field(
        name="🔧 Utilitaires",
        value="`/avatar` `/userinfo` `/serverinfo` `/ping` `/commandes`",
        inline=False
    )
    embed.set_footer(text="Bot créé par toi 😎")
    await interaction.response.send_message(embed=embed)


# ===== FUN =====

@bot.tree.command(name="8ball", description="Pose une question à la boule magique")
@app_commands.describe(question="Ta question")
async def eight_ball(interaction: discord.Interaction, question: str):
    reponses = [
        "Oui, absolument !", "Non, pas du tout.", "Peut-être...",
        "C'est certain !", "Je ne pense pas.", "Sans aucun doute !",
        "Très probablement.", "Les signes pointent vers non.",
        "Concentre-toi et redemande.", "C'est flou, réessaie."
    ]
    embed = discord.Embed(title="🎱 8Ball", color=discord.Color.purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Réponse", value=random.choice(reponses), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="dé", description="Lancer un dé")
@app_commands.describe(faces="Nombre de faces (défaut : 6)")
async def de(interaction: discord.Interaction, faces: int = 6):
    resultat = random.randint(1, faces)
    await interaction.response.send_message(f"🎲 Tu as lancé un dé à {faces} faces et obtenu : **{resultat}**")

@bot.tree.command(name="coinflip", description="Pile ou face")
async def coinflip(interaction: discord.Interaction):
    resultat = random.choice(["Pile 🪙", "Face 🟡"])
    await interaction.response.send_message(f"La pièce tombe sur : **{resultat}**")

@bot.tree.command(name="blague", description="Une blague aléatoire")
async def blague(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        async with session.get("https://v2.jokeapi.dev/joke/Any?lang=fr") as response:
            data = await response.json()
            if data["type"] == "single":
                await interaction.followup.send(f"😂 {data['joke']}")
            else:
                await interaction.followup.send(f"😂 **{data['setup']}**\n||{data['delivery']}||")


# ===== MODÉRATION =====

@bot.tree.command(name="kick", description="Expulser un membre")
@app_commands.describe(membre="Le membre à expulser", raison="La raison")
@app_commands.default_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison fournie"):
    await membre.kick(reason=raison)
    await interaction.response.send_message(f"👢 **{membre.name}** a été expulsé. Raison : {raison}")

@bot.tree.command(name="ban", description="Bannir un membre")
@app_commands.describe(membre="Le membre à bannir", raison="La raison")
@app_commands.default_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison fournie"):
    await membre.ban(reason=raison)
    await interaction.response.send_message(f"🔨 **{membre.name}** a été banni. Raison : {raison}")

@bot.tree.command(name="clear", description="Supprimer des messages")
@app_commands.describe(nombre="Nombre de messages à supprimer")
@app_commands.default_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, nombre: int):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.purge(limit=nombre)
    await interaction.followup.send(f"🗑️ **{nombre}** messages supprimés !", ephemeral=True)

@bot.tree.command(name="poll", description="Créer un sondage")
@app_commands.describe(question="La question", options="Options séparées par des virgules (ex: Oui,Non,Peut-être)")
async def poll(interaction: discord.Interaction, question: str, options: str):
    option_list = [o.strip() for o in options.split(",")]
    if len(option_list) < 2:
        await interaction.response.send_message("❌ Donne au moins 2 options séparées par des virgules !", ephemeral=True)
        return
    if len(option_list) > 9:
        await interaction.response.send_message("❌ Maximum 9 options !", ephemeral=True)
        return
    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
    description = "\n".join([f"{emojis[i]} {opt}" for i, opt in enumerate(option_list)])
    embed = discord.Embed(title=f"📊 {question}", description=description, color=discord.Color.gold())
    embed.set_footer(text=f"Sondage créé par {interaction.user.name}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(option_list)):
        await msg.add_reaction(emojis[i])

@bot.tree.command(name="setwelcome", description="Définir le salon de bienvenue")
@app_commands.describe(salon="Le salon (laisse vide pour utiliser le salon actuel)")
@app_commands.default_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, salon: discord.TextChannel = None):
    salon = salon or interaction.channel
    set_welcome(interaction.guild.id, salon.id)
    await interaction.response.send_message(f"✅ Salon de bienvenue défini sur {salon.mention} !")


# ===== NIVEAUX / XP =====

@bot.tree.command(name="niveau", description="Voir ton niveau et XP")
@app_commands.describe(membre="Le membre dont tu veux voir le niveau")
async def niveau(interaction: discord.Interaction, membre: discord.Member = None):
    membre = membre or interaction.user
    xp = get_xp(membre.id)
    level, progress_xp, needed_xp, percent = get_progress(xp)
    filled = int(percent / 5)
    bar = "█" * filled + "░" * (20 - filled)
    embed = discord.Embed(title=f"📊 {membre.display_name}", color=discord.Color.blurple())
    embed.add_field(name="🏆 Niveau", value=f"**{level}**", inline=True)
    embed.add_field(name="⭐ XP Total", value=f"**{xp}**", inline=True)
    embed.add_field(
        name="📈 Progression",
        value=f"`{bar}` **{percent}%**\n`{progress_xp} / {needed_xp} XP`",
        inline=False
    )
    embed.set_thumbnail(url=membre.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Classement XP du serveur")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    sorted_users = get_leaderboard()
    if not sorted_users:
        await interaction.followup.send("Personne n'a encore d'XP !")
        return
    embed = discord.Embed(title="🏆 Classement XP", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    description = ""
    for i, (user_id, xp) in enumerate(sorted_users):
        try:
            user = await bot.fetch_user(int(user_id))
            name = user.name
        except Exception:
            name = "Utilisateur inconnu"
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        description += f"{medal} {name} — **{xp} XP** (Niveau {get_level(xp)})\n"
    embed.description = description
    await interaction.followup.send(embed=embed)


# ===== MUSIQUE =====

@bot.tree.command(name="join", description="Rejoindre ton salon vocal")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
        return
    channel = interaction.user.voice.channel
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(channel)
    else:
        await channel.connect()
    await interaction.response.send_message(f"✅ Connecté à **{channel.name}** !")

@bot.tree.command(name="play", description="Jouer une musique")
@app_commands.describe(query="Titre ou lien YouTube")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
        return
    await interaction.response.defer()
    if not interaction.guild.voice_client:
        await interaction.user.voice.channel.connect()
    guild_id = interaction.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = []
    await interaction.followup.send(f"🔍 Recherche de **{query}**...")
    try:
        url, title = await get_audio_info(query)
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur lors de la recherche : {e}")
        return
    music_queues[guild_id].append((url, title))
    await interaction.followup.send(f"✅ Ajouté à la file : **{title}**")
    if not interaction.guild.voice_client.is_playing():
        await play_next(interaction.guild.voice_client, interaction.channel, guild_id)

@bot.tree.command(name="skip", description="Passer à la musique suivante")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("⏭️ Musique passée !")
    else:
        await interaction.response.send_message("❌ Aucune musique en cours !", ephemeral=True)

@bot.tree.command(name="queue", description="Voir la file d'attente musicale")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    q = music_queues.get(guild_id, [])
    if not q:
        await interaction.response.send_message("📭 La file d'attente est vide !")
        return
    embed = discord.Embed(title="🎵 File d'attente", color=discord.Color.blurple())
    description = ""
    for i, (url, title) in enumerate(q):
        description += f"**{i+1}.** {title}\n"
    embed.description = description
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stop", description="Stopper la musique et vider la file")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id in music_queues:
        music_queues[guild_id] = []
    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
    await interaction.response.send_message("⏹️ Musique stoppée et file vidée !")

@bot.tree.command(name="leave", description="Quitter le salon vocal")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        guild_id = interaction.guild.id
        if guild_id in music_queues:
            music_queues[guild_id] = []
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 Déconnecté du salon vocal !")
    else:
        await interaction.response.send_message("❌ Je ne suis pas dans un salon vocal !", ephemeral=True)


# ===== LANCEMENT =====
db = DuelDB()
setup_duel_commands(bot, db)
bot.run(TOKEN)
