from flask import Flask, render_template, request, redirect, session, jsonify, g, url_for, abort, send_file
import os
import time
import time as _time
import json
import secrets
import urllib.parse
from collections import deque
from datetime import timedelta
from dotenv import load_dotenv

# Lazy import requests — utilisé uniquement pour OAuth Discord
try:
    import requests as _requests
except ImportError:
    _requests = None

from database import (
    init_db, get_db,
    # XP per-guild
    get_xp, set_xp, get_leaderboard, get_all_users_for_guild, get_global_xp_stats,
    # Reactions per-guild
    get_all_reactions,
    # Guilds
    list_guilds, get_guild,
    # Music
    music_queue_list, music_queue_clear, music_state_get, music_queue_add,
    # Bot command queue
    bot_command_enqueue, bot_command_get,
    # Logs + channels
    get_logs, list_channels,
    # Stats (temporelles + heatmap + top)
    get_activity_by_day, get_xp_by_day, get_activity_heatmap,
    get_top_commands, get_top_active_users,
    # Settings (config dynamique)
    get_all_settings, get_setting, set_setting, DEFAULT_SETTINGS,
    guild_setting_get, guild_setting_set, guild_settings_all,
    promo_code_create, promo_code_get, promo_codes_list,
    promo_code_delete, promo_redeem_check, promo_redeem_apply,
    # Members (cache pour modération + picker)
    list_members,
    # Duels (global)
    admin_lister_duel_users, admin_get_full_duel_user, admin_update_duel_profil,
    admin_supprimer_sabre_collection,
    db_get_tous_sabres, db_get_sabre, db_create_sabre, db_update_sabre, db_delete_sabre,
    ajouter_sabre as db_ajouter_sabre_collection,
    get_combat_xp_progress,
    # Monetization
    user_has_active_entitlement, get_premium_settings, set_premium_setting,
    list_user_entitlements,
    user_is_premium as _db_user_is_premium,
    add_premium_grant, remove_premium_grant, list_premium_grants,
    has_premium_grant, user_has_active_pass, get_or_create_current_season,
    get_pass_progress, list_user_pass_unlocks,
    list_user_active_quests, auto_claim_pass_tiers,
    get_user_cosmetic, list_user_owned_cosmetics,
    list_roles,
    reaction_role_list, reaction_role_remove, reaction_role_remove_message,
    social_alert_create, social_alert_delete, social_alert_set_enabled,
    social_alert_reset, social_alerts_list,
    ticket_panels_list, ticket_panel_delete, ticket_panel_get,
    tickets_list, ticket_set_status,
)
from services import social
from cards.niveau import (
    list_available_backgrounds, render_niveau_card,
    has_owner_custom_bg, save_owner_custom_bg, remove_owner_custom_bg,
    CARD_W as NIVEAU_CARD_W, CARD_H as NIVEAU_CARD_H,
)
from duel.sabres import RARETES

# Init DB + seed
init_db()

load_dotenv()

app = Flask(__name__)
# Cle de session fixe en prod (sinon les sessions sautent a chaque restart).
# Met FLASK_SECRET dans le .env, sinon clef ephemere.
app.secret_key = os.getenv("FLASK_SECRET") or os.urandom(24)
PASSWORD = os.getenv("WEB_PASSWORD")  # Fallback si OAuth pas configure (dev)

# ===== OAuth Discord =====
DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_OWNER_ID      = os.getenv("DISCORD_OWNER_ID", "").strip()
# SKUs Discord pour la monetisation (renseigner apres creation dans le Dev Portal)
SKU_NIVEAU_PREMIUM    = os.getenv("SKU_NIVEAU_PREMIUM", "").strip() or None
SKU_PASS              = os.getenv("SKU_PASS", "").strip() or None
OAUTH_REDIRECT_URI    = os.getenv("OAUTH_REDIRECT_URI", "").strip()

OAUTH_ENABLED = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and OAUTH_REDIRECT_URI and _requests)

DISCORD_API     = "https://discord.com/api/v10"
DISCORD_AUTH    = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN   = "https://discord.com/api/oauth2/token"

# Discord permission bits
PERM_ADMINISTRATOR = 0x8
PERM_MANAGE_GUILD  = 0x20
PERM_BAN_MEMBERS   = 0x4
PERM_KICK_MEMBERS  = 0x2

