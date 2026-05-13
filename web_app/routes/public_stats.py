from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

_PUBLIC_STATS_CACHE = {"data": None, "expires": 0.0}
_PUBLIC_STATS_TTL_SEC = 3600

def register_public_stats_routes(app, deps):
    globals().update(deps)
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
