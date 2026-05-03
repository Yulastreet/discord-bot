import discord
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration
TOKEN = os.getenv("DISCORD_TOKEN")

# Dictionnaire : ID utilisateur -> emoji de réaction
# Tu peux ajouter autant d'utilisateurs que tu veux
USER_REACTIONS = {
    235079585509801984: "<:67:1500587937300091083>",   # Remplace par le vrai ID utilisateur
    250304844374605835: "<:lurios:1462973231890698474>",   # Autre exemple
    # Ajoute d'autres utilisateurs ici
}

# Intents nécessaires
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {client.user}")
    print(f"👀 Surveillance de {len(USER_REACTIONS)} utilisateur(s)")

@client.event
async def on_message(message):
    # Ignore les messages du bot lui-même
    if message.author == client.user:
        return

    # Vérifie si l'auteur du message est dans la liste
    if message.author.id in USER_REACTIONS:
        emoji = USER_REACTIONS[message.author.id]
        try:
            await message.add_reaction(emoji)
            print(f"✅ Réaction {emoji} ajoutée au message de {message.author.name}")
        except discord.HTTPException as e:
            print(f"❌ Erreur lors de l'ajout de la réaction : {e}")

client.run(TOKEN)
