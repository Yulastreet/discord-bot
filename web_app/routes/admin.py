from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file
from services.i18n import t
from services.status_utils import create_db_backup, db_info, read_backup_meta, system_metrics, music_engine_diagnostics, ROOT_DIR

def register_admin_routes(app, deps):
    globals().update(deps)
    @app.route("/logs")
    def logs_page():
        return render_template("logs.html")


    @app.route("/analytics")
    def analytics_page():
        return render_template(
            "analytics.html",
            active_nav="analytics",
            is_tookbot_plus=_is_tookbot_plus_session(),
        )


    def _is_tookbot_plus_session():
        from database import has_premium_grant, user_has_active_entitlement
        uid = _current_user_id()
        if not uid:
            return False
        if has_premium_grant(uid, feature="tookbot_plus", inherit_all=False):
            return True
        sku = globals().get("SKU_TOOKBOT_PLUS")
        if sku and user_has_active_entitlement(uid, sku_id=sku):
            return True
        return False


    @app.route("/api/analytics/cohort-retention")
    def api_analytics_cohort_retention():
        if not _is_tookbot_plus_session():
            return jsonify({"error": t("api.admin.plus_required")}), 402
        from database import get_cohort_retention
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        try:
            weeks = max(2, min(int(request.args.get("weeks", 12)), 24))
        except ValueError:
            weeks = 12
        return jsonify({"weeks": weeks, "cohorts": get_cohort_retention(g_id, weeks=weeks)})


    @app.route("/api/analytics/export/<kind>.csv")
    def api_analytics_export_csv(kind):
        if not _is_tookbot_plus_session():
            return jsonify({"error": t("api.admin.plus_required")}), 402
        from database import export_logs_csv_rows, get_top_commands, get_top_active_users
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        try:
            days = max(1, min(int(request.args.get("days", 90)), 365))
        except ValueError:
            days = 90
        import csv as _csv
        from io import StringIO
        buf = StringIO()
        w = _csv.writer(buf)
        if kind == "events":
            w.writerow(["ts", "type", "user_id", "username", "channel_name", "content"])
            for row in export_logs_csv_rows(g_id, days=days):
                w.writerow(row)
        elif kind == "top-commands":
            w.writerow(["rank", "command", "uses"])
            for i, r in enumerate(get_top_commands(g_id, days=days, limit=200), start=1):
                w.writerow([i, r.get("cmd"), r.get("n")])
        elif kind == "top-users":
            w.writerow(["rank", "user_id", "username", "events"])
            for i, r in enumerate(get_top_active_users(g_id, days=days, limit=200), start=1):
                w.writerow([i, r.get("user_id"), r.get("username"), r.get("n")])
        else:
            return jsonify({"error": t("api.admin.invalid_kind")}), 400
        data = buf.getvalue()
        from flask import Response
        resp = Response(data, mimetype="text/csv")
        resp.headers["Content-Disposition"] = (
            f"attachment; filename=tookbot_{kind}_{days}j.csv"
        )
        return resp


    @app.route("/api/notifications")
    def api_notifications_list():
        from database import dash_notif_list, dash_notif_unread_count
        uid = _current_user_id()
        print(f"[debug notif] /api/notifications uid={uid!r}", flush=True)
        if not uid:
            return jsonify({"unread": 0, "items": []})
        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 50))
        except ValueError:
            limit = 20
        items = dash_notif_list(uid, limit=limit)
        unread = dash_notif_unread_count(uid)
        return jsonify({"unread": unread, "items": items})


    @app.route("/api/notifications/mark-read", methods=["POST"])
    def api_notifications_mark_read():
        from database import dash_notif_mark_read
        uid = _current_user_id()
        if not uid:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        data = request.json or {}
        nid = data.get("id")
        dash_notif_mark_read(uid, notif_id=int(nid) if nid else None)
        return jsonify({"ok": True})


    @app.route("/automod")
    def automod_page():
        uid = _current_user_id()
        if not uid:
            return redirect(url_for("oauth_login"))
        # Gate TookBot+
        from database import has_premium_grant, user_has_active_entitlement, automod_config_get
        is_tookbot_plus = (
            has_premium_grant(uid, feature="tookbot_plus", inherit_all=False)
            or (globals().get("SKU_TOOKBOT_PLUS") and user_has_active_entitlement(uid, sku_id=globals().get("SKU_TOOKBOT_PLUS")))
        )
        g_id = gid()
        cfg = automod_config_get(g_id) if g_id else {}
        return render_template(
            "automod.html",
            active_nav="automod",
            is_tookbot_plus=bool(is_tookbot_plus),
            config=cfg,
        )


    @app.route("/api/automod/config", methods=["GET"])
    def api_automod_get():
        from database import automod_config_get
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        return jsonify(automod_config_get(g_id))


    @app.route("/api/automod/config", methods=["POST"])
    def api_automod_set():
        from database import (has_premium_grant, user_has_active_entitlement,
                              automod_config_set)
        from services.automod import invalidate_automod_cache
        uid = _current_user_id()
        if not uid:
            return jsonify({"error": "not_logged_in"}), 401
        # Gate TookBot+
        is_plus = (
            has_premium_grant(uid, feature="tookbot_plus", inherit_all=False)
            or (globals().get("SKU_TOOKBOT_PLUS") and user_has_active_entitlement(uid, sku_id=globals().get("SKU_TOOKBOT_PLUS")))
        )
        if not is_plus:
            return jsonify({"error": t("api.admin.plus_required")}), 402
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        # Normalisation des champs (bool -> 0/1, int clamp)
        fields = {}
        for bk in ("enabled", "banned_words_enabled", "discord_invites_enabled",
                   "mention_spam_enabled", "raid_protection_enabled"):
            if bk in data:
                fields[bk] = 1 if str(data[bk]) in ("1", "true", "True", "on") else 0
        if "banned_words" in data:
            fields["banned_words"] = str(data["banned_words"] or "")[:2000]
        if "mention_spam_threshold" in data:
            try:
                fields["mention_spam_threshold"] = max(2, min(50, int(data["mention_spam_threshold"])))
            except (TypeError, ValueError): pass
        if "raid_threshold" in data:
            try:
                fields["raid_threshold"] = max(2, min(50, int(data["raid_threshold"])))
            except (TypeError, ValueError): pass
        if "log_channel_id" in data:
            v = str(data["log_channel_id"] or "").strip()
            fields["log_channel_id"] = v or None
        automod_config_set(g_id, **fields)
        invalidate_automod_cache(g_id)
        return jsonify({"ok": True, "updated": list(fields.keys())})


    @app.route("/api/analytics/overview")
    def api_analytics_overview():
        from database import get_guild_analytics_overview
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        return jsonify(get_guild_analytics_overview(g_id))


    @app.route("/api/analytics/msg-per-day")
    def api_analytics_msg_per_day():
        from database import get_msg_per_day
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        try:
            days = max(7, min(int(request.args.get("days", 30)), 90))
        except ValueError:
            days = 30
        return jsonify({"series": get_msg_per_day(g_id, days=days)})


    @app.route("/api/analytics/member-growth")
    def api_analytics_member_growth():
        from database import get_member_growth
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        try:
            days = max(7, min(int(request.args.get("days", 30)), 90))
        except ValueError:
            days = 30
        return jsonify({"series": get_member_growth(g_id, days=days)})


    @app.route("/api/analytics/heatmap")
    def api_analytics_heatmap():
        from database import get_activity_heatmap
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        try:
            weeks = max(1, min(int(request.args.get("weeks", 4)), 12))
        except ValueError:
            weeks = 4
        return jsonify({"matrix": get_activity_heatmap(g_id, weeks=weeks)})


    @app.route("/api/analytics/top-commands")
    def api_analytics_top_commands():
        from database import get_top_commands
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        try:
            days = max(1, min(int(request.args.get("days", 30)), 90))
        except ValueError:
            days = 30
        try:
            limit = max(1, min(int(request.args.get("limit", 10)), 25))
        except ValueError:
            limit = 10
        return jsonify({"rows": get_top_commands(g_id, days=days, limit=limit)})


    @app.route("/api/analytics/top-users")
    def api_analytics_top_users():
        from database import get_top_active_users
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        try:
            days = max(1, min(int(request.args.get("days", 30)), 90))
        except ValueError:
            days = 30
        try:
            limit = max(1, min(int(request.args.get("limit", 10)), 25))
        except ValueError:
            limit = 10
        return jsonify({"rows": get_top_active_users(g_id, days=days, limit=limit)})

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

    # Note: the "Direct messages" feature (reading/sending DMs from the dashboard
    # + storing them in DB) was removed on purpose (privacy reasons).
    # User -> bot DMs are no longer recorded. See privacy.html section 1.3.

    @app.route("/api/bottalk/send", methods=["POST"])
    def api_bottalk_send():
        g_id = gid()
        data = request.json or {}
        channel_id = (data.get("channel_id") or "").strip()
        content    = (data.get("content") or "").strip()
        embed      = data.get("embed")
        if not channel_id:
            return jsonify({"error": t("api.admin.channel_id_required")}), 400
        if not content and not embed:
            return jsonify({"error": t("api.admin.content_or_embed_required")}), 400
        if len(content) > 2000:
            return jsonify({"error": t("api.admin.content_too_long")}), 400
        payload = {"channel_id": channel_id, "content": content}
        if embed and isinstance(embed, dict):
            # Sanitize: only forward the expected keys
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
        from database import GUILD_DEFAULT_SETTINGS, guild_card_config_get
        g_id = gid()
        card_cfg = guild_card_config_get(g_id) or {}
        return render_template("settings.html",
                               active_nav="settings",
                               settings=guild_settings_all(g_id),
                               defaults=GUILD_DEFAULT_SETTINGS,
                               card_channel_id=str(card_cfg.get("channel_id") or ""))

    @app.route("/api/guild-cards-channel", methods=["POST"])
    def api_guild_cards_channel():
        from database import guild_card_config_set
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        cid = (data.get("channel_id") or "").strip()
        # vide = aucune restriction (commandes cartes autorisees partout)
        guild_card_config_set(g_id, channel_id=(cid or None), enabled=True)
        return jsonify({"success": True, "channel_id": cid or None})

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
        BOOL_KEYS = {"xp_enabled", "music", "giveaway", "fun", "moderation_cmds",
                     "tickets", "welcome", "rolereaction", "reactions", "social_alerts",
                     "custom_commands", "poll", "cs2", "lol", "duels",
                     "presentation_enabled"}
        STR_KEYS  = {"xp_min", "xp_max", "xp_cooldown_seconds", "xp_curve_exponent",
                     "welcome_template", "presentation_channel_id"}
        updated = []
        for k, v in data.items():
            if k in BOOL_KEYS:
                guild_setting_set(g_id, k, "1" if str(v) in ("1", "true", "True", "on") else "0")
                updated.append(k)
            elif k in STR_KEYS:
                guild_setting_set(g_id, k, str(v))
                updated.append(k)
        return jsonify({"success": True, "updated": updated})


    @app.route("/api/heatmap/cell")
    def api_heatmap_cell():
        from database import get_heatmap_cell_detail
        try:
            dow   = max(0, min(6, int(request.args.get("dow", 0))))
            hour  = max(0, min(23, int(request.args.get("hour", 0))))
            weeks = max(1, min(12, int(request.args.get("weeks", 4))))
        except ValueError:
            return jsonify({"error": "bad_params"}), 400
        scope = request.args.get("scope", "guild")
        g_id = None if scope == "global" else gid()
        data = get_heatmap_cell_detail(g_id, dow=dow, hour=hour, weeks=weeks, limit=10)
        return jsonify(data)


    @app.route("/poll-builder")
    def poll_builder_page():
        return render_template("poll_builder.html")

    @app.route("/api/poll/create", methods=["POST"])
    def api_poll_create():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        channel_id = (data.get("channel_id") or "").strip()
        question   = (data.get("question") or "").strip()
        options    = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()]
        duration_h = data.get("duration_hours")
        if not channel_id:
            return jsonify({"error": t("api.admin.channel_id_required")}), 400
        if not question or len(question) > 300:
            return jsonify({"error": t("api.admin.question_required")}), 400
        if len(options) < 2 or len(options) > 10:
            return jsonify({"error": "2 a 10 options"}), 400
        if any(len(o) > 55 for o in options):
            return jsonify({"error": t("api.admin.option_too_long")}), 400
        try:
            duration_h = max(1, min(168, int(duration_h or 24)))
        except (TypeError, ValueError):
            duration_h = 24
        cid = bot_command_enqueue(g_id, "poll_send", {
            "channel_id":     channel_id,
            "question":       question,
            "options":        options,
            "duration_hours": duration_h,
        })
        return jsonify({"success": True, "command_id": cid})


    @app.route("/features")
    def features_page():
        from services.feature_guard import FEATURE_REGISTRY
        g_id = gid()
        settings = guild_settings_all(g_id)
        return render_template("features.html",
                               active_nav="features",
                               feature_registry=FEATURE_REGISTRY,
                               settings=settings)


    # ===== Mod permissions config (server owner only) =====
    _MOD_PERMS_REGISTRY = [
        # Slash commands
        ("warn",            "/warn",          t("api.mod_perms.warn"),          "Slash"),
        ("kick",            "/kick",          t("api.mod_perms.kick"),          "Slash"),
        ("ban",             "/ban",           t("api.mod_perms.ban"),           "Slash"),
        ("clear",           "/clear",         t("api.mod_perms.clear"),         "Slash"),
        ("ticket",          "/ticket",        t("api.mod_perms.ticket"),        "Slash"),
        ("giveaway",        "/giveaway",      t("api.mod_perms.giveaway"),      "Slash"),
        ("poll",            "/poll",          t("api.mod_perms.poll"),          "Slash"),
        ("rolereaction",    "/rolereaction",  t("api.mod_perms.rolereaction"),  "Slash"),
        ("socialalert",     "/socialalert",   t("api.mod_perms.socialalert"),   "Slash"),
        ("setwelcome",      "/setwelcome",    t("api.mod_perms.setwelcome"),    "Slash"),
        ("reaction",        "/reaction_*",    t("api.mod_perms.reaction"),      "Slash"),
        ("modlogs",         "/modlogs",       t("api.mod_perms.modlogs"),       "Slash"),
        ("setup",           "/setup",         t("api.mod_perms.setup"),         "Slash"),
        ("xp",              "/xp",            t("api.mod_perms.xp"),            "Slash"),
        ("note",            "/note",          t("api.mod_perms.note"),          "Slash"),
        # Dashboard only
        ("logs",            t("api.mod_perms.label_logs"),
                            t("api.mod_perms.logs"),            "Dashboard"),
        ("custom_commands", t("api.mod_perms.label_custom_commands"),
                            t("api.mod_perms.custom_commands"), "Dashboard"),
        ("music",           t("api.mod_perms.label_music"),
                            t("api.mod_perms.music"),           "Dashboard"),
        ("features",        t("api.mod_perms.label_features"),
                            t("api.mod_perms.features"),        "Dashboard"),
        ("settings",        t("api.mod_perms.label_settings"),
                            t("api.mod_perms.settings"),        "Dashboard"),
    ]


    @app.route("/mod-config")
    def mod_config_page():
        g_id = gid()
        if not g_id:
            return redirect("/select-guild")
        # Seul le server owner ou le bot owner peut voir
        if not (_is_owner_session() or _is_server_owner_of(g_id)):
            return render_template("forbidden.html"), 403
        settings = guild_settings_all(g_id)
        # Liste des roles de la guild (pour le dropdown mod_role_id)
        from database import list_roles as _list_roles
        roles = [r for r in (_list_roles(g_id) or []) if r.get("name") != "@everyone"]
        return render_template("mod_config.html",
                               active_nav="mod_config",
                               perms_registry=_MOD_PERMS_REGISTRY,
                               settings=settings,
                               roles=roles)


    @app.route("/api/mod-config", methods=["GET"])
    def api_mod_config_get():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        if not (_is_owner_session() or _is_server_owner_of(g_id)):
            return jsonify({"error": "server_owner_only"}), 403
        settings = guild_settings_all(g_id)
        from database import list_roles as _list_roles
        roles = [r for r in (_list_roles(g_id) or []) if r.get("name") != "@everyone"]
        return jsonify({
            "mod_role_id":           settings.get("mod_role_id", ""),
            "mod_access_configured": settings.get("mod_access_configured", "0"),
            "perms": {p[0]: settings.get(f"mod_perm_{p[0]}", "0") == "1"
                      for p in _MOD_PERMS_REGISTRY},
            "roles": roles,
        })


    @app.route("/api/mod-config", methods=["POST"])
    def api_mod_config_set():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        if not (_is_owner_session() or _is_server_owner_of(g_id)):
            return jsonify({"error": "server_owner_only"}), 403
        data = request.json or {}
        # Save role
        if "mod_role_id" in data:
            guild_setting_set(g_id, "mod_role_id", str(data["mod_role_id"]) or "")
        # Save perms
        perms = data.get("perms") or {}
        valid_keys = {p[0] for p in _MOD_PERMS_REGISTRY}
        for k, v in perms.items():
            if k in valid_keys:
                guild_setting_set(g_id, f"mod_perm_{k}",
                                  "1" if str(v) in ("1", "true", "True", "on") else "0")
        # Marquer comme configure
        guild_setting_set(g_id, "mod_access_configured", "1")
        return jsonify({"success": True})

    @app.route("/api/guild-features", methods=["GET"])
    def api_guild_features_get():
        from services.feature_guard import FEATURE_REGISTRY, FEATURE_KEYS
        g_id = gid()
        settings = guild_settings_all(g_id)
        features = []
        for f in FEATURE_REGISTRY:
            default = f.get("default", "1")
            features.append({
                **f,
                "enabled": settings.get(f["key"], default) == "1",
            })
        return jsonify({"features": features})

    @app.route("/api/guild-features", methods=["POST"])
    def api_guild_features_set():
        from services.feature_guard import FEATURE_KEYS
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        key   = (data.get("key") or "").strip()
        value = data.get("value")
        if key not in FEATURE_KEYS:
            return jsonify({"error": t("api.admin.invalid_key")}), 400
        if value is None:
            return jsonify({"error": t("api.admin.value_required")}), 400
        val_str = "1" if str(value) in ("1", "true", "True", "on") else "0"
        # Gate : Cards Events necessite >= 10 membres (sauf serveur support)
        if key == "card_events" and val_str == "1":
            import os as _os
            support_gid = (_os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip()
            if str(g_id) != support_gid:
                from database import get_db
                conn = get_db(); c = conn.cursor()
                row = c.execute("SELECT member_count FROM guilds WHERE guild_id = ?",
                                (str(g_id),)).fetchone()
                conn.close()
                mc = int(row["member_count"]) if row and row["member_count"] else 0
                if mc < 10:
                    return jsonify({"error": t("api.admin.card_events_min_members",
                                               count=mc)}), 400
        guild_setting_set(g_id, key, val_str)
        return jsonify({"success": True, "key": key, "value": val_str})

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
            return jsonify({"error": t("api.admin.user_id_required")}), 400
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
            return jsonify({"error": t("api.admin.action_id_required")}), 400
        a = mod_action_get(action_id)
        if not a or str(a.get("guild_id")) != str(g_id):
            return jsonify({"error": t("api.admin.not_found")}), 404
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
            return jsonify({"error": t("api.admin.invalid_action")}), 400
        g_id = gid()
        data = request.json or {}
        user_id = (data.get("user_id") or "").strip()
        if not user_id:
            return jsonify({"error": t("api.admin.user_id_required")}), 400
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


    # We read the bot state from the DB (separate process). The bot persists its state
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

        # Bot state via shared file
        bot_state = _read_bot_state() or {}
        now = time.time()

        # Recent login attempts
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
            "music":  music_engine_diagnostics(),
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
        """Discord snowflake of the logged-in user (str), or None."""
        if not session.get("logged_in"):
            return None
        return (session.get("discord") or {}).get("user_id")


    def _is_admin_of_current_guild() -> bool:
        """True when the user is the owner OR an admin of the currently selected server."""
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
        """Pass granted: owner OR manual grant feature='pass' OR subscription entitlement."""
        if not uid:
            return False
        if DISCORD_OWNER_ID and str(uid) == str(DISCORD_OWNER_ID):
            return True
        return user_has_active_pass(uid, sku_pass_id=SKU_PASS)


    def _is_premium(uid, feature="all") -> bool:
        """Unified wrapper: Discord entitlement OR manual grant OR owner ENV.

        When feature='all', 'pass' is accepted too (Pass subscribers automatically
        get the Premium /niveau pack).
        """
        if _db_user_is_premium(uid, feature=feature, owner_id=DISCORD_OWNER_ID):
            return True
        if feature == "all" and _has_pass(uid):
            return True
        return False


    def _require_premium_user():
        """Return the user_id when the user is logged in AND premium, else None."""
        uid = _current_user_id()
        if not uid:
            return None
        if not _is_premium(uid):
            return None
        return uid
