import os
# Clear the Node IPC env vars inherited from pm2 - otherwise Deno (used by yt-dlp
# to solve the YouTube JS challenges) crashes with "fd is not from BiPipe".
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
    # Tentatives de paths courants : Linux + macOS + Windows + auto-detect
    _here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        ctypes.util.find_library("opus"),
        # Windows : DLL bundle dans le repo (libopus.dll / libopus-0.dll /
        # opus.dll). Tente d'abord le repo local, puis le PATH systeme.
        os.path.join(_here, "libopus.dll"),
        os.path.join(_here, "libopus-0.dll"),
        os.path.join(_here, "opus.dll"),
        "libopus.dll",
        "libopus-0.dll",
        "opus.dll",
        # Linux / macOS
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
    print("[opus] FAILED to load - install libopus0 (apt) or libopus (brew)")
    return False

_load_opus()

def _ensure_opus():
    if discord.opus.is_loaded():
        return True
    return _load_opus()

from cards.rank import generate_levelup_card, generate_rank_card
from database import (init_db, get_xp, set_xp, get_leaderboard,
                      add_xp, get_level, get_progress, xp_for_level,
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
from commandes.tempvoice import (setup_tempvoice,
                                  tempvoice_on_voice_state_update as tempvoice_on_voice)
from commandes.moderation_pro import setup_mod_commands
from commandes.giveaway import (setup_giveaway_commands,
                                giveaway_finalize_sweep as _gw_sweep)
from commandes.custom_cmd import setup_custom_cmd_commands
from commandes.lol import setup_lol_commands
from cards.niveau import render_niveau_card, render_levelup_card_premium, preload_backgrounds
from services.emoji import parse_emoji_input as _parse_emoji_input
from tasks.runtime import setup_runtime
from services.welcome_utils import DEFAULT_WELCOME_MESSAGE, build_welcome_send_kwargs
from services.i18n import (DEFAULT_LOCALE as _I18N_DEFAULT,
                          guild_locale as _i18n_guild_locale, t, ti)


def _guild_locale(guild_id):
    """Locale configured for this server (fallback: English)."""
    return _i18n_guild_locale(guild_id) or _I18N_DEFAULT

_env_file = ".env.dev" if os.path.exists(".env.dev") else ".env"
load_dotenv(_env_file)
print(f"[env] loaded {_env_file}", flush=True)
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
    """Return the tier unlocked by this XP amount (capped at PASS_TIERS)."""
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

# ===== Runtime state exposed for /api/status =====
import time as _time
import json as _json
import math as _math
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
        _lat = _bot.latency if (_bot and _bot.is_ready()) else None
        BOT_STATE["latency_ms"]  = (round(_lat * 1000)
                                    if _lat is not None and _math.isfinite(_lat) else None)
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
        """Add combat XP. Returns (new_level, leveled_up)."""
        return add_combat_xp_db(user_id, amount)

    def attribuer_stat(self, user_id, stat):
        """Assign 1 point to a stat. Returns True on success."""
        return attribuer_stat_db(user_id, stat)


# ===== XP =====
# get_level + get_progress + add_xp + xp_for_level are provided by database.py
# (canonical level^5 formula). No local redefinition.


# ===== MUSIQUE (DB-backed) =====
import datetime as _dt

# PO Token provider (bgutil): bypasses the YouTube anti-bot without cookies.
# Endpoint configurable through the BGUTIL_POT_URL env var (default: local Docker container).
_BGUTIL_POT_URL = os.getenv("BGUTIL_POT_URL", "http://127.0.0.1:4416")

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    # Downloads the EJS solver from GitHub: required to solve the YouTube JS
    # challenges (sig + n). Deno alone is not enough.
    'remote_components': ['ejs:github'],
    'quiet': False,            # set to False to see the error details in the logs
    'no_warnings': False,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'youtube_include_dash_manifest': True,
    'prefer_free_formats': True,
    # Proxy SOCKS5 vers Cloudflare WARP local : YouTube voit une IP Cloudflare
    # (rarement bloquee) au lieu de l'IP datacenter du VPS. Reglable via env
    # YT_PROXY="" pour desactiver.
    'proxy': os.getenv("YT_PROXY", "socks5://127.0.0.1:40000") or None,
    # Clients YouTube : on restreint a ceux qui exploitent les PO tokens bgutil.
    # web_safari + tv_simply work well with WARP + PoToken without requiring a login.
    # On retire android/ios qui exigent un PoToken specifique qu'on n'a pas, et qui
    # produisent des URLs 403 quand ffmpeg les fetch.
    'extractor_args': {
        'youtube': {
            'player_client': ['web_safari', 'tv_simply', 'web', 'mweb'],
        },
        'youtubepot-bgutilhttp': {
            'base_url': [_BGUTIL_POT_URL],
        },
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
}
# Nettoie si proxy vide (cas YT_PROXY="" explicite)
if not YDL_OPTIONS.get('proxy'):
    YDL_OPTIONS.pop('proxy', None)

# Stack actuel : pas de cookies, pas de Firefox, juste bgutil HTTP + WARP.
# Suffit largement pour YouTube non-age-gated / non-restreint geographiquement.
print(f"[yt-dlp] bgutil pot provider endpoint: {_BGUTIL_POT_URL}")
BOT_STATE["music"] = {
    "bgutil_pot_url": _BGUTIL_POT_URL,
    "yt_proxy":       YDL_OPTIONS.get('proxy'),
}

# Proxy HTTP local (Privoxy) qui bridge vers SOCKS5 WARP.
# ffmpeg ne supporte que HTTP proxy, donc on passe par Privoxy pour rejoindre WARP.
# Configurable via env FFMPEG_HTTP_PROXY="" pour desactiver.
_FFMPEG_HTTP_PROXY = os.getenv("FFMPEG_HTTP_PROXY", "http://127.0.0.1:8118")

FFMPEG_OPTIONS = {
    # before_options : passe en CLI a ffmpeg AVANT -i (input)
    # - reconnect : auto-reconnect sur drop
    # - thread_queue_size : tampon paquets entrants (defaut 8 = trop petit)
    # - probesize/analyzeduration : reduit la latence de demarrage sans nuire a l'audio
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
        '-thread_queue_size 1024 '
        '-probesize 1M -analyzeduration 0'
        + (f' -http_proxy {_FFMPEG_HTTP_PROXY}' if _FFMPEG_HTTP_PROXY else '')
    ),
    # options : -vn = pas de video. -bufsize = tampon de sortie plus gros, evite sous-tampons.
    'options': '-vn -bufsize 1024k',
}

