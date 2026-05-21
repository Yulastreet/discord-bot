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

from cards.rank import generate_levelup_card, generate_rank_card
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
                      prune_logs_global,
                      get_setting, get_all_settings,
                      guild_setting_get, guild_setting_set,
                      daily_claim_get, daily_claim_apply,
                      promo_code_create, promo_code_get, promo_codes_list,
                      promo_code_delete, promo_redeem_check, promo_redeem_apply,
                      lol_profile_get, lol_profile_upsert, lol_profile_unlink,
                      lol_rank_config_get, lol_rank_config_upsert,
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
from services import social
from commandes import setup_commands
from duel.commands import setup_duel_commands
from commandes.cs2 import (setup_cs2_commands,
                           on_voice_state_update as cs2_on_voice,
                           queue_cleanup_sweep as cs2_queue_sweep)
from commandes.moderation_pro import setup_mod_commands
from commandes.giveaway import (setup_giveaway_commands,
                                giveaway_finalize_sweep as _gw_sweep)
from commandes.custom_cmd import setup_custom_cmd_commands
from commandes.lol import setup_lol_commands
from cards.niveau import render_niveau_card, render_levelup_card_premium, preload_backgrounds
from services.emoji import parse_emoji_input as _parse_emoji_input
from services.status_utils import best_firefox_cookie_profile
from tasks.runtime import setup_runtime
from services.welcome_utils import DEFAULT_WELCOME_MESSAGE, build_welcome_send_kwargs

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ===== MONETIZATION =====
# SKUs Discord. Remplis ces env apres avoir cree les SKU dans le Dev Portal :
#   SKU_NIVEAU_PREMIUM   = SKU "Durable" (achat unique 1.99 USD) /niveau Premium
#   SKU_PASS             = SKU "Subscription" (recurrent 3.99 USD / mois) Battle Pass
#   SKU_GUILD_BOOST_PLUS = SKU "Subscription" (recurrent 3.99 USD / mois) Guild Boost +
SKU_NIVEAU_PREMIUM    = os.getenv("SKU_NIVEAU_PREMIUM",    "").strip() or None
SKU_PASS              = os.getenv("SKU_PASS",              "").strip() or None
SKU_GUILD_BOOST_PLUS  = os.getenv("SKU_GUILD_BOOST_PLUS",  "").strip() or None  # Solo: 1 slot
SKU_GUILD_BOOST_DUO   = os.getenv("SKU_GUILD_BOOST_DUO",   "").strip() or None  # Duo: 2 slots
SKU_GUILD_BOOST_SQUAD = os.getenv("SKU_GUILD_BOOST_SQUAD", "").strip() or None  # Squad: 5 slots
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


