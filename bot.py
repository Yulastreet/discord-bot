import os
# Nettoyer les env vars Node IPC heritees de pm2 — sinon Deno (utilise par yt-dlp
# pour resoudre les JS challenges YouTube) crash avec "fd is not from BiPipe".
for _v in ("NODE_CHANNEL_FD", "NODE_UNIQUE_ID", "NODE_OPTIONS",
           "PM2_USAGE", "PM2_HOME", "pm_id", "PM2_DISCRETE_MODE"):
    os.environ.pop(_v, None)

import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import aiohttp
import yt_dlp
import asyncio
import ctypes.util
from dotenv import load_dotenv

# ===== Charger libopus pour le voice =====
def _load_opus():
    if discord.opus.is_loaded():
        return True
    # Tentatives de paths courants Linux + macOS + auto-detect
    candidates = [
        ctypes.util.find_library("opus"),
        "libopus.so.0",
        "libopus.so",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/lib/aarch64-linux-gnu/libopus.so.0",
        "/usr/local/lib/libopus.so.0",
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/local/lib/libopus.dylib",
    ]
    for path in candidates:
        if not path:
            continue
        try:
            discord.opus.load_opus(path)
            ok = discord.opus.is_loaded()
            print(f"[opus] load_opus({path}) -> is_loaded={ok}")
            if ok:
                return True
        except Exception as e:
            print(f"[opus] load_opus({path}) FAILED: {e}")
            continue
    print("[opus] FAILED to load — install libopus0 (apt) or libopus (brew)")
    return False

_load_opus()
from rank_card import generate_levelup_card, generate_rank_card
from database import (init_db, get_xp, set_xp, get_leaderboard,
                      get_all_reactions_index, set_reaction, remove_reaction, get_all_reactions,
                      get_welcome, set_welcome,
                      get_duel_profil, creer_duel_profil, ajouter_tookcoins,
                      ajouter_victoire, ajouter_defaite, changer_sabre_equipe,
                      ajouter_sabre, get_collection_sabres, possede_sabre,
                      sauvegarder_duel, get_historique,
                      add_combat_xp_db, attribuer_stat_db,
                      upsert_guild, mark_guild_left,
                      music_queue_add, music_queue_pop_next, music_queue_list, music_queue_clear,
                      music_state_set, music_state_get, music_state_clear_current, music_state_disconnect,
                      bot_command_fetch_pending, bot_command_finish,
                      add_log, replace_guild_channels, upsert_channel, remove_channel,
                      save_dm,
                      prune_logs_global,
                      get_setting, get_all_settings,
                      replace_guild_members, upsert_member, remove_member)
from duel_commands import setup_duel_commands

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

init_db()
# Index reactions par (guild_id_str, user_id_int)
USER_REACTIONS = get_all_reactions_index()

# ===== Etat runtime exposé pour /api/status =====
import time as _time
import json as _json
BOT_STATE = {
    "started_at":     _time.time(),
    "pid":            os.getpid(),
    "last_error":     None,
    "last_error_at":  None,
    "ytdlp_version":  None,
    "discordpy_version": None,
    "guild_count":    0,
    "voice_count":    0,
    "latency_ms":     None,
    "updated_at":     _time.time(),
}
try:
    import yt_dlp as _ytdlp_mod
    BOT_STATE["ytdlp_version"] = getattr(_ytdlp_mod.version, "__version__", None) or str(getattr(_ytdlp_mod, "version", ""))
except Exception:
    pass
try:
    BOT_STATE["discordpy_version"] = discord.__version__
except Exception:
    pass

_BOT_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.json")

def _write_bot_state():
    try:
        BOT_STATE["updated_at"]  = _time.time()
        BOT_STATE["guild_count"] = len(bot.guilds) if bot.is_ready() else 0
        BOT_STATE["voice_count"] = sum(1 for g in bot.guilds if g.voice_client) if bot.is_ready() else 0
        BOT_STATE["latency_ms"]  = round(bot.latency * 1000) if bot.is_ready() else None
        with open(_BOT_STATE_FILE, "w", encoding="utf-8") as f:
            _json.dump(BOT_STATE, f)
    except Exception as e:
        print(f"[bot_state] write error: {e}")

# Ecrit l'etat initial des le module charge
_write_bot_state()


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


# ===== MUSIQUE (DB-backed) =====
import datetime as _dt

_COOKIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

YDL_OPTIONS = {
    # Selecteur ultra-permissif : prend ce qui existe.
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': False,            # passe a False pour voir les details d'erreur dans les logs
    'no_warnings': False,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'youtube_include_dash_manifest': True,
    'prefer_free_formats': True,
    # Cascade large de clients YouTube. ios+android passent souvent quand web echoue.
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'android', 'web', 'mweb', 'tv_embedded'],
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
}

# Si un fichier cookies.txt existe a cote de bot.py, l'utiliser pour by-pass anti-bot YouTube.
if os.path.exists(_COOKIES_PATH):
    YDL_OPTIONS['cookiefile'] = _COOKIES_PATH
    print(f"[yt-dlp] cookies loaded from {_COOKIES_PATH}")
else:
    print(f"[yt-dlp] aucun cookies.txt detecte ({_COOKIES_PATH}). Si YouTube bloque, voir README cookies.")

FFMPEG_OPTIONS = {
    # before_options : passe en CLI a ffmpeg AVANT -i (input)
    # - reconnect : auto-reconnect sur drop
    # - thread_queue_size : tampon paquets entrants (defaut 8 = trop petit)
    # - probesize/analyzeduration : reduit la latence de demarrage sans nuire a l'audio
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
        '-thread_queue_size 1024 '
        '-probesize 1M -analyzeduration 0'
    ),
    # options : -vn = pas de video. -bufsize = tampon de sortie plus gros, evite sous-tampons.
    'options': '-vn -bufsize 1024k',
}

async def get_audio_info(query):
    """Retourne dict {url, title, duration, thumbnail, source_url} depuis yt-dlp."""
    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        if not query.startswith("http"):
            query = f"ytsearch:{query}"
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
        if 'entries' in info:
            info = info['entries'][0]
        return {
            "url":        info.get("url"),
            "title":      info.get("title") or "(sans titre)",
            "duration":   info.get("duration"),
            "thumbnail":  info.get("thumbnail"),
            "source_url": info.get("webpage_url") or info.get("original_url"),
        }

