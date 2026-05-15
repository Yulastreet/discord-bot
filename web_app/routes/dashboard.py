from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file
from web_profile import build_user_profile_payload

def register_dashboard_routes(app, deps):
    globals().update(deps)
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
        payload = build_user_profile_payload(db, user_id, guild_id=g_id, is_owner=_is_owner_session())
        if not payload:
            db.close()
            return jsonify({"error": "Utilisateur non trouvé"}), 404
        db.close()
        return jsonify(payload)
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


