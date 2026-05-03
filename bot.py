import discord
from discord.ext import commands
import os
import random
import json
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

USER_REACTIONS = {
    235079585509801984: "<:67:1500587937300091083>",
    250304844374605835: "<:lurios:1462973231890698474>",
}

# ===== XP =====
XP_FILE = "xp.json"

def load_xp():
    if os.path.exists(XP_FILE):
        with open(XP_FILE, "r") as f:
            return json.load(f)
    return {}

def save_xp(data):
    with open(XP_FILE, "w") as f:
        json.dump(data, f)

def get_level(xp):
    return int(xp ** 0.4)

# ===== BIENVENUE =====
WELCOME_FILE = "welcome.json"

def load_welcome():
    if os.path.exists(WELCOME_FILE):
        with open(WELCOME_FILE, "r") as f:
            return json.load(f)
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

    # Réactions automatiques
    if message.author.id in USER_REACTIONS:
        emoji = USER_REACTIONS[message.author.id]
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException as e:
            print(f"❌ Erreur : {e}")

    # Système XP
    if not message.author.bot:
        xp_data = load_xp()
        user_id = str(message.author.id)
        if user_id not in xp_data:
            xp_data[user_id] = 0
        old_level = get_level(xp_data[user_id])
        xp_data[user_id] += random.randint(5, 15)
        new_level = get_level(xp_data[user_id])
        save_xp(xp_data)

        if new_level > old_level:
            await message.channel.send(f"🎉 {message.author.mention} est passé au niveau **{new_level}** !")

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
    blagues = [
        "Pourquoi les plongeurs plongent-ils toujours en arrière ? Parce que sinon ils tomberaient dans le bateau !",
        "C'est l'histoire d'un homme qui rentre dans un bar... Aïe.",
        "Qu'est-ce qu'un canif ? Un petit fien.",
        "Pourquoi les moutons ne mangent pas d'ordinateurs ? Parce qu'ils ont peur des virus.",
        "Comment appelle-t-on un chat tombé dans un pot de peinture ? Un chat peint !"
    ]
    await ctx.send(f"😂 {random.choice(blagues)}")

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
@ban.error
@clear.error
async def moderation_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Tu n'as pas les permissions nécessaires !")

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
        await ctx.send("❌ Donne au moins 2 options ! Exemple : `!poll \"Question\" \"Option1\" \"Option2\"`")
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
async def niveau(ctx, membre: discord.Member = None):
    membre = membre or ctx.author
    xp_data = load_xp()
    user_id = str(membre.id)
    xp = xp_data.get(user_id, 0)
    level = get_level(xp)
    embed = discord.Embed(title=f"📊 Niveau de {membre.name}", color=discord.Color.orange())
    embed.add_field(name="🏆 Niveau", value=level)
    embed.add_field(name="⭐ XP", value=xp)
    embed.set_thumbnail(url=membre.display_avatar.url)
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