# ===== FEATURE GUARD TREE =====
async def _feature_guard_check(interaction: discord.Interaction) -> bool:
    """Verification feature/boost/mod-perm avant chaque slash command.

    Assigne sur bot.tree.interaction_check apres creation du bot (plus fiable
    que tree_cls qui n'est pas toujours honore par commands.Bot)."""
    if True:
        try:
            _dbg_name = (interaction.data or {}).get("name", "?")
        except Exception:
            _dbg_name = "?"
        print(f"[GUARD] interaction_check FIRED cmd={_dbg_name}", flush=True)

        if not interaction.guild or not interaction.data:
            return True

        root_name = (interaction.data or {}).get("name", "")
        if not root_name:
            return True

        from services.feature_guard import COMMAND_FEATURE_MAP, get_feature_label
        from database import (guild_setting_get, custom_cmd_get, guild_has_active_boost,
                              mod_has_perm)

        # === Gating permissions modérateurs (PRIORITAIRE) ===
        # Tourne avant le gating feature : certaines commandes (warn/setup/xp/note/
        # modlogs) ne sont pas dans COMMAND_FEATURE_MAP et seraient sinon skippees.
        SLASH_MOD_PERM_MAP = {
            "warn":            "warn",
            "rolereaction":    "rolereaction",
            "ticket":          "ticket",
            "giveaway":        "giveaway",
            "clear":           "clear",
            "kick":            "kick",
            "poll":            "poll",
            "modlogs":         "modlogs",
            "setwelcome":      "setwelcome",
            "reaction_add":    "reaction",
            "reaction_remove": "reaction",
            "reaction_list":   "reaction",
            "socialalert":     "socialalert",
            "ban":             "ban",
            "setup":           "setup",
            "xp":              "xp",
            "note":            "note",
        }
        perm_key = SLASH_MOD_PERM_MAP.get(root_name)
        if perm_key is not None:
            uid = str(interaction.user.id)
            is_bypass = (
                (DISCORD_OWNER_ID and uid == str(DISCORD_OWNER_ID))
                or (interaction.guild.owner_id and uid == str(interaction.guild.owner_id))
            )
            if not is_bypass:
                configured = guild_setting_get(interaction.guild.id, "mod_access_configured", "0") == "1"
                _has = mod_has_perm(interaction.guild.id, uid, perm_key)
                print(f"[mod-perm DEBUG] guild={interaction.guild.id} cmd={root_name} "
                      f"perm={perm_key} uid={uid} bypass={is_bypass} "
                      f"configured={configured} mod_has_perm={_has}", flush=True)
                if not configured:
                    await interaction.response.send_message(
                        "⛔ Cette commande est désactivée pour les modérateurs tant que le "
                        "**propriétaire du serveur** n'a pas configuré les permissions.\n"
                        f"Demande à <@{interaction.guild.owner_id}> de faire `/setup` ou de "
                        "passer sur `dashboard.tookbot.click`.",
                        ephemeral=True,
                    )
                    return False
                if not _has:
                    await interaction.response.send_message(
                        f"⛔ Tu n'as pas la permission `{perm_key}` accordée par le propriétaire du serveur.\n"
                        f"Demande à <@{interaction.guild.owner_id}> de l'activer via le dashboard.",
                        ephemeral=True,
                    )
                    return False

        feature_key = COMMAND_FEATURE_MAP.get(root_name)
        if feature_key is None:
            # Fallback : si c'est une commande custom du serveur, gate sur la feature custom_commands
            try:
                if custom_cmd_get(interaction.guild.id, root_name):
                    feature_key = "custom_commands"
            except Exception:
                pass
        if feature_key is None:
            return True

        enabled = guild_setting_get(str(interaction.guild.id), feature_key, "1") == "1"
        if not enabled:
            label = get_feature_label(feature_key)
            await interaction.response.send_message(
                f"Cette fonctionnalité **{label}** est désactivée sur ce serveur par les administrateurs.",
                ephemeral=True,
            )
            return False

        # Gating Guild Boost + : certaines features ne sont accessibles que si la
        # guild a au moins un Guild Boost + actif (achete OU offert OU owner).
        GUILD_BOOST_FEATURES = {"custom_commands", "social_alerts", "tickets"}
        if feature_key in GUILD_BOOST_FEATURES:
            boosted = guild_has_active_boost(
                interaction.guild.id,
                sku_solo=SKU_GUILD_BOOST_PLUS,
                sku_duo=SKU_GUILD_BOOST_DUO,
                sku_squad=SKU_GUILD_BOOST_SQUAD,
                owner_id=DISCORD_OWNER_ID,
            )
            if not boosted:
                label = get_feature_label(feature_key)
                await interaction.response.send_message(
                    f"Cette fonctionnalité **{label}** nécessite **Guild Boost +** sur ce serveur.\n"
                    "Un membre doit acheter Guild Boost + dans la boutique du bot, puis l'assigner "
                    "à ce serveur depuis `dashboard.tookbot.click/premium`.",
                    ephemeral=True,
                )
                return False
        return True


# ===== BOT =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
# Assigne le guard directement sur le tree (fiable quelle que soit la version discord.py)
bot.tree.interaction_check = _feature_guard_check
print(f"[GUARD] interaction_check assigne sur bot.tree : {bot.tree.interaction_check}", flush=True)


# ===== LANCEMENT =====
db = DuelDB()
COMMAND_HOOKS = setup_commands(bot, USER_REACTIONS, globals())
MUSIC_RESUME = COMMAND_HOOKS.get("resume_music")
setup_duel_commands(bot, db)
setup_cs2_commands(bot)
setup_mod_commands(bot)
setup_giveaway_commands(bot)
setup_custom_cmd_commands(bot)
setup_lol_commands(bot)


@bot.event
async def on_voice_state_update(member, before, after):
    """Auto-cleanup des voice channels CS2 vides."""
    try:
        await cs2_on_voice(member, before, after, bot)
    except Exception as e:
        print(f"[cs2/voice-hook] {type(e).__name__}: {e}")


@tasks.loop(minutes=2)
async def cs2_queue_sweep_loop():
    await cs2_queue_sweep(bot)


@cs2_queue_sweep_loop.before_loop
async def _before_cs2_sweep():
    await bot.wait_until_ready()


@tasks.loop(seconds=60)
async def giveaway_finalize_loop():
    await _gw_sweep(bot)


@giveaway_finalize_loop.before_loop
async def _before_gw_finalize():
    await bot.wait_until_ready()


# NB : la loop est demarree dans tasks/runtime.py on_ready pour eviter le crash
# 'no current event loop' au chargement du module (avant bot.run).
setup_runtime(bot, globals())
bot.run(TOKEN)