async def play_next(voice_client, channel, guild_id):
    """Pop next track from DB queue and play. channel optional (for chat notif)."""
    track = music_queue_pop_next(str(guild_id))
    if not track:
        music_state_clear_current(str(guild_id))
        if channel:
            try: await channel.send("✅ File d'attente terminée !")
            except Exception: pass
        return

    try:
        source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)
        voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(voice_client, channel, guild_id), bot.loop
            )
        )
        music_state_set(str(guild_id),
            current_title=track["title"],
            current_url=track["url"],
            current_thumbnail=track.get("thumbnail"),
            current_duration=track.get("duration"),
            is_playing=1, is_paused=0,
            started_at=_dt.datetime.utcnow().isoformat(timespec="seconds"))
        if channel:
            try: await channel.send(f"🎵 En cours : **{track['title']}**")
            except Exception: pass
    except Exception as e:
        print(f"[music] play_next error: {e}")
        music_state_clear_current(str(guild_id))


# ===== BOT =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")
    # Enregistrer chaque guild où le bot est présent + sync ses channels
    for guild in bot.guilds:
        upsert_guild(
            guild.id, guild.name,
            icon_url=str(guild.icon.url) if guild.icon else None,
            member_count=guild.member_count or 0
        )
        try:
            _sync_guild_channels(guild)
        except Exception as e:
            print(f"[channels] sync {guild.name} échoué : {e}")
        try:
            _sync_guild_members(guild)
        except Exception as e:
            print(f"[members] sync {guild.name} échoué : {e}")
    print(f"👀 {len(USER_REACTIONS)} réaction(s) chargée(s) sur {len(bot.guilds)} serveur(s)")
    if not reload_reactions.is_running():
        reload_reactions.start()
    if not process_bot_commands.is_running():
        process_bot_commands.start()
    if not daily_logs_purge.is_running():
        daily_logs_purge.start()
    if not anti_spam_cleanup.is_running():
        anti_spam_cleanup.start()
    if not status_writer.is_running():
        status_writer.start()
    # Resume music: rejoindre les vocals où on était + remettre la queue en lecture
    await _resume_music()
    await bot.tree.sync()
    print("✅ Slash commands synchronisées globalement")
    for guild in bot.guilds:
        try:
            await bot.tree.sync(guild=guild)
            print(f"✅ Sync guild : {guild.name}")
        except Exception as e:
            print(f"❌ Sync guild échouée ({guild.name}) : {e}")

def _sync_guild_members(guild):
    members = []
    for m in guild.members:
        members.append({
            "user_id":    m.id,
            "username":   str(m),
            "avatar_url": str(m.display_avatar.url) if m.display_avatar else None,
            "is_bot":     m.bot,
            "joined_at":  m.joined_at,
        })
    replace_guild_members(guild.id, members)

def _sync_guild_channels(guild):
    """Pousse les salons d'un guild dans la table guild_channels."""
    rows = []
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            ctype = "category"
        elif isinstance(ch, discord.VoiceChannel):
            ctype = "voice"
        elif isinstance(ch, discord.StageChannel):
            ctype = "stage"
        elif isinstance(ch, (discord.TextChannel,)):
            ctype = "text"
        else:
            # forum, news, etc.
            ctype = getattr(ch, "type", "text")
            ctype = str(ctype) if ctype else "text"
        rows.append({
            "channel_id": ch.id,
            "name":       ch.name,
            "type":       ctype,
            "position":   getattr(ch, "position", 0) or 0,
        })
    replace_guild_channels(guild.id, rows)

@bot.event
async def on_guild_join(guild):
    upsert_guild(guild.id, guild.name,
                 icon_url=str(guild.icon.url) if guild.icon else None,
                 member_count=guild.member_count or 0)
    _sync_guild_channels(guild)
    try:
        await bot.tree.sync(guild=guild)
    except Exception:
        pass

@bot.event
async def on_guild_remove(guild):
    mark_guild_left(guild.id)

@bot.event
async def on_guild_channel_create(channel):
    if not channel.guild: return
    ctype = "voice" if isinstance(channel, discord.VoiceChannel) else \
            "category" if isinstance(channel, discord.CategoryChannel) else \
            "text"
    upsert_channel(channel.guild.id, channel.id, channel.name, ctype, getattr(channel, "position", 0))

@bot.event
async def on_guild_channel_update(before, after):
    if not after.guild: return
    ctype = "voice" if isinstance(after, discord.VoiceChannel) else \
            "category" if isinstance(after, discord.CategoryChannel) else \
            "text"
    upsert_channel(after.guild.id, after.id, after.name, ctype, getattr(after, "position", 0))

@bot.event
async def on_guild_channel_delete(channel):
    if not channel.guild: return
    remove_channel(channel.guild.id, channel.id)


# ===== LOGS — capture des events =====
@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command):
    if not interaction.guild:
        return
    # Reconstruire la liste d'arguments lisibles
    args_str = ""
    try:
        opts = (interaction.data or {}).get("options") or []
        parts = []
        for o in opts:
            if "value" in o:
                parts.append(f"{o['name']}={o['value']}")
            elif "options" in o:
                # subcommand
                parts.append(o.get("name", ""))
                for sub in o["options"]:
                    if "value" in sub:
                        parts.append(f"{sub['name']}={sub['value']}")
        args_str = " ".join(parts)
    except Exception:
        pass
    add_log(
        interaction.guild.id, "command",
        user_id=interaction.user.id, username=str(interaction.user),
        channel_id=interaction.channel.id if interaction.channel else None,
        channel_name=getattr(interaction.channel, "name", None),
        content=f"/{command.qualified_name}" + (f" {args_str}" if args_str else ""),
    )

@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    add_log(
        message.guild.id, "action_message_delete",
        user_id=message.author.id, username=str(message.author),
        channel_id=message.channel.id, channel_name=getattr(message.channel, "name", None),
        content=message.content or "(message vide ou attachement seul)",
    )

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not after.guild or after.author.bot:
        return
    if (before.content or "") == (after.content or ""):
        return  # juste un embed/preview qui se met a jour
    add_log(
        after.guild.id, "action_message_edit",
        user_id=after.author.id, username=str(after.author),
        channel_id=after.channel.id, channel_name=getattr(after.channel, "name", None),
        content=(after.content or "")[:1000],
        meta={"before": (before.content or "")[:1000]},
    )

