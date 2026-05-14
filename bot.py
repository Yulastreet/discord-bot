import os
# Nettoyer les env vars Node IPC heritees de pm2 â€” sinon Deno (utilise par yt-dlp
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
import importlib
import importlib.metadata
from dotenv import load_dotenv


def _module_version(name):
    try:
        return importlib.metadata.version(name)
    except Exception:
        return None


def _module_file(name):
    try:
        module = importlib.import_module(name)
        return getattr(module, "__file__", None)
    except Exception as exc:
        return f"missing ({type(exc).__name__}: {exc})"


print(
    "[runtime] "
    f"python={os.sys.executable} "
    f"discord.py={getattr(discord, '__version__', None)} "
    f"discord_file={getattr(discord, '__file__', None)} "
    f"davey={_module_version('davey')} "
    f"davey_file={_module_file('davey')} "
    f"PyNaCl={_module_version('PyNaCl')} "
    f"nacl_file={_module_file('nacl')}"
)

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
    print("[opus] FAILED to load â€” install libopus0 (apt) or libopus (brew)")
    return False

_load_opus()

def _ensure_opus():
    if discord.opus.is_loaded():
        return True
    return _load_opus()

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
                      replace_guild_members, upsert_member, remove_member,
                      upsert_entitlement, mark_entitlement_deleted,
                      user_has_active_entitlement, get_premium_settings,
                      user_is_premium as _db_user_is_premium,
                      user_has_active_pass,
                      get_or_create_current_season, get_pass_progress,
                      list_user_active_quests, increment_quest_progress,
                      claim_quest_reward, add_pass_xp, set_pass_claimed_tier,
                      auto_claim_pass_tiers, get_active_xp_boost_multiplier,
                      list_user_pass_unlocks, get_user_cosmetic,
                      reaction_role_add as db_rr_add,
                      reaction_role_remove as db_rr_remove,
                      reaction_role_remove_message as db_rr_remove_msg,
                      reaction_role_get as db_rr_get,
                      reaction_role_list as db_rr_list,
                      reaction_role_list_unique_group as db_rr_list_unique,
                      replace_guild_roles,
                      social_alert_create, social_alert_delete,
                      social_alert_update_seen, social_alerts_list,
                      social_alert_touch_check,
                      ticket_panel_create, ticket_panel_set_message,
                      ticket_panel_get, ticket_panel_get_by_message,
                      ticket_panels_list, ticket_panel_delete,
                      ticket_create, ticket_get_by_channel,
                      ticket_get_open_by_user, ticket_set_claimed,
                      ticket_set_status, tickets_list)
import social_integrations as social
from commandes import setup_commands
from duel_commands import setup_duel_commands
from niveau_card import render_niveau_card, render_levelup_card_premium, preload_backgrounds
from services.emoji import parse_emoji_input as _parse_emoji_input
from status_utils import best_firefox_cookie_profile
from tasks.runtime import setup_runtime
from welcome_utils import DEFAULT_WELCOME_MESSAGE, build_welcome_send_kwargs

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ===== MONETIZATION =====
# SKUs Discord. Remplis ces env apres avoir cree les SKU dans le Dev Portal :
#   SKU_NIVEAU_PREMIUM = SKU "Durable" (achat unique 1.99 USD) /niveau Premium
#   SKU_PASS           = SKU "Subscription" (recurrent 3.99 EUR / mois) Battle Pass
SKU_NIVEAU_PREMIUM = os.getenv("SKU_NIVEAU_PREMIUM", "").strip() or None
SKU_PASS           = os.getenv("SKU_PASS", "").strip() or None
DISCORD_OWNER_ID   = os.getenv("DISCORD_OWNER_ID", "").strip() or None


# Constantes Pass : XP par palier, total paliers
PASS_TIERS = 30
PASS_XP_PER_TIER = 250
PASS_XP_TOTAL = PASS_TIERS * PASS_XP_PER_TIER  # 7500


