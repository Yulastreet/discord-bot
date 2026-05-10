from flask import Flask, render_template, request, redirect, session, jsonify, g, url_for, abort, send_file
import os
import time
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
    # Members (cache pour modération + picker)
    list_members,
    # DMs (global)
    list_dm_conversations, get_dm_conversation, mark_dm_read, count_unread_dms,
    delete_dm_conversation,
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
    social_alerts_list,
    ticket_panels_list, ticket_panel_delete, ticket_panel_get,
    tickets_list, ticket_set_status,
)
import social_integrations as social
from niveau_card import (
    list_available_backgrounds, render_niveau_card,
    has_owner_custom_bg, save_owner_custom_bg, remove_owner_custom_bg,
    CARD_W as NIVEAU_CARD_W, CARD_H as NIVEAU_CARD_H,
)
from duel_sabres import RARETES

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
}
MOD_ALLOWED_API_PREFIXES = (
    "/api/search", "/api/user/",
    "/api/reactions/", "/api/music/", "/api/logs",
    "/api/members", "/api/moderation/", "/api/channels",
    "/api/select-guild", "/api/guilds",
    "/api/rolereactions",
    "/api/social-alerts",
    "/api/tickets",
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


# =====================================================================
# AUTH + GUILD CONTEXT MIDDLEWARE
# =====================================================================

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


# =====================================================================
# HELPERS
# =====================================================================

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


# =====================================================================
# AUTH
# =====================================================================

@app.route("/", methods=["GET", "POST"])
def login():
    ip = _client_ip()
    password_fallback = bool(PASSWORD) and not OAUTH_ENABLED
    if request.method == "POST":
        # Le password n'est accepte qu'en fallback (OAuth desactive)
        if not password_fallback:
            return render_template("login.html",
                                   oauth_enabled=OAUTH_ENABLED,
                                   password_fallback=False,
                                   error="Login password désactivé. Utilise Discord."), 400
        ok, retry_in = _check_login_rate(ip)
        if not ok:
            return render_template("login.html",
                oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback,
                error=f"Trop de tentatives. Réessaie dans {retry_in // 60}m {retry_in % 60}s.",
            ), 429
        submitted = request.form.get("password") or ""
        if PASSWORD and secrets.compare_digest(submitted, PASSWORD):
            session.permanent       = True
            session["logged_in"]    = True
            session["login_ts"]     = time.time()
            session["login_ip"]     = ip
            # Mode password : tu es owner par defaut (single-user fallback)
            session["discord"] = {
                "user_id":              "password-user",
                "username":             "Admin (password)",
                "avatar":               None,
                "is_owner":             True,
                "accessible_guild_ids": [],
                "guilds_meta":          [],
            }
            _record_login(ip, True, username="password")
            return redirect("/select-guild")
        _record_login(ip, False)
        return render_template("login.html",
            oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback,
            error="Mot de passe incorrect")
    if session.get("logged_in"):
        return redirect("/dashboard" if session.get("guild_id") else "/select-guild")
    return render_template("login.html", oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# =====================================================================
# PUBLIC LEGAL PAGES (sans auth, accessible a tous)
# =====================================================================

@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html")

@app.route("/terms")
def terms_page():
    return render_template("terms.html")


# =====================================================================
# OAuth DISCORD
# =====================================================================

@app.route("/oauth/login")
def oauth_login():
    if not OAUTH_ENABLED:
        return "OAuth Discord non configuré.", 500
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    params = {
        "client_id":     DISCORD_CLIENT_ID,
        "redirect_uri":  OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope":         "identify guilds",
        "state":         state,
        "prompt":        "consent",
    }
    return redirect(DISCORD_AUTH + "?" + urllib.parse.urlencode(params))


@app.route("/oauth/callback")
def oauth_callback():
    if not OAUTH_ENABLED:
        return "OAuth non configuré.", 500
    err = request.args.get("error")
    if err:
        return render_template("login.html", error=f"Discord OAuth refusé : {err}"), 400
    code  = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or state != session.get("oauth_state"):
        return render_template("login.html", error="État OAuth invalide. Réessaie."), 400

    # 1. Échange code contre access_token
    try:
        r = _requests.post(DISCORD_TOKEN, data={
            "client_id":     DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  OAUTH_REDIRECT_URI,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        r.raise_for_status()
        token_data = r.json()
    except Exception as e:
        return render_template("login.html", error=f"Échange code -> token échoué : {e}"), 500

    access_token = token_data.get("access_token")
    if not access_token:
        return render_template("login.html", error="Pas d'access token reçu."), 500

    # 2. Fetch user
    try:
        u = _requests.get(f"{DISCORD_API}/users/@me",
                          headers={"Authorization": f"Bearer {access_token}"},
                          timeout=10).json()
        guilds_user = _requests.get(f"{DISCORD_API}/users/@me/guilds",
                                    headers={"Authorization": f"Bearer {access_token}"},
                                    timeout=10).json()
    except Exception as e:
        return render_template("login.html", error=f"Fetch user/guilds échoué : {e}"), 500

    if not isinstance(u, dict) or not u.get("id"):
        return render_template("login.html", error="Réponse Discord invalide."), 500

    user_id  = str(u["id"])
    is_owner = bool(DISCORD_OWNER_ID) and user_id == DISCORD_OWNER_ID

    # 3. Filtrer les guilds : intersection avec celles ou le bot est present + check perms admin/manage_guild
    bot_guild_ids = {gd["guild_id"] for gd in list_guilds(active_only=True)}
    accessible = []
    for gd in (guilds_user or []):
        gid = str(gd.get("id") or "")
        if gid not in bot_guild_ids:
            continue
        try:
            perms = int(gd.get("permissions", 0) or 0)
        except (TypeError, ValueError):
            perms = 0
        is_admin = bool(perms & PERM_ADMINISTRATOR)
        is_mgr   = bool(perms & PERM_MANAGE_GUILD)
        is_kick  = bool(perms & (PERM_KICK_MEMBERS | PERM_BAN_MEMBERS))
        if gd.get("owner") or is_admin or is_mgr or is_kick:
            accessible.append({
                "guild_id":   gid,
                "name":       gd.get("name"),
                "perms":      perms,
                "is_admin":   is_admin or bool(gd.get("owner")),
                "is_manager": is_mgr,
                "is_mod":     is_kick,
            })

    # On autorise tous les utilisateurs Discord a se connecter (acces page /premium,
    # gestion de leurs achats, etc.). Sans guild commune mod/admin, ils ne verront
    # juste pas le dashboard de moderation — geres par le middleware d'acces.
    avatar_url = None
    if u.get("avatar"):
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{u['avatar']}.png?size=128"
    else:
        # default avatar
        idx = (int(user_id) >> 22) % 6 if user_id.isdigit() else 0
        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{idx}.png"

    session.permanent = True
    session["logged_in"]  = True
    session["login_ts"]   = time.time()
    session["login_ip"]   = _client_ip()
    session["discord"]    = {
        "user_id":              user_id,
        "username":             u.get("global_name") or u.get("username") or "user",
        "avatar":               avatar_url,
        "is_owner":             is_owner,
        "accessible_guild_ids": [g["guild_id"] for g in accessible],
        "guilds_meta":          accessible,
    }
    session.pop("oauth_state", None)
    _record_login(_client_ip(), True, username=session["discord"]["username"])
    # Owner / mod -> selection de guild ; user "regular" -> page premium directement.
    if is_owner or accessible:
        return redirect("/select-guild")
    return redirect("/premium")


@app.route("/oauth/logout")
def oauth_logout():
    session.clear()
    return redirect("/")


# =====================================================================
# GUILD PICKER
# =====================================================================

@app.route("/select-guild", methods=["GET", "POST"])
def select_guild():
    if request.method == "POST":
        g_id = request.form.get("guild_id") or (request.json or {}).get("guild_id")
        if g_id and any(gd["guild_id"] == g_id for gd in g.guilds):
            session["guild_id"] = g_id
            return redirect("/dashboard")
    return render_template("select_guild.html", guilds=g.guilds)

@app.route("/api/select-guild", methods=["POST"])
def api_select_guild():
    data = request.json or {}
    g_id = data.get("guild_id")
    if not g_id:
        return jsonify({"error": "guild_id requis"}), 400
    if not any(gd["guild_id"] == g_id for gd in g.guilds):
        return jsonify({"error": "Serveur inconnu ou non autorisé"}), 403
    session["guild_id"] = g_id
    return jsonify({"success": True})

@app.route("/api/guilds")
def api_guilds():
    return jsonify({"guilds": g.guilds})


# =====================================================================
# DASHBOARD GENERAL (cross-guild)
# =====================================================================

@app.route("/general")
def general_page():
    stats = get_global_xp_stats()
    guilds = list_guilds(active_only=True)
    db = get_db()
    # Stats par serveur
    by_guild = []
    for gd in guilds:
        row = db.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(xp), 0) AS xp, COALESCE(AVG(level), 0) AS lvl
                            FROM users WHERE guild_id = ?""", (gd["guild_id"],)).fetchone()
        rx  = db.execute("SELECT COUNT(*) AS n FROM reactions WHERE guild_id = ?", (gd["guild_id"],)).fetchone()
        by_guild.append({
            **gd,
            "users": row["n"], "xp": row["xp"], "avg_level": round(row["lvl"] or 0, 2),
            "reactions": rx["n"],
        })
    db.close()
    by_guild.sort(key=lambda r: r["xp"], reverse=True)
    # Activité 14 jours cross-server
    activity = get_activity_by_day(guild_id=None, days=14)
    heatmap  = get_activity_heatmap(guild_id=None, weeks=4)
    top_cmds = get_top_commands(guild_id=None, days=30, limit=10)
    return render_template("general.html",
                           stats=stats, by_guild=by_guild,
                           activity=activity, heatmap=heatmap, top_cmds=top_cmds)


# =====================================================================
# DASHBOARD (per-guild)
# =====================================================================

@app.route("/dashboard")
def dashboard():
    g_id = gid()
    db = get_db()
    users = [dict(r) for r in db.execute(
        "SELECT * FROM users WHERE guild_id = ? ORDER BY xp DESC", (g_id,)).fetchall()]
    db.close()
    stats = get_global_stats()
    top10 = users[:10]
    activity   = get_activity_by_day(guild_id=g_id, days=14)
    heatmap    = get_activity_heatmap(guild_id=g_id, weeks=4)
    top_cmds   = get_top_commands(guild_id=g_id, days=30, limit=8)
    top_active = get_top_active_users(guild_id=g_id, days=30, limit=10)
    return render_template("dashboard.html",
                           users=users, stats=stats, top10=top10,
                           activity=activity, heatmap=heatmap,
                           top_cmds=top_cmds, top_active=top_active)


# =====================================================================
# SEARCH (per-guild)
# =====================================================================

@app.route("/search")
def search_page():
    return render_template("search.html")

@app.route("/api/search")
def api_search():
    g_id = gid()
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify({"users": []})
    db = get_db()
    results = db.execute(
        """SELECT user_id, username, level, xp FROM users
           WHERE guild_id = ? AND (LOWER(username) LIKE ? OR user_id LIKE ?) ORDER BY xp DESC""",
        (g_id, f"%{query}%", f"%{query}%")).fetchall()
    db.close()
    return jsonify({"users": [dict(u) for u in results]})


@app.route("/search-global")
def search_global_page():
    if not _is_owner_session():
        abort(403)
    return render_template("search_global.html", active_nav="search_global")


@app.route("/api/search-global")
def api_search_global():
    """Recherche cross-serveur (owner uniquement). Agrege par user_id, somme XP."""
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify({"users": []})
    db = get_db()
    rows = db.execute(
        """SELECT u.user_id,
                  MAX(u.username)                   AS username,
                  COUNT(DISTINCT u.guild_id)        AS guild_count,
                  SUM(u.xp)                         AS xp_total,
                  MAX(u.level)                      AS level_max
             FROM users u
             WHERE LOWER(u.username) LIKE ? OR u.user_id LIKE ?
             GROUP BY u.user_id
             ORDER BY xp_total DESC
             LIMIT 100""",
        (f"%{query}%", f"%{query}%"),
    ).fetchall()
    out = []
    for r in rows:
        guilds_rows = db.execute(
            """SELECT u.guild_id, g.name AS guild_name, u.xp, u.level
                 FROM users u LEFT JOIN guilds g ON g.guild_id = u.guild_id
                 WHERE u.user_id = ? ORDER BY u.xp DESC""",
            (r["user_id"],),
        ).fetchall()
        out.append({
            "user_id":     r["user_id"],
            "username":    r["username"],
            "xp_total":    r["xp_total"] or 0,
            "level_max":   r["level_max"] or 0,
            "guild_count": r["guild_count"] or 0,
            "guilds":      [dict(g) for g in guilds_rows],
        })
    db.close()
    return jsonify({"users": out})

@app.route("/user/<user_id>")
def user_profile(user_id):
    return render_template("user.html")

@app.route("/api/user/<user_id>")
def api_user(user_id):
    g_id = gid()
    db = get_db()
    user = db.execute(
        "SELECT user_id, username, level, xp FROM users WHERE guild_id = ? AND user_id = ?",
        (g_id, str(user_id))).fetchone()
    if not user:
        db.close()
        return jsonify({"error": "Utilisateur non trouvé sur ce serveur"}), 404

    # Activité 14 derniers jours
    rows = db.execute("""SELECT DATE(ts) AS day, COUNT(*) AS n
                         FROM logs WHERE guild_id = ? AND user_id = ?
                           AND ts >= datetime('now', '-14 days')
                         GROUP BY day""", (g_id, str(user_id))).fetchall()
    by_day = {r["day"]: r["n"] for r in rows}
    import datetime as _dt
    today = _dt.date.today()
    activity = []
    for i in range(13, -1, -1):
        d = today - _dt.timedelta(days=i)
        ds = d.isoformat()
        activity.append({"date": ds, "count": by_day.get(ds, 0)})

    # Channels favoris (par count messages edit/delete + voice join + commandes)
    chans = db.execute("""SELECT channel_id, MAX(channel_name) AS name, COUNT(*) AS n
                          FROM logs WHERE guild_id = ? AND user_id = ? AND channel_id IS NOT NULL
                            AND ts >= datetime('now', '-30 days')
                          GROUP BY channel_id ORDER BY n DESC LIMIT 5""",
                       (g_id, str(user_id))).fetchall()
    fav_channels = [dict(r) for r in chans]

    # Compte par type d'event
    by_type = db.execute("""SELECT type, COUNT(*) AS n FROM logs
                            WHERE guild_id = ? AND user_id = ? AND ts >= datetime('now', '-30 days')
                            GROUP BY type ORDER BY n DESC""",
                         (g_id, str(user_id))).fetchall()
    types = [dict(r) for r in by_type]

    # Profil duel global s'il existe
    duel = db.execute("SELECT * FROM duel_profil WHERE user_id = ?", (str(user_id),)).fetchone()
    duel_data = dict(duel) if duel else None

    db.close()
    return jsonify({
        "user":         dict(user),
        "activity":     activity,
        "fav_channels": fav_channels,
        "type_counts":  types,
        "duel":         duel_data,
    })


# =====================================================================
# REACTIONS (per-guild)
# =====================================================================

@app.route("/reactions")
def reactions_panel():
    g_id = gid()
    conn = get_db()
    users_rows = conn.execute(
        "SELECT DISTINCT user_id, username FROM users WHERE guild_id = ? ORDER BY username COLLATE NOCASE",
        (g_id,)).fetchall()
    reactions_rows = conn.execute(
        "SELECT user_id, emoji FROM reactions WHERE guild_id = ?", (g_id,)).fetchall()
    reactions = {str(row["user_id"]): row["emoji"] for row in reactions_rows}
    conn.close()
    users = [(r["user_id"], r["username"]) for r in users_rows]
    return render_template("reactions.html", users=users, reactions=reactions)

@app.route("/api/reactions/add", methods=["POST"])
def add_reaction_route():
    g_id = gid()
    data = request.json or {}
    user_id = str(data.get("user_id") or "")
    emoji = data.get("emoji")
    if not user_id or not emoji:
        return jsonify({"error": "user_id et emoji requis"}), 400
    conn = get_db()
    conn.execute("""INSERT INTO reactions (guild_id, user_id, emoji) VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET emoji = excluded.emoji""",
                 (g_id, user_id, emoji))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/reactions/remove", methods=["POST"])
def remove_reaction_route():
    g_id = gid()
    data = request.json or {}
    user_id = str(data.get("user_id") or "")
    if not user_id:
        return jsonify({"error": "user_id requis"}), 400
    conn = get_db()
    conn.execute("DELETE FROM reactions WHERE guild_id = ? AND user_id = ?", (g_id, user_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# =====================================================================
# DUELS (GLOBAL)
# =====================================================================

@app.route("/duels")
def duels_page():
    return render_template("duels.html", raretes=RARETES)

@app.route("/api/duels/users")
def api_duels_users():
    q = request.args.get("q", "").strip().lower()
    rows = admin_lister_duel_users()
    if q:
        rows = [r for r in rows if q in (r.get("username") or "").lower() or q in str(r.get("user_id"))]
    return jsonify({"users": rows})

@app.route("/api/duels/user/<user_id>")
def api_duels_user(user_id):
    data = admin_get_full_duel_user(user_id)
    if not data:
        return jsonify({"error": "Profil duel introuvable"}), 404
    profil = data["profil"]
    total_xp = profil.get("combat_xp", 0) or 0
    level, xp_in_level, xp_needed = get_combat_xp_progress(total_xp)
    profil["combat_xp_in_level"] = xp_in_level
    profil["combat_xp_needed"]   = xp_needed
    v = profil.get("victoires", 0) or 0
    d = profil.get("defaites", 0) or 0
    profil["ratio"] = round(v / d, 2) if d > 0 else (float(v) if v else 0.0)
    return jsonify(data)

@app.route("/api/duels/user/<user_id>/update", methods=["POST"])
def api_duels_user_update(user_id):
    data = request.json or {}
    ok = admin_update_duel_profil(user_id, data)
    if not ok:
        return jsonify({"error": "Aucune mise à jour"}), 400
    return jsonify({"success": True})

@app.route("/api/duels/user/<user_id>/sabres/add", methods=["POST"])
def api_duels_user_sabre_add(user_id):
    data = request.json or {}
    sabre_id = data.get("sabre_id")
    if not sabre_id or not db_get_sabre(sabre_id):
        return jsonify({"error": "Sabre inconnu"}), 400
    db_ajouter_sabre_collection(user_id, sabre_id)
    return jsonify({"success": True})

@app.route("/api/duels/user/<user_id>/sabres/remove", methods=["POST"])
def api_duels_user_sabre_remove(user_id):
    data = request.json or {}
    sabre_id = data.get("sabre_id")
    if not sabre_id:
        return jsonify({"error": "sabre_id requis"}), 400
    admin_supprimer_sabre_collection(user_id, sabre_id)
    return jsonify({"success": True})

@app.route("/api/sabres")
def api_sabres_list():
    """Liste publique des sabres : exclut les sabres saisonniers du Battle Pass
    (qui ont leur propre endpoint owner /api/owner/seasonal-sabres)."""
    sabres = db_get_tous_sabres()
    filtered = [s for s in sabres.values() if not s["id"].startswith("season_")]
    return jsonify({"sabres": filtered, "raretes": RARETES})

@app.route("/api/sabres/create", methods=["POST"])
def api_sabres_create():
    data = request.json or {}
    if not data.get("id") or not data.get("nom") or not data.get("rarete"):
        return jsonify({"error": "id, nom, rarete requis"}), 400
    if data["rarete"] not in RARETES:
        return jsonify({"error": "rareté invalide"}), 400
    ok = db_create_sabre(data)
    if not ok:
        return jsonify({"error": "Un sabre avec cet ID existe déjà"}), 400
    return jsonify({"success": True})

@app.route("/api/sabres/<sabre_id>/update", methods=["POST"])
def api_sabres_update(sabre_id):
    data = request.json or {}
    if "rarete" in data and data["rarete"] not in RARETES:
        return jsonify({"error": "rareté invalide"}), 400
    ok = db_update_sabre(sabre_id, data)
    if not ok:
        return jsonify({"error": "Aucune mise à jour"}), 400
    return jsonify({"success": True})

@app.route("/api/sabres/<sabre_id>/delete", methods=["POST"])
def api_sabres_delete(sabre_id):
    if sabre_id == "bleu":
        return jsonify({"error": "Le sabre 'bleu' ne peut pas être supprimé (sabre par défaut)"}), 400
    ok = db_delete_sabre(sabre_id)
    if not ok:
        return jsonify({"error": "Sabre introuvable"}), 404
    return jsonify({"success": True})


# =====================================================================
# MUSIQUE (per-guild)
# =====================================================================

@app.route("/music")
def music_page():
    return render_template("music.html")

@app.route("/api/music/state")
def api_music_state():
    g_id = gid()
    return jsonify({
        "state": music_state_get(g_id) or {},
        "queue": music_queue_list(g_id),
    })

@app.route("/api/music/play", methods=["POST"])
def api_music_play():
    g_id = gid()
    data = request.json or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query requis"}), 400
    cmd_id = bot_command_enqueue(g_id, "music_play", {
        "query": query,
        "voice_channel_id": data.get("voice_channel_id"),
    })
    return jsonify({"success": True, "command_id": cmd_id})

@app.route("/api/music/skip", methods=["POST"])
def api_music_skip():
    cid = bot_command_enqueue(gid(), "music_skip", {})
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/music/stop", methods=["POST"])
def api_music_stop():
    cid = bot_command_enqueue(gid(), "music_stop", {})
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/music/pause", methods=["POST"])
def api_music_pause():
    cid = bot_command_enqueue(gid(), "music_pause", {})
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/music/resume", methods=["POST"])
def api_music_resume():
    cid = bot_command_enqueue(gid(), "music_resume", {})
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/music/leave", methods=["POST"])
def api_music_leave():
    cid = bot_command_enqueue(gid(), "music_leave", {})
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/music/clear", methods=["POST"])
def api_music_clear():
    cid = bot_command_enqueue(gid(), "music_clear", {})
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/music/remove", methods=["POST"])
def api_music_remove():
    data = request.json or {}
    track_id = data.get("track_id")
    if track_id is None:
        return jsonify({"error": "track_id requis"}), 400
    cid = bot_command_enqueue(gid(), "music_remove_track", {"track_id": track_id})
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/music/command/<int:cmd_id>")
def api_music_command_status(cmd_id):
    row = bot_command_get(cmd_id)
    if not row:
        return jsonify({"error": "Commande introuvable"}), 404
    return jsonify(row)


# =====================================================================
# LOGS (per-guild)
# =====================================================================

@app.route("/logs")
def logs_page():
    return render_template("logs.html")

@app.route("/api/logs")
def api_logs():
    g_id = gid()
    type_filter = request.args.get("type") or None  # commands | actions | None
    search = (request.args.get("q") or "").strip() or None
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 1000))
    except ValueError:
        limit = 200
    rows = get_logs(g_id, type_filter=type_filter, search=search, limit=limit)
    return jsonify({"logs": rows})


# =====================================================================
# BOTTALK (per-guild)
# =====================================================================

@app.route("/bottalk")
def bottalk_page():
    return render_template("bottalk.html")

@app.route("/api/channels")
def api_channels():
    g_id = gid()
    type_filter = request.args.get("type")  # 'text' | 'voice' | None
    rows = list_channels(g_id, type_filter=type_filter)
    return jsonify({"channels": rows})

# =====================================================================
# DMs (global, cross-guild)
# =====================================================================

@app.route("/dms")
def dms_page():
    return render_template("dms.html")

@app.route("/api/dms/conversations")
def api_dms_conversations():
    return jsonify({
        "conversations": list_dm_conversations(),
        "unread": count_unread_dms(),
    })

@app.route("/api/dms/conversation/<user_id>")
def api_dms_conversation(user_id):
    msgs = get_dm_conversation(user_id, limit=500)
    mark_dm_read(user_id)  # auto-marquer comme lu a la consultation
    return jsonify({"messages": msgs})

@app.route("/api/dms/send", methods=["POST"])
def api_dms_send():
    data = request.json or {}
    user_id = (data.get("user_id") or "").strip()
    content = (data.get("content") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id requis"}), 400
    if not content:
        return jsonify({"error": "content vide"}), 400
    if len(content) > 2000:
        return jsonify({"error": "content trop long (max 2000 chars)"}), 400
    # DM est global : pas de guild_id, on stocke '0'
    cid = bot_command_enqueue("0", "dm_send", {
        "user_id": user_id,
        "content": content,
    })
    return jsonify({"success": True, "command_id": cid})

@app.route("/api/dms/mark-read/<user_id>", methods=["POST"])
def api_dms_mark_read(user_id):
    mark_dm_read(user_id)
    return jsonify({"success": True})

@app.route("/api/dms/conversation/<user_id>", methods=["DELETE"])
def api_dms_delete(user_id):
    n = delete_dm_conversation(user_id)
    return jsonify({"success": True, "deleted": n})


@app.route("/api/bottalk/send", methods=["POST"])
def api_bottalk_send():
    g_id = gid()
    data = request.json or {}
    channel_id = (data.get("channel_id") or "").strip()
    content    = (data.get("content") or "").strip()
    embed      = data.get("embed")
    if not channel_id:
        return jsonify({"error": "channel_id requis"}), 400
    if not content and not embed:
        return jsonify({"error": "content ou embed requis"}), 400
    if len(content) > 2000:
        return jsonify({"error": "content trop long (max 2000 chars)"}), 400
    payload = {"channel_id": channel_id, "content": content}
    if embed and isinstance(embed, dict):
        # Sanitize : on ne forwarde que les clés attendues
        allowed = {"title","description","url","color","author_name","author_url","author_icon",
                   "footer_text","footer_icon","image","thumbnail","fields","timestamp"}
        clean = {k: v for k, v in embed.items() if k in allowed}
        # Trim fields
        if isinstance(clean.get("fields"), list):
            clean["fields"] = [
                {"name": str(f.get("name", ""))[:256],
                 "value": str(f.get("value", ""))[:1024],
                 "inline": bool(f.get("inline"))}
                for f in clean["fields"][:25]
            ]
        payload["embed"] = clean
    cid = bot_command_enqueue(g_id, "bot_say", payload)
    return jsonify({"success": True, "command_id": cid})


# =====================================================================
# SETTINGS (global)
# =====================================================================

@app.route("/settings")
def settings_page():
    return render_template("settings.html",
                           settings=get_all_settings(),
                           defaults=DEFAULT_SETTINGS)

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify({"settings": get_all_settings(), "defaults": DEFAULT_SETTINGS})

@app.route("/api/settings", methods=["POST"])
def api_settings_set():
    data = request.json or {}
    allowed = set(DEFAULT_SETTINGS.keys())
    updated = []
    for k, v in data.items():
        if k in allowed:
            set_setting(k, v)
            updated.append(k)
    return jsonify({"success": True, "updated": updated})


# =====================================================================
# MODERATION (per-guild) — list members + kick/ban/timeout/unban
# =====================================================================

@app.route("/moderation")
def moderation_page():
    return render_template("moderation.html")

@app.route("/api/members")
def api_members():
    g_id = gid()
    search = (request.args.get("q") or "").strip() or None
    include_bots = request.args.get("bots") == "1"
    rows = list_members(g_id, include_bots=include_bots, search=search, limit=300)
    return jsonify({"members": rows})

@app.route("/api/moderation/<action>", methods=["POST"])
def api_moderation(action):
    if action not in ("kick", "ban", "timeout", "unban"):
        return jsonify({"error": "action invalide"}), 400
    g_id = gid()
    data = request.json or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id requis"}), 400
    payload = {
        "user_id":          user_id,
        "reason":           (data.get("reason") or "").strip() or None,
    }
    if action == "ban":
        payload["delete_seconds"] = int(data.get("delete_seconds", 0) or 0)
    if action == "timeout":
        payload["duration_minutes"] = int(data.get("duration_minutes", 10) or 10)
    cid = bot_command_enqueue(g_id, f"mod_{action}", payload)
    return jsonify({"success": True, "command_id": cid})


# =====================================================================
# STATUS / HEALTH (public-after-login, global)
# =====================================================================

# On lit l'etat du bot via la DB (process séparé). Le bot persiste son etat
# dans une mini-table 'kv' qu'on cree a la volee. Plus simple : on stocke
# le pid + boot ts dans bot_state.json a cote.

import os as _os
_BOT_STATE_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "bot_state.json")

def _read_bot_state():
    try:
        with open(_BOT_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

@app.route("/status")
def status_page():
    return render_template("status.html")

@app.route("/api/status")
def api_status():
    # DB stats
    db = get_db()
    counts = {}
    for tbl in ("users", "reactions", "logs", "dm_messages", "music_queue", "guilds", "sabres", "duel_profil", "duel_collection"):
        try:
            counts[tbl] = db.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()["n"]
        except Exception:
            counts[tbl] = None
    db_size_bytes = None
    try:
        db_size_bytes = _os.path.getsize("bot_database.db")
    except Exception:
        pass
    db.close()

    # Bot state via fichier partagé
    bot_state = _read_bot_state() or {}
    now = time.time()

    # Login attempts récents
    login_log = list(_LOGIN_LOG)[:20]

    return jsonify({
        "now": now,
        "bot": bot_state,
        "db": {
            "counts":    counts,
            "size_bytes": db_size_bytes,
        },
        "login_log": login_log,
        "session": {
            "logged_in": bool(session.get("logged_in")),
            "login_ts":  session.get("login_ts"),
            "login_ip":  session.get("login_ip"),
        },
    })


# =====================================================================
# PREMIUM (achats Discord SKU integres)
# =====================================================================

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
    """Wrapper unifie : entitlement Discord OU grant manuel OU owner ENV.

    Si feature='all', on accepte aussi 'pass' (les abonnés Pass ont automatiquement
    le pack /niveau Premium).
    """
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


@app.route("/premium")
def premium_page():
    uid = _current_user_id()
    if not uid:
        return redirect(url_for("oauth_login"))
    is_premium = _is_premium(uid)
    settings_p = get_premium_settings(uid) if is_premium else {}
    # On passe user_id pour que le BG custom owner apparaisse pour lui seul.
    backgrounds = list_available_backgrounds(user_id=uid)
    user = session.get("discord") or {}
    return render_template(
        "premium.html",
        is_premium=is_premium,
        settings_p=settings_p,
        backgrounds=backgrounds,
        user=user,
        active_nav="premium",
    )


@app.route("/api/premium/status", methods=["GET"])
def api_premium_status():
    uid = _current_user_id()
    if not uid:
        return jsonify({"ok": False, "error": "not_logged_in"}), 401
    return jsonify({
        "ok":           True,
        "is_premium":   _is_premium(uid),
        "entitlements": list_user_entitlements(uid),
        "grants":       list_premium_grants(uid),
        "settings":     get_premium_settings(uid),
    })


@app.route("/api/premium/niveau", methods=["POST"])
def api_premium_niveau_update():
    uid = _require_premium_user()
    if not uid:
        return jsonify({"ok": False, "error": "premium_required"}), 403
    data = request.get_json(silent=True) or {}
    bg = data.get("background")
    if not bg:
        return jsonify({"ok": False, "error": "missing_background"}), 400
    if bg not in list_available_backgrounds(user_id=uid):
        return jsonify({"ok": False, "error": "unknown_background"}), 400
    set_premium_setting(uid, "niveau_background", bg)
    return jsonify({"ok": True, "settings": get_premium_settings(uid)})


@app.route("/api/premium/niveau/preview.png")
def api_premium_niveau_preview():
    """Genere une carte preview avec les infos OAuth + bg passe en query.

    Utilise pour preview live sur le dashboard sans avoir a sauvegarder d'abord.
    """
    uid = _require_premium_user()
    if not uid:
        # Pour preview on autorise les utilisateurs connectes meme non-premium
        # afin qu'ils voient un apercu, mais on bloque les anonymes.
        if not _current_user_id():
            return ("", 403)
    user = session.get("discord") or {}
    requested_uid = uid or _current_user_id()
    bg = request.args.get("bg") or get_premium_settings(requested_uid).get("niveau_background", "default")
    allowed_bgs = list_available_backgrounds(user_id=requested_uid)
    if bg not in allowed_bgs:
        bg = "default"

    # XP fictifs pour preview (vrais XP necessitent un guild_id selectionne).
    # session["discord"]["avatar"] est deja une URL CDN complete (cf oauth_callback).
    avatar_url = user.get("avatar") or None
    import asyncio
    buf = asyncio.run(render_niveau_card(
        username=user.get("username") or "Toi",
        avatar_url=avatar_url,
        level=12,
        xp_total=8420,
        xp_in_level=320,
        xp_needed=900,
        background=bg,
    ))
    return send_file(buf, mimetype="image/png")


# ===== Owner : page de paramètres avancée (custom BG, etc.) =====

@app.route("/owner/settings")
def owner_settings_page():
    if not _is_owner_session():
        abort(403)
    uid = _current_user_id()
    has_custom = has_owner_custom_bg(uid) if uid else False
    return render_template(
        "owner_settings.html",
        active_nav="owner_settings",
        has_custom=has_custom,
        owner_id=uid,
    )


@app.route("/api/owner/niveau-bg", methods=["POST"])
def api_owner_niveau_bg_upload():
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "no_user"}), 400
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "missing_file"}), 400

    allowed_ext = {".png", ".jpg", ".jpeg", ".webp"}
    ext = os.path.splitext(f.filename.lower())[1]
    if ext not in allowed_ext:
        return jsonify({"error": "unsupported_format", "allowed": list(allowed_ext)}), 400

    # Limite 10 Mo
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > 10 * 1024 * 1024:
        return jsonify({"error": "file_too_large", "max_bytes": 10 * 1024 * 1024}), 400

    # Charger via Pillow + redimensionner cote serveur
    try:
        from PIL import Image as _PIL
        img = _PIL.open(f.stream)
        save_owner_custom_bg(uid, img)
    except Exception as e:
        return jsonify({"error": "cannot_decode", "detail": str(e)}), 400

    return jsonify({"ok": True, "bg_id": f"owner:{uid}"})


# ─── Live console (tail pm2 logs) ─────────────────────────────────────────

_PM2_LOG_PATHS = {
    "bot": {
        "out": os.path.expanduser("~/.pm2/logs/discord-bot-out.log"),
        "err": os.path.expanduser("~/.pm2/logs/discord-bot-error.log"),
    },
    "web": {
        "out": os.path.expanduser("~/.pm2/logs/web-dashboard-out.log"),
        "err": os.path.expanduser("~/.pm2/logs/web-dashboard-error.log"),
    },
}
_LOG_INITIAL_TAIL = 32 * 1024  # 32 KB de l'historique a la 1ere connexion
_LOG_MAX_CHUNK    = 256 * 1024  # cap par requete (eviter mega payload)


@app.route("/api/owner/logs", methods=["GET"])
def api_owner_logs():
    """Polling tail des logs pm2.

    Params:
        proc:   'bot' | 'web'
        stream: 'out' | 'err'
        offset: position en bytes (envoyee par la reponse precedente)

    Reponse: { offset: <new_size>, text: "<delta>" }
    """
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    proc   = request.args.get("proc", "bot")
    stream = request.args.get("stream", "out")
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    path = _PM2_LOG_PATHS.get(proc, {}).get(stream)
    if not path or not os.path.exists(path):
        return jsonify({"offset": 0, "text": "", "missing": True, "path": path})
    try:
        size = os.path.getsize(path)
        # Premiere connexion (offset=0) -> on saute au derniere portion du fichier
        if offset == 0 and size > _LOG_INITIAL_TAIL:
            offset = size - _LOG_INITIAL_TAIL
        # Si l'ancienne offset est plus grande que la taille (rotation pm2 logs),
        # on repart du debut du nouveau fichier.
        if offset > size:
            offset = max(0, size - _LOG_INITIAL_TAIL)
        chunk_size = min(_LOG_MAX_CHUNK, size - offset)
        if chunk_size <= 0:
            return jsonify({"offset": size, "text": ""})
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(chunk_size)
        text = data.decode("utf-8", errors="replace")
        return jsonify({"offset": offset + len(data), "text": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/owner/seasonal-sabres", methods=["GET"])
def api_owner_seasonal_sabres():
    """Liste tous les sabres saisonniers (id LIKE 'season_%') pour visu owner."""
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    db = get_db()
    rows = db.execute(
        '''SELECT id, nom, emoji, rarete, description, speciale_nom,
                  speciale_description, speciale_emoji, speciale_effet
           FROM sabres
           WHERE id LIKE 'season_%'
           ORDER BY id DESC'''
    ).fetchall()
    db.close()
    return jsonify({"sabres": [dict(r) for r in rows]})


@app.route("/api/owner/niveau-bg", methods=["DELETE"])
def api_owner_niveau_bg_delete():
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "no_user"}), 400
    remove_owner_custom_bg(uid)
    # Reset le BG selectionne si c'etait celui-la
    cur = get_premium_settings(uid).get("niveau_background")
    if cur == f"owner:{uid}":
        set_premium_setting(uid, "niveau_background", "default")
    return jsonify({"ok": True})


# ===== Admin (owner ou admin Discord du serveur) : edit XP/niveau d'un membre =====

def _level_to_min_xp(level: int) -> int:
    """Inverse de get_level (xp -> level = int(xp**0.2))."""
    if level <= 0:
        return 0
    # Plus petit xp tel que int(xp**0.2) == level => xp = level**5
    return level ** 5


@app.route("/api/user/<user_id>/xp", methods=["POST"])
def api_user_xp_set(user_id):
    """Modifie XP (ou niveau) d'un membre sur le serveur courant.

    Accepte JSON {xp: int} OU {level: int}. Si level fourni, on calcule
    le minimum d'XP requis pour ce niveau. Recalcule level depuis xp via
    set_xp() qui applique la formule canonique.
    """
    if not _is_admin_of_current_guild():
        return jsonify({"error": "admin_only"}), 403
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild_selected"}), 400
    db = get_db()
    row = db.execute(
        "SELECT username, xp, level FROM users WHERE guild_id = ? AND user_id = ?",
        (g_id, str(user_id)),
    ).fetchone()
    db.close()
    if not row:
        return jsonify({"error": "user_not_found_on_guild"}), 404

    data = request.get_json(silent=True) or {}
    new_xp = None
    if "xp" in data and data["xp"] is not None:
        try:
            new_xp = max(0, int(data["xp"]))
        except (TypeError, ValueError):
            return jsonify({"error": "bad_xp"}), 400
    elif "level" in data and data["level"] is not None:
        try:
            target_level = max(0, int(data["level"]))
        except (TypeError, ValueError):
            return jsonify({"error": "bad_level"}), 400
        new_xp = _level_to_min_xp(target_level)
    else:
        return jsonify({"error": "missing_xp_or_level"}), 400

    set_xp(g_id, user_id, new_xp, username=row["username"])
    # Recompute pour reponse (formule int(xp**0.2))
    new_level = int(new_xp ** 0.2) if new_xp > 0 else 0
    return jsonify({
        "ok":       True,
        "user_id":  str(user_id),
        "guild_id": g_id,
        "xp":       new_xp,
        "level":    new_level,
    })


# ===== API publique : stats pour landing tookbot.click =====
# Cache 1h pour eviter de bombarder la DB depuis la home page.

import time as _time
_PUBLIC_STATS_CACHE = {"data": None, "expires": 0.0}
_PUBLIC_STATS_TTL_SEC = 3600  # 1h


@app.route("/api/public-stats", methods=["GET"])
def api_public_stats():
    """Stats publiques affichees sur la landing tookbot.click.

    Pas d'auth, CORS ouvert pour permettre le fetch cross-origin
    (apex tookbot.click -> dashboard.tookbot.click). Cache server-side
    1h pour limiter la charge DB.
    """
    now = _time.time()
    if _PUBLIC_STATS_CACHE["data"] and _PUBLIC_STATS_CACHE["expires"] > now:
        data = _PUBLIC_STATS_CACHE["data"]
    else:
        db = get_db()
        try:
            guild_count = db.execute(
                "SELECT COUNT(*) AS n FROM guilds WHERE active = 1"
            ).fetchone()["n"]
        except Exception:
            guild_count = db.execute("SELECT COUNT(*) AS n FROM guilds").fetchone()["n"]
        user_count = db.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM users"
        ).fetchone()["n"]
        db.close()
        data = {
            "guilds":     int(guild_count or 0),
            "users":      int(user_count  or 0),
            "updated_at": int(now),
        }
        _PUBLIC_STATS_CACHE["data"]    = data
        _PUBLIC_STATS_CACHE["expires"] = now + _PUBLIC_STATS_TTL_SEC

    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET"
    resp.headers["Cache-Control"]                = "public, max-age=3600"
    return resp


# ===== Tickets dashboard =====

@app.route("/tickets")
def tickets_page():
    return render_template("tickets.html", active_nav="tickets")


@app.route("/api/tickets/panels", methods=["GET"])
def api_tickets_panels_list():
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    return jsonify({"panels": ticket_panels_list(g_id)})


@app.route("/api/tickets/panels/<int:pid>", methods=["DELETE"])
def api_tickets_panel_delete(pid):
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    panel = ticket_panel_get(pid)
    if not panel or str(panel["guild_id"]) != str(g_id):
        return jsonify({"error": "not_found"}), 404
    n = ticket_panel_delete(pid, guild_id=g_id)
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/tickets", methods=["GET"])
def api_tickets_list():
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    status = request.args.get("status") or None
    return jsonify({"tickets": tickets_list(g_id, status=status, limit=100)})


@app.route("/api/tickets/<int:ticket_id>/close", methods=["POST"])
def api_tickets_close(ticket_id):
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    ticket_set_status(ticket_id, "closed", closed_by=_current_user_id())
    return jsonify({"ok": True})


# ===== Social Alerts dashboard =====

@app.route("/social-alerts")
def social_alerts_page():
    return render_template("social_alerts.html", active_nav="social_alerts")


@app.route("/api/social-alerts", methods=["GET"])
def api_social_alerts_list():
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    return jsonify({"alerts": social_alerts_list(guild_id=g_id)})


@app.route("/api/social-alerts", methods=["POST"])
def api_social_alerts_create():
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    data = request.get_json(silent=True) or {}
    plat = (data.get("platform") or "").strip()
    raw = (data.get("target_id") or "").strip()
    channel_id = data.get("channel_id")
    message = (data.get("message_template") or "").strip() or None

    if plat not in ("twitch", "youtube", "reddit"):
        return jsonify({"error": "platform invalide"}), 400
    if not raw or not channel_id:
        return jsonify({"error": "lien et salon requis"}), 400

    parsed = social.parse_social_url(plat, raw)
    if not parsed:
        examples = {
            "twitch":  "https://twitch.tv/<pseudo>",
            "youtube": "https://youtube.com/@<handle> ou /channel/UC...",
            "reddit":  "https://reddit.com/r/<sub> ou /user/<u>",
        }
        return jsonify({"error": f"lien invalide. Exemple : {examples[plat]}"}), 400
    target, label = parsed

    aid = social_alert_create(
        guild_id=g_id, platform=plat, target_id=target, target_label=label,
        channel_id=channel_id, message_template=message,
        created_by=_current_user_id(),
    )
    return jsonify({"ok": True, "id": aid, "target": target, "label": label})


@app.route("/api/social-alerts/<int:alert_id>", methods=["DELETE"])
def api_social_alerts_delete(alert_id):
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    n = social_alert_delete(alert_id, guild_id=g_id)
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/social-alerts/<int:alert_id>/toggle", methods=["POST"])
def api_social_alerts_toggle(alert_id):
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    social_alert_set_enabled(alert_id, enabled, guild_id=g_id)
    return jsonify({"ok": True, "enabled": enabled})


# ===== Reaction Roles dashboard =====

@app.route("/reactionroles")
def reactionroles_page():
    return render_template("reactionroles.html", active_nav="reactionroles")


@app.route("/api/rolereactions", methods=["GET"])
def api_rolereactions_list():
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    rows = reaction_role_list(g_id)
    # Group by message
    by_msg: dict[str, list] = {}
    for r in rows:
        by_msg.setdefault(r["message_id"], []).append(r)
    out = []
    for msg_id, items in by_msg.items():
        out.append({
            "message_id": msg_id,
            "channel_id": items[0]["channel_id"],
            "mode":       items[0]["mode"],
            "mappings":   items,
        })
    out.sort(key=lambda x: int(x["message_id"]), reverse=True)
    return jsonify({"messages": out})


@app.route("/api/rolereactions/roles", methods=["GET"])
def api_rolereactions_roles():
    g_id = gid()
    if not g_id:
        return jsonify({"roles": []})
    return jsonify({"roles": list_roles(g_id)})


@app.route("/api/rolereactions/post", methods=["POST"])
def api_rolereactions_post():
    """Enqueue une commande bot pour poster un message role-reaction."""
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    data = request.get_json(silent=True) or {}
    channel_id  = data.get("channel_id")
    titre       = (data.get("titre") or "").strip() or "Choisis ton rôle"
    description = (data.get("description") or "").strip()
    mode        = data.get("mode") or "toggle"
    mappings    = data.get("mappings") or []
    if not channel_id or not mappings:
        return jsonify({"error": "channel_id et mappings requis"}), 400
    if mode not in ("toggle", "add_only", "unique"):
        return jsonify({"error": "mode invalide"}), 400
    for m in mappings:
        if not m.get("emoji_key") or not m.get("role_id"):
            return jsonify({"error": "mapping incomplet"}), 400

    cmd_id = bot_command_enqueue(g_id, "rolereaction_post", {
        "channel_id":  str(channel_id),
        "titre":       titre,
        "description": description,
        "mode":        mode,
        "mappings":    mappings,
        "by":          _current_user_id(),
    })
    return jsonify({"ok": True, "cmd_id": cmd_id})


@app.route("/api/rolereactions/command/<int:cmd_id>", methods=["GET"])
def api_rolereactions_command_status(cmd_id):
    """Permet au front de polling le statut d'une commande role-reaction
    pour afficher succes ou erreur explicite."""
    row = bot_command_get(cmd_id)
    if not row:
        return jsonify({"error": "command_not_found"}), 404
    return jsonify({
        "id":        row.get("id"),
        "cmd":       row.get("cmd"),
        "status":    row.get("status"),
        "result":    row.get("result"),
        "created_at":   row.get("created_at"),
        "processed_at": row.get("processed_at"),
    })


@app.route("/api/rolereactions/<message_id>", methods=["DELETE"])
def api_rolereactions_delete(message_id):
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    n = reaction_role_remove_message(g_id, message_id)
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/rolereactions/<message_id>/<emoji>", methods=["DELETE"])
def api_rolereactions_delete_emoji(message_id, emoji):
    g_id = gid()
    if not g_id:
        return jsonify({"error": "no_guild"}), 400
    n = reaction_role_remove(g_id, message_id, emoji)
    return jsonify({"ok": True, "deleted": n})


# ===== Pass : page utilisateur "Mon Pass" =====

@app.route("/my-pass")
def my_pass_page():
    uid = _current_user_id()
    if not uid:
        return redirect(url_for("oauth_login"))
    return render_template("my_pass.html", active_nav="my_pass")


@app.route("/api/my/pass", methods=["GET"])
def api_my_pass():
    """Etat complet du Pass pour l'user connecte (lecture seule)."""
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "not_logged_in"}), 401
    season   = get_or_create_current_season()
    sid      = season["season_id"]
    progress = get_pass_progress(uid, sid)
    quests   = list_user_active_quests(uid)
    unlocks  = list_user_pass_unlocks(uid)
    has_pass = _has_pass(uid)
    cosmetics_owned = list_user_owned_cosmetics(uid)
    cosmetics_active = get_user_cosmetic(uid)
    # Roadmap des paliers (rewards definis pour la saison)
    db = get_db()
    rows = db.execute(
        "SELECT tier, type, label FROM pass_rewards WHERE season_id = ? ORDER BY tier",
        (sid,),
    ).fetchall()
    db.close()
    rewards = [dict(r) for r in rows]
    return jsonify({
        "has_pass":   has_pass,
        "season":     season,
        "progress":   progress,
        "quests":     quests,
        "unlocks":    unlocks,
        "rewards":    rewards,
        "cosmetics_owned":  cosmetics_owned,
        "cosmetics_active": cosmetics_active,
    })


@app.route("/api/my/cosmetic", methods=["POST"])
def api_my_cosmetic_set():
    """Selectionne un titre/emoji parmi ceux possedes via Pass."""
    from database import set_premium_setting as _set_setting
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "not_logged_in"}), 401
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")  # 'title' | 'emoji'
    value = data.get("value")  # str ou None pour reset

    if kind not in ("title", "emoji"):
        return jsonify({"error": "bad_kind"}), 400

    # Verif possession si valeur fournie
    if value:
        owned = list_user_owned_cosmetics(uid)
        pool = owned["titles"] if kind == "title" else owned["emojis"]
        if value not in pool:
            return jsonify({"error": "not_owned"}), 400

    setting_key = f"pass_selected_{kind}"
    _set_setting(uid, setting_key, value or None)
    return jsonify({"ok": True, "kind": kind, "value": value})


