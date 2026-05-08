from flask import Flask, render_template, request, redirect, session, jsonify, g, url_for
import os
import time
import json
import secrets
from collections import deque
from datetime import timedelta
from dotenv import load_dotenv

from database import (
    init_db, get_db,
    # XP per-guild
    get_xp, get_leaderboard, get_all_users_for_guild, get_global_xp_stats,
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
)
from duel_sabres import RARETES

# Init DB + seed
init_db()

load_dotenv()

app = Flask(__name__)
# Cle de session fixe en prod (sinon les sessions sautent a chaque restart).
# Met FLASK_SECRET dans le .env, sinon clef ephemere.
app.secret_key = os.getenv("FLASK_SECRET") or os.urandom(24)
PASSWORD = os.getenv("WEB_PASSWORD")
if not PASSWORD:
    print("[WARN] WEB_PASSWORD non defini dans .env — login impossible.")

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


# =====================================================================
# AUTH + GUILD CONTEXT MIDDLEWARE
# =====================================================================

PUBLIC_PATHS = {"/", "/static"}        # tout le reste exige login
GUILD_FREE_PATHS = {                   # routes qui n'exigent pas de guild sélectionné
    "/", "/select-guild", "/general", "/logout",
    "/dms", "/status", "/settings",
    "/api/guilds", "/api/select-guild",
    "/api/dms", "/api/status", "/api/settings",
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

@app.before_request
def _ctx():
    g.logged_in = bool(session.get("logged_in"))
    g.guilds = list_guilds(active_only=True) if g.logged_in else []
    g.guild_id = session.get("guild_id")
    g.guild = get_guild(g.guild_id) if g.guild_id else None
    if g.guild_id and not g.guild:
        # Bot a quitté ce serveur — invalider la sélection
        session.pop("guild_id", None)
        g.guild_id = None

    path = request.path
    # Auth gate
    if not g.logged_in and path not in ("/",) and not path.startswith("/static"):
        if path.startswith("/api/"):
            return jsonify({"error": "Non authentifié"}), 401
        return redirect("/")
    # Guild gate
    if g.logged_in and not g.guild_id and needs_guild(path):
        if path.startswith("/api/"):
            return jsonify({"error": "Aucun serveur sélectionné", "redirect": "/select-guild"}), 412
        return redirect("/select-guild")


@app.context_processor
def _inject_ctx():
    return {
        "current_guild":  getattr(g, "guild", None),
        "current_guilds": getattr(g, "guilds", []),
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
    if request.method == "POST":
        ok, retry_in = _check_login_rate(ip)
        if not ok:
            return render_template(
                "login.html",
                error=f"Trop de tentatives. Réessaie dans {retry_in // 60}m {retry_in % 60}s.",
            ), 429
        submitted = request.form.get("password") or ""
        if PASSWORD and secrets.compare_digest(submitted, PASSWORD):
            session.permanent       = True
            session["logged_in"]    = True
            session["login_ts"]     = time.time()
            session["login_ip"]     = ip
            _record_login(ip, True)
            return redirect("/select-guild")
        _record_login(ip, False)
        return render_template("login.html", error="Mot de passe incorrect")
    if session.get("logged_in"):
        return redirect("/dashboard" if session.get("guild_id") else "/select-guild")
    return render_template("login.html")

@app.route("/logout")
def logout():
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
        return jsonify({"error": "Serveur inconnu"}), 404
    session["guild_id"] = g_id
    return jsonify({"success": True})

@app.route("/api/guilds")
def api_guilds():
    return jsonify({"guilds": list_guilds(active_only=True)})


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
    sabres = db_get_tous_sabres()
    return jsonify({"sabres": list(sabres.values()), "raretes": RARETES})

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


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug, use_reloader=False)