# Pages accessibles aux mods (non-owner) — owner voit tout
MOD_ALLOWED_PAGES = {
    "dashboard", "search", "user_profile",
    "reactions_panel", "reactions_panel_post",
    "music_page", "logs_page", "moderation_page",
    "reactionroles_page", "social_alerts_page", "tickets_page",
    "cs2_page", "giveaways_page", "custom_commands_page",
    "poll_builder_page",
}
MOD_ALLOWED_API_PREFIXES = (
    "/api/search", "/api/user/",
    "/api/reactions/", "/api/music/", "/api/logs",
    "/api/members", "/api/moderation/", "/api/channels",
    "/api/select-guild", "/api/guilds",
    "/api/rolereactions",
    "/api/social-alerts",
    "/api/tickets",
    "/api/cs2",
    "/api/roles",
    "/api/giveaways",
    "/api/custom-commands",
    "/api/heatmap",
    "/api/poll/",
    "/api/guild-settings",
)
MOD_BLOCKED_PAGES = {
    # Pages global ou owner-only
    "/general", "/duels", "/dms", "/status", "/settings", "/bottalk",
}
MOD_BLOCKED_API_PREFIXES = (
    "/api/duels", "/api/sabres", "/api/dms", "/api/status",
    "/api/settings", "/api/bottalk",
)

if not OAUTH_ENABLED:
    print("[WARN] OAuth Discord non configure (DISCORD_CLIENT_ID/SECRET/REDIRECT_URI manquants ou requests absent).")
    print("       Login en fallback : password (WEB_PASSWORD).")
if not PASSWORD and not OAUTH_ENABLED:
    print("[WARN] WEB_PASSWORD ni OAuth definis — login impossible.")

# ===== Sécurité dashboard =====
SESSION_LIFETIME_HOURS = int(os.getenv("SESSION_LIFETIME_HOURS", "24"))
app.permanent_session_lifetime = timedelta(hours=SESSION_LIFETIME_HOURS)
app.config["SESSION_COOKIE_HTTPONLY"]   = True
app.config["SESSION_COOKIE_SAMESITE"]   = "Lax"
# Cookie secure activé seulement si HTTPS (proxy nginx). Active via HTTPS_ENABLED=1 dans .env
if os.getenv("HTTPS_ENABLED", "0") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PREFERRED_URL_SCHEME"]  = "https"

# Login attempts (anti brute-force, en mémoire)
_LOGIN_FAIL = {}                 # ip -> [timestamps...]
_LOGIN_LOG  = deque(maxlen=200)  # historique succès/échec pour /status

LOGIN_RATE_WINDOW = 300          # 5 min
LOGIN_RATE_MAX    = 5            # 5 tentatives ratées en 5 min -> bannit 15 min
LOGIN_BAN_SECONDS = 900

def _client_ip():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")

def _check_login_rate(ip):
    now = time.time()
    fails = _LOGIN_FAIL.get(ip, [])
    fails = [t for t in fails if t > now - LOGIN_BAN_SECONDS]
    _LOGIN_FAIL[ip] = fails
    if len(fails) >= LOGIN_RATE_MAX:
        oldest = min(fails)
        retry_in = int(LOGIN_BAN_SECONDS - (now - oldest))
        return False, max(retry_in, 1)
    return True, 0

def _record_login(ip, success, username=None):
    if not success:
        _LOGIN_FAIL.setdefault(ip, []).append(time.time())
    _LOGIN_LOG.appendleft({
        "ts": time.time(),
        "ip": ip,
        "ok": bool(success),
        "user": username,
    })

# ===== Discord OAuth helpers =====
def _is_owner_session():
    """L'user logge est-il le proprio (super-admin) ?"""
    d = session.get("discord") or {}
    return bool(d.get("is_owner"))

def _accessible_guild_ids():
    """IDs des serveurs accessibles par l'user logge.
    Owner -> toutes les guilds actives du bot.
    Mod -> intersection (guilds du bot ∩ guilds ou il a manage_guild|admin)."""
    d = session.get("discord") or {}
    if d.get("is_owner"):
        return [g["guild_id"] for g in list_guilds(active_only=True)]
    return list(d.get("accessible_guild_ids") or [])

