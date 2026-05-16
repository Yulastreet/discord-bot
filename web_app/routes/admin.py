from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file
from services.status_utils import create_db_backup, db_info, read_backup_meta, system_metrics, youtube_diagnostics, ROOT_DIR

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


    @app.route("/bottalk")
    def bottalk_page():
        return render_template("bottalk.html")

    @app.route("/api/channels")
    def api_channels():
        g_id = gid()
        type_filter = request.args.get("type")  # 'text' | 'voice' | None
        rows = list_channels(g_id, type_filter=type_filter)
        return jsonify({"channels": rows})

    # Note : la feature "Messages privés" (lecture/envoi de DM via dashboard
    # + stockage en base) a été retirée volontairement (raison vie privée).
    # Les DM users -> bot ne sont plus enregistrés. Voir privacy.html §1.3.

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

    @app.route("/api/guild-settings", methods=["GET"])
    def api_guild_settings_get():
        g_id = gid()
        return jsonify({"settings": guild_settings_all(g_id)})

    @app.route("/api/guild-settings", methods=["POST"])
    def api_guild_settings_set():
        g_id = gid()
        data = request.json or {}
        allowed = {"xp_enabled"}
        updated = []
        for k, v in data.items():
            if k in allowed:
                guild_setting_set(g_id, k, "1" if str(v) in ("1", "true", "True", "on") else "0")
                updated.append(k)
        return jsonify({"success": True, "updated": updated})


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

    @app.route("/api/moderation/warn", methods=["POST"])
    def api_moderation_warn():
        from database import (mod_action_add, mod_action_count_active, mod_config_get,
                              mod_action_get)
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        user_id = (data.get("user_id") or "").strip()
        reason  = (data.get("reason") or "").strip() or None
        if not user_id:
            return jsonify({"error": "user_id requis"}), 400
        if reason and len(reason) > 500:
            return jsonify({"error": "raison max 500 caracteres"}), 400
        aid = mod_action_add(g_id, user_id, "warn",
                             reason=reason, moderator_id=_current_user_id())
        active = mod_action_count_active(g_id, user_id, "warn")
        # Enqueue une commande bot pour DM + modlog + auto-timeout
        bot_command_enqueue(g_id, "mod_warn_followup", {
            "action_id":  aid,
            "user_id":    user_id,
            "moderator_id": _current_user_id(),
            "reason":     reason,
        })
        return jsonify({"success": True, "action_id": aid, "active_warns": active})


    @app.route("/api/moderation/modlogs", methods=["GET"])
    def api_moderation_modlogs():
        from database import mod_actions_list, mod_action_count_active
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        user_id = (request.args.get("user_id") or "").strip() or None
        actions = mod_actions_list(g_id, user_id=user_id, limit=200)
        active_warns = mod_action_count_active(g_id, user_id, "warn") if user_id else None
        return jsonify({"actions": actions, "active_warns": active_warns})


    @app.route("/api/moderation/revoke", methods=["POST"])
    def api_moderation_revoke():
        from database import mod_action_revoke, mod_action_get
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        action_id = int(data.get("action_id") or 0)
        revoke_reason = (data.get("reason") or "").strip() or None
        if not action_id:
            return jsonify({"error": "action_id requis"}), 400
        a = mod_action_get(action_id)
        if not a or str(a.get("guild_id")) != str(g_id):
            return jsonify({"error": "introuvable"}), 404
        ok = mod_action_revoke(action_id, _current_user_id(), revoke_reason)
        return jsonify({"success": ok})


    @app.route("/api/moderation/config", methods=["GET"])
    def api_moderation_config_get():
        from database import mod_config_get
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        return jsonify(mod_config_get(g_id))


    @app.route("/api/moderation/config", methods=["POST"])
    def api_moderation_config_set():
        from database import mod_config_upsert
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        threshold = data.get("autotimeout_threshold")
        duration  = data.get("autotimeout_duration")
        modlog    = data.get("modlog_channel_id")
        mod_config_upsert(
            g_id,
            autotimeout_threshold=int(threshold) if threshold is not None else None,
            autotimeout_duration=int(duration) if duration is not None else None,
            modlog_channel_id=str(modlog) if modlog else None,
        )
        return jsonify({"success": True})


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
            "moderator_id":     _current_user_id(),
        }
        if action == "ban":
            payload["delete_seconds"] = int(data.get("delete_seconds", 0) or 0)
        if action == "timeout":
            payload["duration_minutes"] = int(data.get("duration_minutes", 10) or 10)
        cid = bot_command_enqueue(g_id, f"mod_{action}", payload)
        return jsonify({"success": True, "command_id": cid})


    # On lit l'etat du bot via la DB (process séparé). Le bot persiste son etat
    # dans une mini-table 'kv' qu'on cree a la volee. Plus simple : on stocke
    # le pid + boot ts dans bot_state.json a cote.

    import os as _os
    _BOT_STATE_FILE = _os.path.join(str(ROOT_DIR), "bot_state.json")

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
        database_info = db_info()
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
                **database_info,
                "backup": read_backup_meta(),
            },
            "system": system_metrics(),
            "youtube": youtube_diagnostics(bot_state=bot_state),
            "login_log": login_log,
            "session": {
                "logged_in": bool(session.get("logged_in")),
                "login_ts":  session.get("login_ts"),
                "login_ip":  session.get("login_ip"),
            },
        })

    @app.route("/api/status/db-backup", methods=["POST"])
    def api_status_db_backup():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        try:
            return jsonify(create_db_backup())
        except FileNotFoundError:
            return jsonify({"error": "db_not_found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500


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