# ===== Owner-only : Pass grant/revoke + status =====

@app.route("/api/user/<user_id>/pass", methods=["GET"])
def api_user_pass_status(user_id):
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    has_pass = _has_pass(user_id)
    season = get_or_create_current_season()
    progress = get_pass_progress(user_id, season["season_id"])
    unlocks = list_user_pass_unlocks(user_id)
    return jsonify({
        "user_id":  str(user_id),
        "has_pass": has_pass,
        "season":   season,
        "progress": progress,
        "unlocks":  unlocks,
    })


@app.route("/api/user/<user_id>/pass", methods=["POST"])
def api_user_pass_grant(user_id):
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    data = request.get_json(silent=True) or {}
    note = data.get("note")
    add_premium_grant(user_id, feature="pass", granted_by=_current_user_id(), note=note)
    return jsonify({"ok": True, "user_id": str(user_id), "feature": "pass"})


@app.route("/api/user/<user_id>/pass", methods=["DELETE"])
def api_user_pass_revoke(user_id):
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    remove_premium_grant(user_id, feature="pass")
    return jsonify({"ok": True, "user_id": str(user_id), "feature": "pass", "revoked": True})


@app.route("/api/user/<user_id>/pass", methods=["PATCH"])
def api_user_pass_set_xp(user_id):
    """Reglage manuel de l'XP de saison (owner). Reset claimed_max_tier en
    consequence pour ne pas garder un palier > xp_actuel."""
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    season = get_or_create_current_season()
    sid = season["season_id"]
    data = request.get_json(silent=True) or {}
    if "xp" not in data:
        return jsonify({"error": "missing_xp"}), 400
    try:
        new_xp = max(0, int(data["xp"]))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_xp"}), 400

    # Si on baisse l'XP, on reset claimed_max_tier sinon les paliers superieurs
    # restent debloques. Si on monte, on garde claimed_max_tier (autoclaim suit).
    db = get_db()
    c = db.cursor()
    if new_xp == 0:
        c.execute('''
            INSERT INTO pass_progress (user_id, season_id, xp, claimed_max_tier, updated_at)
            VALUES (?, ?, 0, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, season_id) DO UPDATE SET
                xp = 0, claimed_max_tier = 0, updated_at = CURRENT_TIMESTAMP
        ''', (str(user_id), sid))
    else:
        c.execute('''
            INSERT INTO pass_progress (user_id, season_id, xp, claimed_max_tier, updated_at)
            VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, season_id) DO UPDATE SET
                xp = excluded.xp,
                updated_at = CURRENT_TIMESTAMP
        ''', (str(user_id), sid, new_xp))
    db.commit()
    db.close()

    # Auto-claim des paliers franchis par cette modif
    delivered = []
    if new_xp > 0:
        try:
            delivered = auto_claim_pass_tiers(user_id, sid, new_xp)
            for d in delivered:
                print(f"[pass admin] user={user_id} unlock tier {d['tier']} ({d['type']}: {d.get('label')})")
        except Exception as e:
            print(f"[pass admin] auto_claim error: {e!r}")

    return jsonify({
        "ok": True, "user_id": str(user_id), "season_id": sid, "xp": new_xp,
        "delivered": delivered,
    })


