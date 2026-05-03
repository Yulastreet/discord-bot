import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

USER_REACTIONS = {
    235079585509801984: "<:67:1500587937300091083>",
    250304844374605835: "<:lurios:1462973231890698474>",
}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")
    print(f"👀 Surveillance de {len(USER_REACTIONS)} utilisateur(s)")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.author.id in USER_REACTIONS:
        emoji = USER_REACTIONS[message.author.id]
        try:
            await message.add_reaction(emoji)
            print(f"✅ Réaction {emoji} ajoutée au message de {message.author.name}")
        except discord.HTTPException as e:
            print(f"❌ Erreur : {e}")

    await bot.process_commands(message)  # ⚠️ IMPORTANT pour que les commandes marchent

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

bot.run(TOKEN)