@bot.event
async def on_voice_state_update(member, before, after):
    if not member.guild:
        return
    if before.channel == after.channel:
        return  # mute/deaf etc., pas de move
    if before.channel is None and after.channel is not None:
        # join
        add_log(member.guild.id, "action_voice_join",
                user_id=member.id, username=str(member),
                channel_id=after.channel.id, channel_name=after.channel.name)
    elif before.channel is not None and after.channel is None:
        # leave
        add_log(member.guild.id, "action_voice_leave",
                user_id=member.id, username=str(member),
                channel_id=before.channel.id, channel_name=before.channel.name)
    else:
        # move
        add_log(member.guild.id, "action_voice_move",
                user_id=member.id, username=str(member),
                channel_id=after.channel.id, channel_name=after.channel.name,
                meta={"from": before.channel.name, "to": after.channel.name})

@bot.event
async def on_member_remove(member):
    if not member.guild:
        return
    add_log(member.guild.id, "action_member_leave",
            user_id=member.id, username=str(member),
            content=f"a quitté **{member.guild.name}**")
    try:
        remove_member(member.guild.id, member.id)
    except Exception:
        pass

@bot.event
async def on_member_update(before, after):
    if not after.guild:
        return
    try:
        upsert_member(after.guild.id, after.id, str(after),
                      avatar_url=str(after.display_avatar.url) if after.display_avatar else None,
                      is_bot=after.bot, joined_at=after.joined_at)
    except Exception:
        pass

@bot.tree.command(name="sync", description="sync les slash commands manuellement (owner uniquement)")
@commands.is_owner()
async def sync_commands(ctx):
    """Resync les slash commands manuellement (owner uniquement)."""
    await bot.tree.sync()
    for guild in bot.guilds:
        try:
            await bot.tree.sync(guild=guild)
        except Exception:
            pass
    await ctx.send("✅ Slash commands resynchronisées !")

@tasks.loop(seconds=5)
async def reload_reactions():
    global USER_REACTIONS
    USER_REACTIONS = get_all_reactions_index()

@tasks.loop(hours=24)
async def daily_logs_purge():
    """Purge quotidienne: > 90 jours OU > 5000 logs par guild. Recupere espace disque via VACUUM."""
    try:
        res = prune_logs_global(keep_per_guild=5000, max_age_days=90)
        if res["by_age"] or res["by_count"]:
            print(f"[purge] logs : -{res['by_age']} (age) -{res['by_count']} (count) + VACUUM")
    except Exception as e:
        print(f"[purge] error: {e}")
        BOT_STATE["last_error"]    = f"purge: {e}"
        BOT_STATE["last_error_at"] = _time.time()

@daily_logs_purge.before_loop
async def _before_purge():
    await bot.wait_until_ready()

@bot.event
async def on_member_join(member):
    add_log(member.guild.id, "action_member_join",
            user_id=member.id, username=str(member),
            content=f"a rejoint **{member.guild.name}**")
    # Update member cache
    try:
        upsert_member(member.guild.id, member.id, str(member),
                      avatar_url=str(member.display_avatar.url) if member.display_avatar else None,
                      is_bot=member.bot, joined_at=member.joined_at)
    except Exception:
        pass

    data = get_welcome(member.guild.id)
    if not data:
        return
    channel = bot.get_channel(data)
    if channel:
        # Welcome template configurable via settings
        template = get_setting("welcome_template",
                               "👋 Bienvenue {user} !\nBienvenue sur **{guild}** ! Tu es le membre numéro **{count}**.")
        try:
            description = template.format(
                user=member.mention, username=member.name,
                guild=member.guild.name, count=member.guild.member_count or 0,
            )
        except Exception:
            description = f"👋 Bienvenue {member.mention} !"
        # Si template contient un titre dans la 1ere ligne, sinon on garde tout en description
        title = f"👋 Bienvenue {member.name} !" if "{user}" not in template else None
        if title:
            embed = discord.Embed(title=title, description=description, color=discord.Color.green())
        else:
            embed = discord.Embed(description=description, color=discord.Color.green())
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.guild is None:
        # DM (user -> bot) : sauvegarder + laisser process_commands au cas ou
        if not message.author.bot:
            try:
                attachments = [
                    {"url": a.url, "filename": a.filename, "size": a.size,
                     "content_type": getattr(a, "content_type", None)}
                    for a in message.attachments
                ] if message.attachments else None
                save_dm(
                    user_id=message.author.id,
                    username=str(message.author),
                    direction="in",
                    content=message.content or "",
                    attachments=attachments,
                    avatar_url=str(message.author.display_avatar.url) if message.author.display_avatar else None,
                )
            except Exception as e:
                print(f"[dm] save_dm error: {e}")
        await bot.process_commands(message)
        return
    guild_id_str = str(message.guild.id)

    # Réactions automatiques per-guild
    key = (guild_id_str, message.author.id)
    if key in USER_REACTIONS:
        try:
            await message.add_reaction(USER_REACTIONS[key])
        except discord.HTTPException as e:
            print(f"❌ Erreur réaction : {e}")

    # XP per-guild
    if not message.author.bot:
        xp = get_xp(guild_id_str, message.author.id)
        old_level = get_level(xp)
        try:
            xp_min = max(0, int(get_setting("xp_min", "1")))
            xp_max = max(xp_min, int(get_setting("xp_max", "5")))
        except (TypeError, ValueError):
            xp_min, xp_max = 1, 5
        xp += random.randint(xp_min, xp_max)
        set_xp(guild_id_str, message.author.id, xp, username=message.author.name)
        new_level = get_level(xp)
        if new_level > old_level:
            level, progress_xp, needed_xp, percent = get_progress(xp)
            image = await generate_levelup_card(message.author, new_level, percent)
            await message.channel.send(
                content=f"🎉 {message.author.mention}",
                file=discord.File(image, filename="levelup.png")
            )
    await bot.process_commands(message)


# ===== RÉACTIONS AUTOMATIQUES =====

@bot.tree.command(name="reaction_add", description="Ajouter une réaction automatique à un membre (sur ce serveur)")
@app_commands.describe(membre="Le membre ciblé", emoji="L'emoji à utiliser")
@app_commands.checks.has_permissions(administrator=True)
async def reaction_add(interaction: discord.Interaction, membre: discord.Member, emoji: str):
    gid = str(interaction.guild.id)
    USER_REACTIONS[(gid, membre.id)] = emoji
    set_reaction(gid, membre.id, emoji)
    await interaction.response.send_message(f"✅ Le bot réagira avec {emoji} aux messages de **{membre.name}** (sur ce serveur).")