def _music_auth_hint():
    # Deliberately empty on the user-facing side: music error messages are
    # replaced by a friendly one in commandes/music.py (i18n key
    # games.music.trouble). Technical detail stays in the pm2 logs.
    return ""


def _format_audio_info(info):
    if 'entries' in info:
        info = info['entries'][0]
    return {
        "url":        info.get("url"),
        "title":      info.get("title") or "(sans titre)",
        "duration":   info.get("duration"),
        "thumbnail":  info.get("thumbnail"),
        "source_url": info.get("webpage_url") or info.get("original_url"),
    }


def _entry_url(entry):
    url = entry.get("webpage_url") or entry.get("url")
    if url and str(url).startswith("http"):
        return url
    video_id = entry.get("id") or entry.get("url")
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return None


def _is_youtube_target(s):
    """True when the query/URL targets YouTube (so the WARP proxy + bgutil POT are needed)."""
    if not s:
        return False
    s_low = s.lower()
    if s_low.startswith("ytsearch") or s_low.startswith("http") is False:
        # Keyword search -> default_search ytsearch -> YouTube
        return True
    return ("youtube.com/" in s_low or "youtu.be/" in s_low or
            "music.youtube.com" in s_low)


def _ydl_opts_for(url_or_query):
    """Retourne YDL_OPTIONS adapte a la cible : retire proxy/POT/clients YT
    pour SoundCloud / Bandcamp / autres (WARP SOCKS5 bloque chez certains hosts)."""
    if _is_youtube_target(url_or_query):
        return YDL_OPTIONS
    opts = dict(YDL_OPTIONS)
    opts.pop("proxy", None)
    opts.pop("extractor_args", None)
    opts.pop("remote_components", None)
    return opts


