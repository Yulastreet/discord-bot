from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

def register_admin_routes(app, deps):
    globals().update(deps)
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