def _user_can_access_page(endpoint, path):
    """Vérifie que l'user peut accéder à cette page/route."""
    if _is_owner_session():
        return True

    # Pages essentielles toujours accessibles (auth + selection guild + statique)
    if path in ("/select-guild", "/logout", "/oauth/logout"):
        return True
    if path.startswith("/oauth/") or path.startswith("/static"):
        return True
    if path in ("/api/select-guild", "/api/guilds"):
        return True

    # Pages "Mon compte" (premium, gestion d'achats) : tout user connecte y accede
    if path == "/premium" or path.startswith("/premium/") or path.startswith("/api/premium"):
        return True
    if path == "/my-pass" or path.startswith("/my-pass/") or path.startswith("/api/my/"):
        return True

    # Pages user-perso non scopees a un serveur
    if path == "/logout" or path == "/forbidden":
        return True

    # Mods : checks par endpoint et par path
    if path in MOD_BLOCKED_PAGES:
        return False
    for pref in MOD_BLOCKED_API_PREFIXES:
        if path.startswith(pref):
            return False
    # Si endpoint dans la liste autorisee, OK
    if endpoint in MOD_ALLOWED_PAGES:
        return True
    # API allowed if matches prefix
    for pref in MOD_ALLOWED_API_PREFIXES:
        if path.startswith(pref):
            return True
    # Path-based fallback (allows /dashboard, /search, /user/<id>, /reactions, /music, /logs, /moderation)
    allowed_path_prefixes = ("/dashboard", "/search", "/user/", "/reactions", "/music", "/logs", "/moderation")
    return any(path == p.rstrip("/") or path.startswith(p) for p in allowed_path_prefixes)

def _filter_guilds_by_session(guilds):
    """Filtre une liste de guilds (du bot) selon l'access user."""
    if _is_owner_session():
        return guilds
    allowed = set(_accessible_guild_ids())
    return [g for g in guilds if g.get("guild_id") in allowed]


PUBLIC_PATHS = {"/", "/static"}        # tout le reste exige login
GUILD_FREE_PATHS = {                   # routes qui n'exigent pas de guild sélectionné
    "/", "/select-guild", "/general", "/logout",
    "/dms", "/status", "/settings",
    "/oauth/login", "/oauth/callback", "/oauth/logout",
    "/api/guilds", "/api/select-guild",
    "/api/dms", "/api/status", "/api/settings",
    "/premium", "/api/premium",
    "/owner", "/api/owner",
    "/search-global", "/api/search-global",
    "/my-pass", "/api/my",
}

def needs_guild(path):
    if path.startswith("/static"):
        return False
    for p in GUILD_FREE_PATHS:
        if path == p or path.startswith(p + "/"):
            return False
    if path.startswith("/api/duels") or path.startswith("/api/sabres"):
        return False  # duels/sabres = global
    if path.startswith("/api/dms") or path.startswith("/api/status") or path.startswith("/api/settings"):
        return False  # DMs / status / settings = global
    return True

PUBLIC_NO_AUTH_PATHS = {"/", "/privacy", "/terms", "/api/public-stats"}


def _current_user_id():
    """Snowflake Discord de l'utilisateur connecte (str), ou None."""
    if not session.get("logged_in"):
        return None
    return (session.get("discord") or {}).get("user_id")


def _is_admin_of_current_guild() -> bool:
    """True si owner OU admin du serveur actuellement selectionne."""
    if _is_owner_session():
        return True
    cg = session.get("guild_id")
    if not cg:
        return False
    metas = (session.get("discord") or {}).get("guilds_meta") or []
    for m in metas:
        if str(m.get("guild_id")) == str(cg) and m.get("is_admin"):
            return True
    return False


def _has_pass(uid) -> bool:
    """Pass actif : owner OU grant manuel feature='pass' OU entitlement subscription."""
    if not uid:
        return False
    if DISCORD_OWNER_ID and str(uid) == str(DISCORD_OWNER_ID):
        return True
    return user_has_active_pass(uid, sku_pass_id=SKU_PASS)


def _is_premium(uid, feature="all") -> bool:
    """Wrapper unifie : entitlement Discord OU grant manuel OU owner ENV."""
    if _db_user_is_premium(uid, feature=feature, owner_id=DISCORD_OWNER_ID):
        return True
    if feature == "all" and _has_pass(uid):
        return True
    return False


def _require_premium_user():
    """Retourne user_id si user connecte ET premium actif, sinon None."""
    uid = _current_user_id()
    if not uid:
        return None
    if not _is_premium(uid):
        return None
    return uid