def _extract_audio_info_fast_sync(query):
    """Search YouTube via HTML scrape directe (PAS yt-dlp pour eviter
    bgutil-pot-provider plugin qui fait asyncio.run en parallel context).

    Parse le JSON ytInitialData de la page de resultats. Retourne juste
    {title, url, duration}. play_next re-resoudra l'URL en stream complet
    au moment de jouer la track (cf. _is_webpage_url check).

    Rapide (~200ms par appel), thread-safe, pas de plugin yt-dlp."""
    if query.startswith("http"):
        return {"title": query, "url": query, "source_url": query}
    import json
    import re as _re
    import urllib.parse
    import urllib.request
    q = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        raise RuntimeError(f"yt search fetch fail: {type(e).__name__}: {e}")
    m = _re.search(r"var ytInitialData = (\{.*?\});</script>", html, _re.DOTALL)
    if not m:
        raise RuntimeError("yt search: ytInitialData not found")
    try:
        data = json.loads(m.group(1))
    except Exception as e:
        raise RuntimeError(f"yt search json parse: {e}")
    # Look for the first videoRenderer in the contents
    def _walk_for_video(obj):
        if isinstance(obj, dict):
            if "videoRenderer" in obj:
                return obj["videoRenderer"]
            for v in obj.values():
                r = _walk_for_video(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for it in obj:
                r = _walk_for_video(it)
                if r:
                    return r
        return None
    vr = _walk_for_video(data)
    if not vr:
        raise RuntimeError("yt search: no videoRenderer found")
    vid = vr.get("videoId")
    title = ""
    title_runs = (vr.get("title") or {}).get("runs") or []
    if title_runs:
        title = title_runs[0].get("text") or ""
    duration_text = (vr.get("lengthText") or {}).get("simpleText") or ""
    duration = None
    if duration_text and ":" in duration_text:
        try:
            parts = [int(x) for x in duration_text.split(":")]
            if len(parts) == 2:
                duration = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                duration = parts[0] * 3600 + parts[1] * 60 + parts[2]
        except ValueError:
            pass
    thumb = None
    thumbs = (vr.get("thumbnail") or {}).get("thumbnails") or []
    if thumbs:
        thumb = thumbs[-1].get("url")
    if not vid:
        raise RuntimeError("yt search: no videoId")
    yt_url = f"https://www.youtube.com/watch?v={vid}"
    return {
        "title": title or query,
        "url": yt_url,
        "source_url": yt_url,
        "duration": duration,
        "thumbnail": thumb,
    }


async def get_audio_info_fast(query):
    return await asyncio.to_thread(_extract_audio_info_fast_sync, query)


def _extract_audio_info_sync(query):
    if query.startswith("http"):
        opts = _ydl_opts_for(query)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return _format_audio_info(ydl.extract_info(query, download=False))

    flat_options = dict(YDL_OPTIONS)
    flat_options["extract_flat"] = "in_playlist"
    with yt_dlp.YoutubeDL(flat_options) as ydl:
        search = ydl.extract_info(f"ytsearch5:{query}", download=False)
        candidates = [_entry_url(e) for e in (search.get("entries") or [])]
        candidates = [u for u in candidates if u]

    last_error = None
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        for candidate in candidates:
            try:
                info = _format_audio_info(ydl.extract_info(candidate, download=False))
                if info.get("url"):
                    return info
            except Exception as e:
                last_error = e
                print(f"[music] candidate failed: {candidate} -> {type(e).__name__}: {e}")

    if last_error:
        raise RuntimeError(f"No playable YouTube result for this search.{_music_auth_hint()} Last error: {last_error}")
    raise RuntimeError("No YouTube result found for this search.")


def _is_playlist_url(query):
    """True when the URL is a YouTube playlist (contains list=...)."""
    if not isinstance(query, str) or not query.startswith("http"):
        return False
    q = query.lower()
    return ("youtube.com/playlist" in q or
            ("list=" in q and "youtube.com" in q) or
            ("list=" in q and "youtu.be" in q))


def _extract_playlist_sync(url, max_items=50):
    """Extract the entry list of a YouTube playlist.

    Returns a list of dicts with minimal metadata (title, url, duration).
    extract_flat is used to avoid resolving everything up front: each entry
    is resolved when it is about to be played (play_next).
    """
    opts = dict(YDL_OPTIONS)
    opts["noplaylist"] = False
    opts["extract_flat"] = "in_playlist"
    opts["playlistend"] = max_items
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") or []
    out = []
    for e in entries:
        if not e:
            continue
        entry_url = _entry_url(e)
        if not entry_url:
            continue
        out.append({
            "title":      e.get("title") or "(sans titre)",
            "url":        entry_url,
            "source_url": entry_url,
            "duration":   e.get("duration"),
            "thumbnail":  None,  # resolu plus tard par play_next via extract_info
        })
    return {
        "playlist_title": info.get("title") or "Playlist YouTube",
        "entries":        out,
    }


async def get_playlist_info(url, max_items=50):
    """Retourne {playlist_title, entries:[{title,url,duration,...}, ...]}."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _extract_playlist_sync(url, max_items))


def _search_youtube_sync(query, max_results=5):
    """Cherche sur YouTube et retourne metadonnees minimales (sans resolve stream).

    Plus rapide que extract complet : on resout le stream seulement quand
    l'utilisateur a choisi (cf. /search dans commandes/music.py).
    """
    opts = dict(YDL_OPTIONS)
    opts["extract_flat"] = "in_playlist"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{int(max_results)}:{query}", download=False)
    out = []
    for e in info.get("entries") or []:
        if not e:
            continue
        url = _entry_url(e)
        if not url:
            continue
        out.append({
            "title":    e.get("title") or "(sans titre)",
            "url":      url,
            "duration": e.get("duration"),
            "uploader": e.get("uploader") or e.get("channel"),
        })
    return out


async def search_youtube(query, max_results=5):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _search_youtube_sync(query, max_results))


def _search_soundcloud_sync(query, max_results=5):
    """SoundCloud search through yt-dlp scsearch. The proxy/POT stack is not used (SC is not affected)."""
    opts = {
        'quiet': True, 'no_warnings': True,
        'extract_flat': 'in_playlist',
        'default_search': 'scsearch',
        'source_address': '0.0.0.0',
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch{int(max_results)}:{query}", download=False)
    out = []
    for e in info.get("entries") or []:
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url")
        if not url or not str(url).startswith("http"):
            continue
        out.append({
            "title":    e.get("title") or "(sans titre)",
            "url":      url,
            "duration": e.get("duration"),
            "uploader": e.get("uploader") or e.get("channel"),
        })
    return out


async def search_soundcloud(query, max_results=5):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _search_soundcloud_sync(query, max_results))


async def get_audio_info(query):
    """Return dict {url, title, duration, thumbnail, source_url} from yt-dlp."""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: _extract_audio_info_sync(query))
    except Exception as e:
        msg = str(e)
        if "Sign in to confirm" in msg or "not a bot" in msg:
            raise RuntimeError(f"YouTube is asking for an anti-bot verification.{_music_auth_hint()} yt-dlp error: {msg}") from e
        raise

# Consecutive failure counter per guild: avoids spamming when a WHOLE playlist
# fails (e.g. every link is an unresolvable YT page).
_PLAY_FAIL_COUNTER: dict = {}
_PLAY_FAIL_MAX = 5


def _is_webpage_url(url):
    """True when the URL is a web page (YouTube/SoundCloud/Bandcamp), not a direct audio stream."""
    if not url:
        return False
    u = url.lower()
    return ("youtube.com/watch" in u or "youtu.be/" in u or
            "music.youtube.com" in u or
            "soundcloud.com/" in u or
            "bandcamp.com/" in u)


async def play_next(voice_client, channel, guild_id):
    """Pop next track from DB queue and play. channel optional (for chat notif).

    Resolves YouTube web page URLs into an audio stream through yt-dlp before
    playing (typical case: tracks added from a playlist in extract_flat mode).
    Circuit breaker after N consecutive failures to avoid spamming the queue.
    """
    if not voice_client or not voice_client.is_connected():
        print(f"[music] play_next skipped: voice client not connected (guild={guild_id})")
        if channel:
            try:
                await channel.send(t("runtime.music.not_connected", _guild_locale(guild_id)))
            except Exception:
                pass
        return

    track = music_queue_pop_next(str(guild_id))
    if not track:
        music_state_clear_current(str(guild_id))
        _PLAY_FAIL_COUNTER.pop(str(guild_id), None)
        if channel:
            try: await channel.send(t("runtime.music.queue_finished", _guild_locale(guild_id)))
            except Exception: pass
        return

    # If the stored URL is a YouTube page (extract_flat playlist case), resolve
    # it again through yt-dlp to get a real audio stream.
    stream_url = track.get("url")
    thumbnail = track.get("thumbnail")
    duration = track.get("duration")
    if _is_webpage_url(stream_url):
        try:
            resolved = await get_audio_info(stream_url)
            stream_url = resolved.get("url") or stream_url
            if not thumbnail:
                thumbnail = resolved.get("thumbnail")
            if not duration:
                duration = resolved.get("duration")
        except Exception as e:
            print(f"[music] resolve fail for {track.get('title')!r}: {type(e).__name__}: {e}")
            _PLAY_FAIL_COUNTER[str(guild_id)] = _PLAY_FAIL_COUNTER.get(str(guild_id), 0) + 1
            if _PLAY_FAIL_COUNTER[str(guild_id)] >= _PLAY_FAIL_MAX:
                music_queue_clear(str(guild_id))
                music_state_clear_current(str(guild_id))
                _PLAY_FAIL_COUNTER.pop(str(guild_id), None)
                if channel:
                    try:
                        await channel.send(
                            t("runtime.music.too_many_errors", _guild_locale(guild_id))
                        )
                    except Exception:
                        pass
                return
            # Next track (silent, no notification)
            return await play_next(voice_client, channel, guild_id)

    try:
        # ffmpeg: goes through Privoxy (-> WARP) for YouTube only.
        # SoundCloud/Bandcamp serve directly, the WARP proxy blocks them.
        src_for_proxy = track.get("source_url") or track.get("url") or stream_url
        if _is_youtube_target(src_for_proxy) or "googlevideo.com" in (stream_url or "").lower():
            ff_opts = FFMPEG_OPTIONS
        else:
            ff_opts = dict(FFMPEG_OPTIONS)
            ff_opts["before_options"] = ff_opts.get("before_options", "").replace(
                f" -http_proxy {_FFMPEG_HTTP_PROXY}" if _FFMPEG_HTTP_PROXY else "", ""
            )
        raw_source = discord.FFmpegPCMAudio(stream_url, **ff_opts)
        # Volume guild : persiste via guild_setting (default 1.0 = 100%)
        try:
            from database import guild_setting_get
            vol_str = guild_setting_get(str(guild_id), "music_volume", "1.0")
            volume = max(0.0, min(2.0, float(vol_str)))
        except Exception:
            volume = 1.0
        source = discord.PCMVolumeTransformer(raw_source, volume=volume)
        voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(voice_client, channel, guild_id), bot.loop
            )
        )
        # Reset compteur fails sur succes
        _PLAY_FAIL_COUNTER.pop(str(guild_id), None)
        music_state_set(str(guild_id),
            current_title=track["title"],
            current_url=track.get("source_url") or track.get("url"),
            current_thumbnail=thumbnail,
            current_duration=duration,
            is_playing=1, is_paused=0,
            started_at=_dt.datetime.utcnow().isoformat(timespec="seconds"))
        # Telemetry : log lecture (best-effort, ne bloque pas la lecture si echec)
        try:
            from database import music_play_log
            src_url = (track.get("source_url") or track.get("url") or "").lower()
            if "soundcloud.com" in src_url:
                source = "soundcloud"
            elif "bandcamp.com" in src_url:
                source = "bandcamp"
            elif "twitch.tv" in src_url:
                source = "twitch"
            else:
                source = "youtube"
            music_play_log(
                guild_id=str(guild_id),
                user_id=track.get("requested_by"),
                track_title=track["title"],
                track_url=track.get("source_url") or track.get("url"),
                source=source,
                duration=duration,
            )
        except Exception as e:
            print(f"[music telemetry] log fail: {e}")
        if channel:
            try: await channel.send(t("runtime.music.now_playing", _guild_locale(guild_id),
                                      title=track['title']))
            except Exception: pass
    except Exception as e:
        print(f"[music] play_next error: {type(e).__name__}: {e}")
        _PLAY_FAIL_COUNTER[str(guild_id)] = _PLAY_FAIL_COUNTER.get(str(guild_id), 0) + 1
        if _PLAY_FAIL_COUNTER[str(guild_id)] >= _PLAY_FAIL_MAX:
            music_queue_clear(str(guild_id))
            music_state_clear_current(str(guild_id))
            _PLAY_FAIL_COUNTER.pop(str(guild_id), None)
            if channel:
                try:
                    await channel.send(
                        t("runtime.music.too_many_errors", _guild_locale(guild_id))
                    )
                except Exception:
                    pass


# ===== FEATURE GUARD TREE =====
async def _feature_guard_check(interaction: discord.Interaction) -> bool:
    """feature/boost/mod-perm check run before every slash command.

    Assigned on bot.tree.interaction_check after the bot is created (more reliable
    than tree_cls, which commands.Bot does not always honour)."""
    if True:
        # AUTOCOMPLETE interactions must never be gated here: they cannot be
        # answered with send_message -> it breaks them ("Failed to load options").
        # Gating happens on the real command instead (at submit time).
        if interaction.type == discord.InteractionType.autocomplete:
            return True

        if not interaction.guild or not interaction.data:
            return True

        root_name = (interaction.data or {}).get("name", "")
        if not root_name:
            return True

        # === LOCK: cards support channel = /cardsuggest and /cardmodify only ===
        SUGGEST_CHANNEL_ID = 1513592894265757716
        SUPPORT_GUILD_ID = int((os.getenv("SUPPORT_GUILD_ID") or "0").strip() or 0)
        if (SUPPORT_GUILD_ID and interaction.guild.id == SUPPORT_GUILD_ID
                and interaction.channel and interaction.channel.id == SUGGEST_CHANNEL_ID
                and root_name not in ("cardsuggest", "cardmodify")):
            try:
                await interaction.response.send_message(
                    ti(interaction, "runtime.guard.suggest_channel_only"),
                    ephemeral=True,
                )
            except Exception:
                pass
            return False

        from services.feature_guard import COMMAND_FEATURE_MAP, get_feature_label
        from database import (guild_setting_get, custom_cmd_get, guild_has_active_boost,
                              mod_has_perm)

        # === Moderator permission gating (TAKES PRECEDENCE) ===
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
                if not configured:
                    await interaction.response.send_message(
                        ti(interaction, "runtime.guard.mod_not_configured",
                           owner_id=interaction.guild.owner_id),
                        ephemeral=True,
                    )
                    return False
                if not _has:
                    await interaction.response.send_message(
                        ti(interaction, "runtime.guard.mod_missing_perm",
                           perm=perm_key, owner_id=interaction.guild.owner_id),
                        ephemeral=True,
                    )
                    return False

        feature_key = COMMAND_FEATURE_MAP.get(root_name)
        if feature_key is None:
            # Fallback: if it is a server custom command, gate on the custom_commands feature
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
                ti(interaction, "runtime.guard.feature_disabled", feature=label),
                ephemeral=True,
            )
            return False

        # No more Guild Boost gating: every feature is free, gating is now just
        # the per-feature on/off switch + the role permissions configured by the
        # admins.
        return True


# ===== BOT =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
# Assigne le guard directement sur le tree (fiable quelle que soit la version discord.py)
bot.tree.interaction_check = _feature_guard_check


# Note : error handler global app_commands defini dans tasks/runtime.py
# (single handler, exigence top.gg : messages clairs sur missing perms/roles)


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


# on_voice_state_update central : voir tasks/runtime.py (handler unique
# qui log les voice changes + appelle cs2 + tempvoice hooks). Pas de
# decorator ici sinon il overrideait le handler runtime au boot.


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