@app.route("/api/user/<user_id>/pass/quests", methods=["GET"])
def api_user_pass_quests_list(user_id):
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    quests = list_user_active_quests(user_id)
    return jsonify({"quests": quests})


@app.route("/api/user/<user_id>/pass/quests", methods=["DELETE"])
def api_user_pass_quests_reroll(user_id):
    """Supprime les quetes de la periode courante -> seront re-tirees au prochain
    appel de list_user_active_quests."""
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    import datetime as _dt
    now = _dt.datetime.utcnow()
    daily_ps  = now.strftime("%Y-%m-%d")
    weekly_ps = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
    db = get_db()
    c = db.cursor()
    c.execute(
        "DELETE FROM pass_user_quests WHERE user_id = ? AND ((period='daily' AND period_start=?) OR (period='weekly' AND period_start=?))",
        (str(user_id), daily_ps, weekly_ps),
    )
    deleted = c.rowcount
    db.commit()
    db.close()
    # Re-genere immediat
    quests = list_user_active_quests(user_id)
    return jsonify({"ok": True, "deleted": deleted, "quests": quests})


# ===== Owner-only : grant/revoke premium manuel =====

@app.route("/api/user/<user_id>/premium", methods=["GET"])
def api_user_premium_status(user_id):
    """Statut premium d'un user vu par l'owner depuis le dashboard."""
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    return jsonify({
        "user_id":      str(user_id),
        "is_premium":   _is_premium(user_id),
        "is_owner":     bool(DISCORD_OWNER_ID) and str(user_id) == str(DISCORD_OWNER_ID),
        "grants":       list_premium_grants(user_id),
        "entitlements": list_user_entitlements(user_id),
    })


@app.route("/api/user/<user_id>/premium", methods=["POST"])
def api_user_premium_grant(user_id):
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    data = request.get_json(silent=True) or {}
    feature = data.get("feature") or "all"
    note    = data.get("note")
    add_premium_grant(user_id, feature=feature, granted_by=_current_user_id(), note=note)
    return jsonify({"ok": True, "user_id": str(user_id), "feature": feature})


@app.route("/api/user/<user_id>/premium", methods=["DELETE"])
def api_user_premium_revoke(user_id):
    if not _is_owner_session():
        return jsonify({"error": "owner_only"}), 403
    feature = (request.args.get("feature") or "all").strip()
    remove_premium_grant(user_id, feature=feature)
    return jsonify({"ok": True, "user_id": str(user_id), "feature": feature, "revoked": True})


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug, use_reloader=False)