@app.before_request
def _ctx():
    g.logged_in = bool(session.get("logged_in"))
    g.discord_user = session.get("discord") or {}
    g.is_owner = _is_owner_session()
    all_guilds = list_guilds(active_only=True) if g.logged_in else []
    g.guilds = _filter_guilds_by_session(all_guilds)
    g.guild_id = session.get("guild_id")
    g.guild = get_guild(g.guild_id) if g.guild_id else None
    if g.guild_id and not g.guild:
        # Bot a quitté ce serveur — invalider la sélection
        session.pop("guild_id", None)
        g.guild_id = None
    # Guild non-accessible pour cet user ? Reset.
    if g.guild_id and not g.is_owner:
        if g.guild_id not in [gd["guild_id"] for gd in g.guilds]:
            session.pop("guild_id", None)
            g.guild_id = None
            g.guild = None

    path = request.path
    # Auth gate
    if not g.logged_in \
            and path not in PUBLIC_NO_AUTH_PATHS \
            and not path.startswith("/static") \
            and not path.startswith("/oauth/"):
        if path.startswith("/api/"):
            return jsonify({"error": "Non authentifié"}), 401
        return redirect("/")
    # Page-level access check (owner vs mod)
    if g.logged_in and g.discord_user and not g.is_owner:
        if not _user_can_access_page(request.endpoint or "", path):
            if path.startswith("/api/"):
                return jsonify({"error": "Accès refusé (mod)"}), 403
            return render_template("forbidden.html"), 403
    # Guild gate
    if g.logged_in and not g.guild_id and needs_guild(path):
        if path.startswith("/api/"):
            return jsonify({"error": "Aucun serveur sélectionné", "redirect": "/select-guild"}), 412
        return redirect("/select-guild")


@app.context_processor
def _inject_ctx():
    is_owner = getattr(g, "is_owner", False)
    guilds   = getattr(g, "guilds", []) or []
    # Mod = a au moins une guild commune avec perms moderation/admin (mais pas owner).
    has_mod_access = bool(guilds) and not is_owner
    return {
        "current_guild":  getattr(g, "guild", None),
        "current_guilds": guilds,
        "is_owner":       is_owner,
        "has_mod_access": has_mod_access,
        # True pour owner OU mod ; sert a afficher la section "Ce serveur".
        "has_server_access": is_owner or has_mod_access,
        # True si l'user peut editer les data d'un membre du serveur courant
        # (owner partout, admin Discord pour le serveur selectionne).
        "is_guild_admin": _is_admin_of_current_guild(),
        "discord_user":   getattr(g, "discord_user", {}),
        "oauth_enabled":  OAUTH_ENABLED,
    }


def gid():
    return session.get("guild_id")

def get_global_stats():
    """Stats du serveur sélectionné (pas global cross-guild)."""
    db = get_db()
    g_id = gid()
    total_users = db.execute("SELECT COUNT(*) AS n FROM users WHERE guild_id = ?", (g_id,)).fetchone()["n"]
    total_xp   = db.execute("SELECT COALESCE(SUM(xp), 0) AS s FROM users WHERE guild_id = ?", (g_id,)).fetchone()["s"]
    avg_level  = db.execute("SELECT COALESCE(AVG(level), 0) AS a FROM users WHERE guild_id = ?", (g_id,)).fetchone()["a"]
    top_user   = db.execute("SELECT username, xp, level FROM users WHERE guild_id = ? ORDER BY xp DESC LIMIT 1", (g_id,)).fetchone()
    db.close()
    return {
        "total_users": total_users,
        "total_xp":    total_xp or 0,
        "avg_level":   round(avg_level or 0, 2),
        "top_user":    dict(top_user) if top_user else None,
    }


from web_app.routes.auth import register_auth_routes
from web_app.routes.dashboard import register_dashboard_routes
from web_app.routes.reactions import register_reaction_routes
from web_app.routes.duels import register_duel_routes
from web_app.routes.music import register_music_routes
from web_app.routes.admin import register_admin_routes
from web_app.routes.premium import register_premium_routes
from web_app.routes.public_stats import register_public_stats_routes
from web_app.routes.server_tools import register_server_tool_routes
from web_app.routes.pass_routes import register_pass_routes

for _register_routes in (
    register_auth_routes, register_dashboard_routes, register_reaction_routes,
    register_duel_routes, register_music_routes, register_admin_routes,
    register_premium_routes, register_public_stats_routes, register_server_tool_routes,
    register_pass_routes,
):
    _register_routes(app, globals())

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug, use_reloader=False)