def pass_tier_from_xp(xp: int) -> int:
    """Renvoie le palier debloque par cet XP (cap a PASS_TIERS)."""
    if xp <= 0:
        return 0
    return min(PASS_TIERS, xp // PASS_XP_PER_TIER)


def _track_pass_quest(user_id, quest_type: str, amount: int = 1):
    """Helper : incremente progress + claim auto + add XP au Pass.

    Silencieux si l'user n'a pas de Pass actif (no-op).
    """
    if not user_id:
        return
    has_pass = user_has_active_pass(user_id, sku_pass_id=SKU_PASS) or (
        DISCORD_OWNER_ID and str(user_id) == str(DISCORD_OWNER_ID)
    )
    if not has_pass:
        return
    try:
        # S'assure que les quetes de la periode courante existent (lazy generate).
        from database import list_user_active_quests as _lq
        _lq(user_id)
        completed = increment_quest_progress(user_id, quest_type, amount)
        if not completed:
            return
        # Auto-claim + credit XP saison courante + auto-claim paliers
        season = get_or_create_current_season()
        sid = season["season_id"]
        new_total_xp = None
        for q in completed:
            claim = claim_quest_reward(user_id, q["period"], q["slot"])
            if claim:
                new_total_xp = add_pass_xp(user_id, sid, claim["xp_reward"])
                print(f"[pass] user={user_id} clear quest {q['type']} +{claim['xp_reward']} XP")
        if new_total_xp is not None:
            delivered = auto_claim_pass_tiers(
                user_id, sid, new_total_xp,
                tier_xp=PASS_XP_PER_TIER, max_tier=PASS_TIERS,
            )
            for d in delivered:
                print(f"[pass] user={user_id} unlock tier {d['tier']} ({d['type']}: {d.get('label')})")
    except Exception as e:
        print(f"[pass] track error: {e!r}")


def is_premium_user(user_id, feature="all") -> bool:
    """Retourne True si l'utilisateur a la feature premium :
    - via entitlement Discord (achat reel : /niveau Premium ou Pass)
    - via grant manuel offert (table premium_grants)
    - via DISCORD_OWNER_ID (toujours premium gratuit)
    - via Pass actif (les abonnes Pass ont automatiquement le pack premium)
    """
    if DISCORD_OWNER_ID and user_id and str(user_id) == str(DISCORD_OWNER_ID):
        return True
    if _db_user_is_premium(user_id, feature=feature, owner_id=DISCORD_OWNER_ID):
        return True
    if feature == "all" and user_id and user_has_active_pass(user_id, sku_pass_id=SKU_PASS):
        return True
    return False

init_db()
# Preload backgrounds /niveau premium en RAM (~3MB total) pour eliminer
# la latence de decode disque au premier appel.
try:
    preload_backgrounds()
except Exception as _e:
    print(f"[niveau_card] preload error: {_e!r}")
# Index reactions par (guild_id_str, user_id_int)
USER_REACTIONS = get_all_reactions_index()

# ===== Etat runtime exposÃ© pour /api/status =====
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
        _bot = globals().get("bot")
        BOT_STATE["updated_at"]  = _time.time()
        BOT_STATE["guild_count"] = len(_bot.guilds) if _bot and _bot.is_ready() else 0
        BOT_STATE["voice_count"] = sum(1 for g in _bot.guilds if g.voice_client) if _bot and _bot.is_ready() else 0
        BOT_STATE["latency_ms"]  = round(_bot.latency * 1000) if _bot and _bot.is_ready() else None
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
        """Attribue 1 point Ã  une stat. Retourne True si succÃ¨s."""
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

# PO Token provider (bgutil) â€” bypass anti-bot YouTube sans cookies.
# Endpoint configurable via env BGUTIL_POT_URL (defaut: container Docker local).
_BGUTIL_POT_URL = os.getenv("BGUTIL_POT_URL", "http://127.0.0.1:4416")

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
    # Clients YouTube : web/mweb consomment le po_token fourni par bgutil.
    # Fallbacks ios/android/tv pour cas ou web echoue.
    'extractor_args': {
        'youtube': {
            'player_client': ['tv_simply', 'web_safari', 'web_embedded', 'web', 'mweb', 'tv', 'ios', 'android'],
        },
        'youtubepot-bgutilhttp': {
            'base_url': [_BGUTIL_POT_URL],
        },
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
}

# Cookies : priorite Firefox live (auto-rotation par le browser sur le VPS),
# fallback cookies.txt manuel.
_USE_FIREFOX_COOKIES = os.getenv("YT_USE_FIREFOX_COOKIES", "1") == "1"
_FIREFOX_COOKIE_PROFILE = os.getenv("YT_FIREFOX_PROFILE") or best_firefox_cookie_profile()
if _USE_FIREFOX_COOKIES:
    # yt-dlp lit cookies.sqlite live du profil par defaut.
    YDL_OPTIONS['cookiesfrombrowser'] = ('firefox', _FIREFOX_COOKIE_PROFILE) if _FIREFOX_COOKIE_PROFILE else ('firefox',)
    print(f"[yt-dlp] cookies-from-browser: firefox ({_FIREFOX_COOKIE_PROFILE or 'profil par defaut'})")
elif os.path.exists(_COOKIES_PATH):
    YDL_OPTIONS['cookiefile'] = _COOKIES_PATH
    print(f"[yt-dlp] cookies loaded from {_COOKIES_PATH}")
else:
    print(f"[yt-dlp] aucun cookies.txt detecte ({_COOKIES_PATH}) â€” bgutil pot provider doit suffire.")

print(f"[yt-dlp] bgutil pot provider endpoint: {_BGUTIL_POT_URL}")
BOT_STATE["youtube"] = {
    "yt_use_firefox_cookies": _USE_FIREFOX_COOKIES,
    "bgutil_pot_url": _BGUTIL_POT_URL,
    "cookies_path": _COOKIES_PATH,
    "cookies_txt_exists": os.path.exists(_COOKIES_PATH),
    "firefox_profile": _FIREFOX_COOKIE_PROFILE,
    "effective_mode": "firefox" if _USE_FIREFOX_COOKIES else ("cookies.txt" if os.path.exists(_COOKIES_PATH) else "bgutil_only"),
}

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
    if not voice_client or not voice_client.is_connected():
        print(f"[music] play_next skipped: voice client not connected (guild={guild_id})")
        if channel:
            try:
                await channel.send("❌ Je ne suis plus connecté au vocal. Relance `/join` puis `/play`.")
            except Exception:
                pass
        return

    track = music_queue_pop_next(str(guild_id))
    if not track:
        music_state_clear_current(str(guild_id))
        if channel:
            try: await channel.send("âœ… File d'attente terminÃ©e !")
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
            try: await channel.send(f"ðŸŽµ En cours : **{track['title']}**")
            except Exception: pass
    except Exception as e:
        print(f"[music] play_next error: {e}")
        music_state_clear_current(str(guild_id))


# ===== BOT =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ===== LANCEMENT =====
db = DuelDB()
COMMAND_HOOKS = setup_commands(bot, USER_REACTIONS, globals())
MUSIC_RESUME = COMMAND_HOOKS.get("resume_music")
setup_duel_commands(bot, db)
setup_runtime(bot, globals())
bot.run(TOKEN)