@reaction_add.error
async def reaction_add_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ Permission administrateur requise.", ephemeral=True)

@bot.tree.command(name="reaction_remove", description="Supprimer la réaction automatique d'un membre (sur ce serveur)")
@app_commands.describe(membre="Le membre dont supprimer la réaction")
@app_commands.checks.has_permissions(administrator=True)
async def reaction_remove(interaction: discord.Interaction, membre: discord.Member):
    gid = str(interaction.guild.id)
    key = (gid, membre.id)
    if key in USER_REACTIONS:
        del USER_REACTIONS[key]
        remove_reaction(gid, membre.id)
        await interaction.response.send_message(f"✅ Réaction supprimée pour **{membre.name}** (sur ce serveur).")
    else:
        await interaction.response.send_message(f"❌ Aucune réaction configurée pour **{membre.name}** sur ce serveur.", ephemeral=True)

@reaction_remove.error
async def reaction_remove_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ Permission administrateur requise.", ephemeral=True)

@bot.tree.command(name="reaction_list", description="Voir les réactions automatiques actives sur ce serveur")
async def reaction_list(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    guild_reactions = {uid: emo for (g, uid), emo in USER_REACTIONS.items() if g == gid}
    if not guild_reactions:
        await interaction.response.send_message("❌ Aucune réaction automatique configurée sur ce serveur.", ephemeral=True)
        return
    embed = discord.Embed(title="📋 Réactions automatiques actives", color=discord.Color.orange())
    for user_id, emoji in guild_reactions.items():
        membre = interaction.guild.get_member(user_id)
        nom = membre.name if membre else f"Inconnu ({user_id})"
        embed.add_field(name=nom, value=emoji, inline=True)
    await interaction.response.send_message(embed=embed)


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

@bot.tree.command(name="commandes", description="Recevoir la liste des commandes en MP")
async def commandes(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Liste des commandes",
        description="Toutes les commandes disponibles, par catégorie.",
        color=discord.Color.blue()
    )

    moderation = (
        "**/clear <nombre>** (supprime les N derniers messages)\n"
        "**/kick <membre> [raison]** (expulse un membre du serveur)\n"
        "**/ban <membre> [raison]** (bannit un membre du serveur)\n"
        "**/poll <question> <option1> <option2> [option3] [option4]** (crée un sondage avec réactions)\n"
        "**/setwelcome <salon>** (définit le salon de bienvenue)\n"
        "**/reaction_add <membre> <emoji>** (ajoute une réaction auto à un membre, ce serveur uniquement)\n"
        "**/reaction_remove <membre>** (supprime la réaction auto d'un membre)\n"
        "**/reaction_list** (liste les réactions auto actives sur ce serveur)"
    )
    embed.add_field(name="🛡️ Modération", value=moderation, inline=False)
    embed.add_field(name="​", value="​", inline=False)

    fun = (
        "**/8ball <question>** (la boule magique répond à ta question)\n"
        "**/dé [faces]** (lance un dé, 6 faces par défaut)\n"
        "**/coinflip** (pile ou face)\n"
        "**/blague** (raconte une blague aléatoire)\n"
        "**/ship <membre1> <membre2>** (calcule le taux de compatibilité entre deux membres)\n"
        "**/choix <options>** (le bot choisit une option parmi celles que tu donnes, séparées par |)\n"
        "**/random <min> <max>** (tire un nombre aléatoire entre deux bornes)\n"
        "**/qui <question>** (le bot désigne un membre du serveur au hasard)\n"
        "**/clap <texte>** (insère 👏 entre 👏 chaque 👏 mot)\n"
        "**/rate <truc>** (le bot note quelque chose sur 10)\n"
        "**/citation** (affiche une citation au hasard)\n"
        "**/zgeg** (mesure ton zgeg, réaction selon le résultat)"
    )
    embed.add_field(name="🎉 Fun", value=fun, inline=False)
    embed.add_field(name="​", value="​", inline=False)

    xp = (
        "**/niveau [membre]** (affiche ton niveau et XP, ou celui d'un membre)\n"
        "**/leaderboard** (top 10 XP de ce serveur)"
    )
    embed.add_field(name="⭐ Niveaux & XP", value=xp, inline=False)
    embed.add_field(name="​", value="​", inline=False)

    duel = (
        "**/duel <adversaire>** (défie un membre en duel de sabres)\n"
        "**/duel <adversaire> nerf:True** (duel équilibré, ignore niveaux et stats)\n"
        "**/profil [membre]** (affiche le profil duel d'un joueur)\n"
        "**/statpoint <stat>** (attribue un point de stat : force, agilite, defense, endurance, chance)\n"
        "**/sabre** (menu unifié : sabre équipé, collection, boutique avec navigation par boutons)\n"
        "**/historique [membre]** (affiche l'historique des duels)"
    )
    embed.add_field(name="⚔️ Duel", value=duel, inline=False)
    embed.add_field(name="​", value="​", inline=False)

    music = (
        "**/join** (rejoint ton salon vocal actuel)\n"
        "**/play <titre ou lien>** (joue une musique ou l'ajoute à la file)\n"
        "**/queue** (affiche la file d'attente musicale)\n"
        "**/skip** (passe à la musique suivante)\n"
        "**/stop** (stoppe la lecture et vide la file)\n"
        "**/leave** (déconnecte le bot du salon vocal)"
    )
    embed.add_field(name="🎵 Musique", value=music, inline=False)
    embed.add_field(name="​", value="​", inline=False)

    utils = (
        "**/avatar [membre]** (affiche l'avatar d'un membre)\n"
        "**/userinfo [membre]** (informations détaillées sur un membre)\n"
        "**/serverinfo** (informations détaillées sur le serveur)\n"
        "**/ping** (affiche la latence du bot)\n"
        "**/commandes** (envoie cette liste en message privé)"
    )
    embed.add_field(name="🔧 Utilitaires", value=utils, inline=False)

    embed.set_footer(text="Tip : tape / dans le chat pour voir l'autocomplete Discord.")

    # Tente l'envoi en MP, fallback ephemere si DMs fermés
    try:
        await interaction.user.send(embed=embed)
        await interaction.response.send_message(
            "📩 La liste des commandes vient de t'être envoyée en message privé.",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Impossible d'envoyer un MP : tu as peut-être désactivé les MP de ce serveur.\n"
            "Active-les dans les paramètres Discord puis relance la commande.",
            ephemeral=True
        )


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


@bot.tree.command(name="ship", description="Calcule le taux de compatibilité entre deux membres")
@app_commands.describe(membre1="Premier membre", membre2="Deuxième membre")
async def ship(interaction: discord.Interaction, membre1: discord.Member, membre2: discord.Member):
    if membre1.id == membre2.id:
        await interaction.response.send_message("❌ Tu ne peux pas ship quelqu'un avec lui-même !", ephemeral=True)
        return
    # Deterministic : meme couple = meme score, ordre indifferent
    import hashlib
    pair = tuple(sorted([str(membre1.id), str(membre2.id)]))
    seed = int(hashlib.sha256(f"{pair[0]}:{pair[1]}".encode()).hexdigest(), 16)
    pct = seed % 101
    # Nom fusion
    n1, n2 = membre1.display_name, membre2.display_name
    fused = n1[:max(2, len(n1)//2)] + n2[max(1, len(n2)//2):]
    # Verdict + couleur
    if   pct >= 90: verdict, col = "Âmes sœurs.", discord.Color.from_rgb(220, 50, 80)
    elif pct >= 70: verdict, col = "Belle alchimie.", discord.Color.from_rgb(230, 100, 130)
    elif pct >= 50: verdict, col = "Ça pourrait marcher.", discord.Color.from_rgb(200, 140, 160)
    elif pct >= 25: verdict, col = "Mouais.", discord.Color.from_rgb(150, 150, 150)
    else:           verdict, col = "Catastrophe.", discord.Color.from_rgb(120, 120, 120)
    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    embed = discord.Embed(title=f"💘 {n1} × {n2}", color=col)
    embed.add_field(name="Compatibilité", value=f"`{bar}` **{pct}%**", inline=False)
    embed.add_field(name="Nom fusion", value=f"**{fused}**", inline=True)
    embed.add_field(name="Verdict", value=verdict, inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="choix", description="Le bot choisit pour toi entre plusieurs options")
@app_commands.describe(options="Tes options séparées par des | (ex: pizza | sushi | burger)")
async def choix(interaction: discord.Interaction, options: str):
    items = [o.strip() for o in options.split("|") if o.strip()]
    if len(items) < 2:
        await interaction.response.send_message("❌ Donne au moins 2 options séparées par `|` (ex: `option1 | option2`).", ephemeral=True)
        return
    if len(items) > 20:
        await interaction.response.send_message("❌ Maximum 20 options.", ephemeral=True)
        return
    pick = random.choice(items)
    embed = discord.Embed(title="🎯 Le bot a choisi", color=discord.Color.teal())
    embed.add_field(name="Options", value=" · ".join(f"`{o}`" for o in items), inline=False)
    embed.add_field(name="→ Choix", value=f"**{pick}**", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="random", description="Tire un nombre aléatoire entre deux bornes")
@app_commands.describe(min="Borne min (inclus)", max="Borne max (inclus)")
async def random_cmd(interaction: discord.Interaction, min: int, max: int):
    if min > max:
        min, max = max, min
    if max - min > 1_000_000_000:
        await interaction.response.send_message("❌ Plage trop grande (max 1 milliard).", ephemeral=True)
        return
    n = random.randint(min, max)
    await interaction.response.send_message(f"🎲 Entre **{min}** et **{max}** → **{n}**")

@bot.tree.command(name="qui", description="Le bot désigne un membre du serveur au hasard")
@app_commands.describe(question="La question (ex: qui paie le café ?)")
async def qui(interaction: discord.Interaction, question: str):
    members = [m for m in interaction.guild.members if not m.bot]
    if not members:
        await interaction.response.send_message("❌ Aucun membre humain trouvé sur ce serveur.", ephemeral=True)
        return
    pick = random.choice(members)
    embed = discord.Embed(color=discord.Color.gold())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="→ Désigné", value=pick.mention, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clap", description="Met 👏 entre 👏 chaque 👏 mot")
@app_commands.describe(texte="Le texte à transformer")
async def clap(interaction: discord.Interaction, texte: str):
    if len(texte) > 800:
        await interaction.response.send_message("❌ Texte trop long (max 800 caractères).", ephemeral=True)
        return
    out = " 👏 ".join(texte.split())
    if not out:
        await interaction.response.send_message("❌ Texte vide.", ephemeral=True)
        return
    await interaction.response.send_message(out)

@bot.tree.command(name="rate", description="Le bot note quelque chose sur 10")
@app_commands.describe(truc="Ce que tu veux noter")
async def rate(interaction: discord.Interaction, truc: str):
    import hashlib
    seed = int(hashlib.sha256(truc.lower().strip().encode()).hexdigest(), 16)
    note = seed % 11  # 0 a 10
    if   note >= 9: avis = "Chef-d'œuvre."
    elif note >= 7: avis = "Solide."
    elif note >= 5: avis = "Honnête."
    elif note >= 3: avis = "Mitigé."
    else:           avis = "Bof."
    bar = "★" * note + "☆" * (10 - note)
    embed = discord.Embed(title=f"📊 Évaluation : {truc[:80]}", color=discord.Color.blue())
    embed.add_field(name="Note", value=f"{bar}\n**{note}/10** — {avis}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="citation", description="Affiche une citation au hasard")
async def citation(interaction: discord.Interaction):
    citations = [
        ("Le doute est le commencement de la sagesse.", "Aristote"),
        ("Connais-toi toi-même.", "Socrate"),
        ("Ce qui ne nous tue pas nous rend plus forts.", "Friedrich Nietzsche"),
        ("La vie est un mystère qu'il faut vivre, et non un problème à résoudre.", "Gandhi"),
        ("Sois le changement que tu veux voir dans le monde.", "Gandhi"),
        ("Je pense, donc je suis.", "René Descartes"),
        ("L'imagination est plus importante que le savoir.", "Albert Einstein"),
        ("La simplicité est la sophistication suprême.", "Léonard de Vinci"),
        ("On ne voit bien qu'avec le cœur. L'essentiel est invisible pour les yeux.", "Saint-Exupéry"),
        ("Faites de votre vie un rêve, et d'un rêve, une réalité.", "Saint-Exupéry"),
        ("Le succès, c'est aller d'échec en échec sans perdre son enthousiasme.", "Winston Churchill"),
        ("Celui qui déplace une montagne commence par déplacer les petites pierres.", "Confucius"),
        ("Choisis un travail que tu aimes et tu n'auras pas à travailler un seul jour.", "Confucius"),
        ("La meilleure façon de prédire l'avenir, c'est de le créer.", "Peter Drucker"),
        ("Le voyage de mille lieues commence par un seul pas.", "Lao Tseu"),
        ("Tout ce qui ne tue pas une idée la rend plus forte.", "Anonyme"),
        ("Ne demande pas ce que ton pays peut faire pour toi, demande ce que tu peux faire pour ton pays.", "John F. Kennedy"),
        ("La folie, c'est de faire toujours la même chose et d'attendre un résultat différent.", "Albert Einstein"),
        ("Vivre, c'est naître à chaque instant.", "Erich Fromm"),
        ("L'échec est le fondement de la réussite.", "Lao Tseu"),
    ]
    texte, auteur = random.choice(citations)
    embed = discord.Embed(description=f"_« {texte} »_", color=discord.Color.dark_grey())
    embed.set_footer(text=f"— {auteur}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="zgeg", description="Mesure ton zgeg")
async def zgeg(interaction: discord.Interaction):
    taille = random.randint(1, 25)
    if taille >= 23:
        reaction = "Wow ! Impressionnant !! 🍆🔥 Une légende vivante."
    elif taille >= 19:
        reaction = "Pas mal du tout, monsieur. 😏"
    elif taille >= 15:
        reaction = "Honnête. Solide même. 👍"
    elif taille >= 11:
        reaction = "Dans la moyenne. Rien à signaler. 🤷"
    elif taille >= 7:
        reaction = "Bon... ça reste utilisable. 😬"
    elif taille >= 4:
        reaction = "Ahah... seulement... 😅"
    else:
        reaction = "Mes condoléances. 💀"
    await interaction.response.send_message(
        f"📏 Ton zgeg mesure **{taille} cm**. {reaction}"
    )


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

@bot.tree.command(name="niveau", description="Voir ton niveau et XP (sur ce serveur)")
@app_commands.describe(membre="Le membre dont tu veux voir le niveau")
async def niveau(interaction: discord.Interaction, membre: discord.Member = None):
    membre = membre or interaction.user
    gid = str(interaction.guild.id)
    xp = get_xp(gid, membre.id)
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

@bot.tree.command(name="leaderboard", description="Classement XP de ce serveur")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    gid = str(interaction.guild.id)
    sorted_users = get_leaderboard(gid, limit=10)
    if not sorted_users:
        await interaction.followup.send("Personne n'a encore d'XP sur ce serveur.")
        return
    embed = discord.Embed(title=f"🏆 Classement XP — {interaction.guild.name}", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    description = ""
    for i, row in enumerate(sorted_users):
        try:
            user = await bot.fetch_user(int(row["user_id"]))
            name = user.name
        except Exception:
            name = row.get("username") or "Utilisateur inconnu"
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        description += f"{medal} {name} — **{row['xp']} XP** (Niveau {row['level']})\n"
    embed.description = description
    await interaction.followup.send(embed=embed)


# ===== MUSIQUE =====

def _ensure_opus():
    """Ensure libopus is loaded. Re-tries load if not. Returns True on success."""
    if discord.opus.is_loaded():
        return True
    return _load_opus()

@bot.tree.command(name="join", description="Rejoindre ton salon vocal")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        if not _ensure_opus():
            await interaction.followup.send("❌ libopus introuvable sur le serveur. Installe-la (`apt install libopus0`) et redémarre le bot.")
            return
        channel = interaction.user.voice.channel
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(channel)
        else:
            await channel.connect()
        music_state_set(str(interaction.guild.id),
                        voice_channel_id=str(channel.id),
                        voice_channel_name=channel.name)
        await interaction.followup.send(f"✅ Connecté à **{channel.name}** !")
    except Exception as e:
        import traceback
        print(f"[music /join] error: {e}")
        traceback.print_exc()
        await interaction.followup.send(f"❌ Erreur connexion vocal : {type(e).__name__} — {e}")

@bot.tree.command(name="play", description="Jouer une musique")
@app_commands.describe(query="Titre ou lien YouTube")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ Tu dois être dans un salon vocal !", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        if not _ensure_opus():
            await interaction.followup.send("❌ libopus introuvable sur le serveur.")
            return
        if not interaction.guild.voice_client:
            await interaction.user.voice.channel.connect()
            music_state_set(str(interaction.guild.id),
                            voice_channel_id=str(interaction.user.voice.channel.id),
                            voice_channel_name=interaction.user.voice.channel.name)
        gid = str(interaction.guild.id)
        await interaction.followup.send(f"🔍 Recherche de **{query}**...")
        try:
            info = await get_audio_info(query)
        except Exception as e:
            print(f"[music] yt-dlp error: {e}")
            await interaction.followup.send(f"❌ Erreur lors de la recherche : {e}")
            return
        music_queue_add(gid,
                        title=info["title"], url=info["url"],
                        source_url=info.get("source_url"),
                        duration=info.get("duration"),
                        thumbnail=info.get("thumbnail"),
                        requested_by=interaction.user.id)
        await interaction.followup.send(f"✅ Ajouté à la file : **{info['title']}**")
        if not interaction.guild.voice_client.is_playing():
            await play_next(interaction.guild.voice_client, interaction.channel, interaction.guild.id)
    except Exception as e:
        import traceback
        print(f"[music /play] error: {e}")
        traceback.print_exc()
        try:
            await interaction.followup.send(f"❌ Erreur interne : {type(e).__name__} — {e}")
        except Exception:
            pass

@bot.tree.command(name="skip", description="Passer à la musique suivante")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("⏭️ Musique passée !")
    else:
        await interaction.response.send_message("❌ Aucune musique en cours !", ephemeral=True)

@bot.tree.command(name="queue", description="Voir la file d'attente musicale")
async def queue_cmd(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    q = music_queue_list(gid)
    if not q:
        await interaction.response.send_message("📭 La file d'attente est vide !")
        return
    embed = discord.Embed(title="🎵 File d'attente", color=discord.Color.blurple())
    description = ""
    for i, t in enumerate(q):
        description += f"**{i+1}.** {t['title']}\n"
    embed.description = description
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stop", description="Stopper la musique et vider la file")
async def stop(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    music_queue_clear(gid)
    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
    music_state_clear_current(gid)
    await interaction.response.send_message("⏹️ Musique stoppée et file vidée !")

@bot.tree.command(name="leave", description="Quitter le salon vocal")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        gid = str(interaction.guild.id)
        music_queue_clear(gid)
        await interaction.guild.voice_client.disconnect()
        music_state_disconnect(gid)
        await interaction.response.send_message("👋 Déconnecté du salon vocal !")
    else:
        await interaction.response.send_message("❌ Je ne suis pas dans un salon vocal !", ephemeral=True)


async def _resume_music():
    """Au boot, rejoindre les vocals connus + relancer la queue si elle n'est pas vide.
    Lit music_state pour chaque guild où le bot est présent."""
    from database import music_state_get, music_state_disconnect, music_queue_list
    for guild in bot.guilds:
        gid = str(guild.id)
        st = music_state_get(gid)
        if not st:
            continue
        ch_id = st.get("voice_channel_id")
        if not ch_id:
            continue
        # Le voice channel existe-t-il toujours ?
        channel = guild.get_channel(int(ch_id))
        if not channel or not isinstance(channel, discord.VoiceChannel):
            music_state_disconnect(gid)
            continue
        try:
            vc = await channel.connect()
            print(f"[resume] {guild.name} : reconnecté à {channel.name}")
            # Relancer la queue si non vide
            if music_queue_list(gid):
                await play_next(vc, None, guild.id)
        except Exception as e:
            print(f"[resume] {guild.name} : échec reconnexion {channel.name}: {e}")
            music_state_disconnect(gid)


# ===== ANTI-SPAM SLASH COMMANDS =====
# Limite : max N commandes par user dans une fenetre glissante
import collections as _col
_USER_CMD_TIMES = _col.defaultdict(list)  # user_id -> [timestamp, ...]
_RATE_LIMIT_N      = 6        # 6 commandes
_RATE_LIMIT_WINDOW = 30.0     # par 30 secondes

def _is_rate_limited(user_id):
    now = _time.time()
    bucket = _USER_CMD_TIMES[user_id]
    # purge entries hors fenetre
    cutoff = now - _RATE_LIMIT_WINDOW
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= _RATE_LIMIT_N:
        return True, _RATE_LIMIT_WINDOW - (now - bucket[0])
    bucket.append(now)
    return False, 0.0

async def _global_rate_limit(interaction: discord.Interaction) -> bool:
    """Refuse les interactions si l'user spam les slash commands."""
    # Bypass pour le proprietaire (toujours autorise)
    try:
        if await bot.is_owner(interaction.user):
            return True
    except Exception:
        pass
    limited, retry = _is_rate_limited(interaction.user.id)
    if limited:
        try:
            await interaction.response.send_message(
                f"⏱️ Tu envoies des commandes trop vite. Réessaie dans **{int(retry) + 1}s**.",
                ephemeral=True,
            )
        except Exception:
            pass
        return False
    return True

# Hook global rate-limit sur l'arbre des slash commands
bot.tree.interaction_check = _global_rate_limit

@tasks.loop(seconds=15)
async def status_writer():
    _write_bot_state()

@status_writer.before_loop
async def _before_status_writer():
    await bot.wait_until_ready()

@tasks.loop(minutes=10)
async def anti_spam_cleanup():
    """Purge les buckets vides toutes les 10 min (sinon dict grossit indefiniment)."""
    now = _time.time()
    cutoff = now - _RATE_LIMIT_WINDOW
    to_del = [uid for uid, ts in _USER_CMD_TIMES.items() if not ts or ts[-1] < cutoff]
    for uid in to_del:
        _USER_CMD_TIMES.pop(uid, None)

@anti_spam_cleanup.before_loop
async def _before_spam_cleanup():
    await bot.wait_until_ready()


# ===== ERROR CAPTURE GLOBAL =====
@bot.event
async def on_error(event_method, *args, **kwargs):
    import traceback
    tb = traceback.format_exc()
    print(f"[on_error] event={event_method}\n{tb}")
    BOT_STATE["last_error"]    = f"{event_method}: {tb.splitlines()[-1] if tb else 'unknown'}"[:200]
    BOT_STATE["last_error_at"] = _time.time()

@bot.tree.error
async def _slash_cmd_error(interaction: discord.Interaction, error):
    import traceback
    tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    print(f"[slash error] {tb_str}")
    BOT_STATE["last_error"]    = f"slash: {type(error).__name__}: {error}"[:200]
    BOT_STATE["last_error_at"] = _time.time()
    try:
        msg = f"❌ Erreur : {type(error).__name__}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ===== WORKER : commandes web -> bot (polling 1.5s) =====
@tasks.loop(seconds=1.5)
async def process_bot_commands():
    pending = bot_command_fetch_pending(limit=10)
    if not pending:
        return
    for cmd in pending:
        try:
            await _dispatch_bot_command(cmd)
            bot_command_finish(cmd["id"], "done")
        except Exception as e:
            print(f"[bot_commands] error on {cmd['cmd']}: {e}")
            bot_command_finish(cmd["id"], "error", str(e)[:300])

async def _dispatch_bot_command(cmd):
    gid = cmd["guild_id"]
    name = cmd["cmd"]
    payload = cmd.get("payload") or {}

    # Commandes globales (sans guild requis)
    if name == "dm_send":
        # payload: {user_id, content}
        user_id = payload.get("user_id")
        content = (payload.get("content") or "").strip()
        if not user_id or not content:
            raise ValueError("user_id et content requis")
        if len(content) > 2000:
            content = content[:1997] + "..."
        try:
            user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
        except Exception as e:
            raise RuntimeError(f"user introuvable: {e}")
        if not user:
            raise RuntimeError("user introuvable")
        try:
            await user.send(content)
        except discord.Forbidden:
            raise RuntimeError("user a ferme ses DMs ou n'a pas de serveur en commun")
        save_dm(
            user_id=user.id,
            username=str(user),
            direction="out",
            content=content,
            avatar_url=str(user.display_avatar.url) if user.display_avatar else None,
        )
        return

    guild = bot.get_guild(int(gid))
    if not guild:
        raise RuntimeError(f"guild {gid} introuvable (bot pas dans ce serveur ?)")
    vc = guild.voice_client

    if name == "music_play":
        # payload: {query, voice_channel_id (optional)}
        if not _ensure_opus():
            raise RuntimeError("libopus pas chargee sur le serveur")
        query = payload.get("query")
        if not query:
            raise ValueError("query manquant")
        if not vc:
            ch_id = payload.get("voice_channel_id")
            if ch_id:
                channel = guild.get_channel(int(ch_id))
                if channel and isinstance(channel, discord.VoiceChannel):
                    vc = await channel.connect()
                    music_state_set(gid, voice_channel_id=str(channel.id), voice_channel_name=channel.name)
                else:
                    raise ValueError("salon vocal introuvable")
            else:
                # Premier salon vocal disponible
                vchan = next((c for c in guild.voice_channels), None)
                if not vchan:
                    raise ValueError("aucun salon vocal disponible")
                vc = await vchan.connect()
                music_state_set(gid, voice_channel_id=str(vchan.id), voice_channel_name=vchan.name)
        info = await get_audio_info(query)
        music_queue_add(gid,
                        title=info["title"], url=info["url"],
                        source_url=info.get("source_url"),
                        duration=info.get("duration"),
                        thumbnail=info.get("thumbnail"),
                        requested_by="web")
        if not vc.is_playing():
            await play_next(vc, None, int(gid))

    elif name == "music_skip":
        if vc and vc.is_playing():
            vc.stop()  # triggers play_next via after callback

    elif name == "music_stop":
        music_queue_clear(gid)
        if vc:
            vc.stop()
        music_state_clear_current(gid)

    elif name == "music_pause":
        if vc and vc.is_playing():
            vc.pause()
            music_state_set(gid, is_paused=1, is_playing=0)

    elif name == "music_resume":
        if vc and vc.is_paused():
            vc.resume()
            music_state_set(gid, is_paused=0, is_playing=1)

    elif name == "music_join":
        ch_id = payload.get("voice_channel_id")
        if not ch_id:
            raise ValueError("voice_channel_id manquant")
        channel = guild.get_channel(int(ch_id))
        if not channel:
            raise ValueError("salon vocal introuvable")
        if vc:
            await vc.move_to(channel)
        else:
            await channel.connect()
        music_state_set(gid, voice_channel_id=str(channel.id), voice_channel_name=channel.name)

    elif name == "music_leave":
        if vc:
            music_queue_clear(gid)
            await vc.disconnect()
            music_state_disconnect(gid)

    elif name == "music_remove_track":
        from database import music_queue_remove
        track_id = payload.get("track_id")
        if track_id is not None:
            music_queue_remove(gid, track_id)

    elif name == "music_clear":
        music_queue_clear(gid)

    elif name in ("mod_kick", "mod_ban", "mod_timeout", "mod_unban"):
        target_id = payload.get("user_id")
        reason    = (payload.get("reason") or "Action depuis le dashboard").strip()
        if not target_id:
            raise ValueError("user_id requis")
        if name == "mod_unban":
            try:
                user = await bot.fetch_user(int(target_id))
                await guild.unban(user, reason=reason)
            except discord.NotFound:
                raise RuntimeError("user non banni ou inconnu")
            return
        member = guild.get_member(int(target_id))
        if not member:
            try:
                member = await guild.fetch_member(int(target_id))
            except Exception:
                raise RuntimeError("membre introuvable sur ce serveur")
        if name == "mod_kick":
            await member.kick(reason=reason)
        elif name == "mod_ban":
            delete_seconds = int(payload.get("delete_seconds", 0) or 0)
            await guild.ban(member, reason=reason, delete_message_seconds=delete_seconds)
        elif name == "mod_timeout":
            duration_min = int(payload.get("duration_minutes", 10) or 10)
            until = discord.utils.utcnow() + _dt.timedelta(minutes=duration_min)
            await member.timeout(until, reason=reason)
        # Log côté bot
        add_log(guild.id, f"action_{name}",
                user_id=target_id, username=str(member) if 'member' in dir() and member else target_id,
                content=reason,
                meta={"by": "dashboard"})
        return

    elif name == "bot_say":
        # payload: {channel_id, content, embed?}
        ch_id = payload.get("channel_id")
        content = (payload.get("content") or "").strip()
        embed_data = payload.get("embed")
        if not ch_id:
            raise ValueError("channel_id requis")
        if not content and not embed_data:
            raise ValueError("content ou embed requis")
        channel = guild.get_channel(int(ch_id))
        if not channel:
            raise ValueError("salon introuvable")
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise ValueError("le salon n'est pas textuel")
        if len(content) > 2000:
            content = content[:1997] + "..."

        embed_obj = None
        if embed_data and isinstance(embed_data, dict):
            try:
                color_hex = (embed_data.get("color") or "").lstrip("#")
                color_int = int(color_hex, 16) if color_hex else None
            except ValueError:
                color_int = None
            embed_obj = discord.Embed(
                title=(embed_data.get("title") or None),
                description=(embed_data.get("description") or None),
                url=(embed_data.get("url") or None),
                color=color_int,
            )
            author_name = embed_data.get("author_name")
            if author_name:
                embed_obj.set_author(
                    name=author_name,
                    url=embed_data.get("author_url") or None,
                    icon_url=embed_data.get("author_icon") or None,
                )
            footer_text = embed_data.get("footer_text")
            if footer_text:
                embed_obj.set_footer(
                    text=footer_text,
                    icon_url=embed_data.get("footer_icon") or None,
                )
            if embed_data.get("image"):
                embed_obj.set_image(url=embed_data["image"])
            if embed_data.get("thumbnail"):
                embed_obj.set_thumbnail(url=embed_data["thumbnail"])
            for f in (embed_data.get("fields") or [])[:25]:
                fname  = (f.get("name")  or "").strip()
                fvalue = (f.get("value") or "").strip()
                if fname and fvalue:
                    embed_obj.add_field(
                        name=fname[:256],
                        value=fvalue[:1024],
                        inline=bool(f.get("inline")),
                    )
            if embed_data.get("timestamp"):
                embed_obj.timestamp = discord.utils.utcnow()

        await channel.send(content=content or None, embed=embed_obj)

    else:
        raise ValueError(f"commande inconnue: {name}")


# ===== LANCEMENT =====
db = DuelDB()
setup_duel_commands(bot, db)
bot.run(TOKEN)