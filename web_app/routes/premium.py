from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file
from services.owner_settings_utils import update_seasonal_sabre_name

def register_premium_routes(app, deps):
    globals().update(deps)
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


    # ===== GUILD BOOST + : assignation par user =====

    def _user_guilds_admin_or_owner():
        """Liste des guilds connues du bot ou le user connecte est admin OU owner."""
        metas = (session.get("discord") or {}).get("guilds_meta") or []
        out = []
        seen = set()
        for m in metas:
            if m.get("is_admin") or m.get("is_owner"):
                gid = str(m.get("guild_id"))
                if gid and gid not in seen:
                    seen.add(gid)
                    out.append({
                        "guild_id":  gid,
                        "name":      m.get("name") or gid,
                        "icon_url":  m.get("icon_url"),
                    })
        return out


    def _gb_skus():
        return {
            "solo":  globals().get("SKU_GUILD_BOOST_PLUS"),
            "duo":   globals().get("SKU_GUILD_BOOST_DUO"),
            "squad": globals().get("SKU_GUILD_BOOST_SQUAD"),
        }


    @app.route("/api/guild-boost/guild-status", methods=["GET"])
    def api_guild_boost_guild_status():
        """Statut Guild Boost + pour la guild actuellement selectionnee."""
        g_id = gid()
        if not g_id:
            return jsonify({"ok": False, "active": False, "error": "no_guild"}), 200
        s = _gb_skus()
        active = guild_has_active_boost(
            g_id, sku_solo=s["solo"], sku_duo=s["duo"], sku_squad=s["squad"],
            owner_id=DISCORD_OWNER_ID,
        )
        return jsonify({"ok": True, "guild_id": str(g_id), "active": active})


    @app.route("/api/guild-boost/status", methods=["GET"])
    def api_guild_boost_status():
        uid = _current_user_id()
        if not uid:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        s = _gb_skus()
        owner = DISCORD_OWNER_ID
        is_owner = bool(owner) and str(uid) == str(owner)
        max_slots = user_max_guild_slots(
            uid, sku_solo=s["solo"], sku_duo=s["duo"], sku_squad=s["squad"], owner_id=owner,
        )
        can_assign = max_slots > 0
        assignments = guild_boost_get_for_user(uid)
        used_slots = len(assignments)
        # Detail tiers actifs (pour affichage UI)
        tiers = {
            "solo":  bool(s["solo"]  and user_has_active_entitlement(uid, sku_id=s["solo"]))  or has_premium_grant(uid, feature="guild_boost",       inherit_all=False),
            "duo":   bool(s["duo"]   and user_has_active_entitlement(uid, sku_id=s["duo"]))   or has_premium_grant(uid, feature="guild_boost_duo",   inherit_all=False),
            "squad": bool(s["squad"] and user_has_active_entitlement(uid, sku_id=s["squad"])) or has_premium_grant(uid, feature="guild_boost_squad", inherit_all=False),
        }
        return jsonify({
            "ok": True,
            "user_id":     str(uid),
            "is_owner":    is_owner,
            "can_assign":  can_assign,
            "max_slots":   max_slots,
            "used_slots":  used_slots,
            "tiers":       tiers,
            "assignments": assignments,
            "guilds":      _user_guilds_admin_or_owner(),
        })


    @app.route("/api/guild-boost/assign", methods=["POST"])
    def api_guild_boost_assign():
        uid = _current_user_id()
        if not uid:
            return jsonify({"error": "not_logged_in"}), 401
        s = _gb_skus()
        owner = DISCORD_OWNER_ID
        max_slots = user_max_guild_slots(
            uid, sku_solo=s["solo"], sku_duo=s["duo"], sku_squad=s["squad"], owner_id=owner,
        )
        if max_slots <= 0:
            return jsonify({"error": "pas de Guild Boost + actif sur ce compte"}), 403
        data = request.get_json(silent=True) or {}
        gid_target = str(data.get("guild_id") or "").strip()
        if not gid_target:
            return jsonify({"error": "guild_id requis"}), 400
        eligible = {g["guild_id"] for g in _user_guilds_admin_or_owner()}
        if gid_target not in eligible:
            return jsonify({"error": "tu dois etre admin ou owner du serveur"}), 403
        # Capacite : ne depasse pas max_slots (sauf reassignation a une guild deja boostee)
        current = guild_boost_get_for_user(uid)
        already = any(str(a["guild_id"]) == gid_target for a in current)
        if not already and len(current) >= max_slots:
            return jsonify({
                "error": f"limite atteinte ({len(current)}/{max_slots} serveurs). "
                         f"Achete Duo ou Squad pour plus de slots.",
            }), 403
        guild_boost_assign(uid, gid_target)
        # Notifie le salon admin de la guild qu'un boost vient d'etre active
        if not already:
            try:
                bot_command_enqueue(gid_target, "guild_boost_activated_notify",
                                    {"user_id": str(uid)})
            except Exception as e:
                print(f"[gb-notify enqueue] err: {e}")
        return jsonify({"ok": True, "guild_id": gid_target})


    @app.route("/api/guild-boost/unassign", methods=["POST"])
    def api_guild_boost_unassign():
        uid = _current_user_id()
        if not uid:
            return jsonify({"error": "not_logged_in"}), 401
        data = request.get_json(silent=True) or {}
        gid_target = str(data.get("guild_id") or "").strip() or None
        guild_boost_unassign(uid, gid_target)
        return jsonify({"ok": True, "guild_id": gid_target})


    # ===== Owner : grant/revoke Guild Boost + (3 tiers) manuellement =====

    _GB_TIER_FEATURE = {
        "solo":  "guild_boost",
        "duo":   "guild_boost_duo",
        "squad": "guild_boost_squad",
    }

    @app.route("/api/user/<user_id>/guild-boost", methods=["POST"])
    def api_user_guild_boost_grant(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        data = request.get_json(silent=True) or {}
        tier = (data.get("tier") or "solo").lower()
        if tier not in _GB_TIER_FEATURE:
            return jsonify({"error": "tier invalide (solo|duo|squad)"}), 400
        note = data.get("note") or f"Offert ({tier}) depuis dashboard"
        add_premium_grant(user_id, feature=_GB_TIER_FEATURE[tier],
                          granted_by=_current_user_id(), note=note)
        return jsonify({"ok": True, "user_id": str(user_id), "tier": tier})


    @app.route("/api/user/<user_id>/guild-boost", methods=["DELETE"])
    def api_user_guild_boost_revoke(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        tier = (request.args.get("tier") or "").lower().strip()
        if tier and tier in _GB_TIER_FEATURE:
            # Revoke d'un tier specifique
            remove_premium_grant(user_id, feature=_GB_TIER_FEATURE[tier])
        else:
            # Revoke tous les tiers
            for f in _GB_TIER_FEATURE.values():
                remove_premium_grant(user_id, feature=f)
            guild_boost_unassign(user_id, None)
        return jsonify({"ok": True, "user_id": str(user_id), "tier": tier or "all", "revoked": True})


    @app.route("/api/user/<user_id>/guild-boost", methods=["GET"])
    def api_user_guild_boost_status(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        s = _gb_skus()
        owner = DISCORD_OWNER_ID
        is_owner_target = bool(owner) and str(user_id) == str(owner)
        max_slots = user_max_guild_slots(
            user_id, sku_solo=s["solo"], sku_duo=s["duo"], sku_squad=s["squad"], owner_id=owner,
        )
        # Detail par tier
        def _tier_state(tier_key, sku_key, feat):
            return {
                "has_grant":       has_premium_grant(user_id, feature=feat, inherit_all=False),
                "has_entitlement": bool(s[sku_key]) and user_has_active_entitlement(user_id, sku_id=s[sku_key]),
            }
        tiers = {
            "solo":  _tier_state("solo",  "solo",  "guild_boost"),
            "duo":   _tier_state("duo",   "duo",   "guild_boost_duo"),
            "squad": _tier_state("squad", "squad", "guild_boost_squad"),
        }
        return jsonify({
            "user_id":     str(user_id),
            "is_owner":    is_owner_target,
            "active":      max_slots > 0,
            "max_slots":   max_slots,
            "tiers":       tiers,
            "assignments": guild_boost_get_for_user(user_id),
        })


    # ===== Owner : page de paramètres avancée (custom BG, etc.) =====

    # ===== Owner : Analytics (visites + tokens IA) =====
    @app.route("/owner/analytics")
    def owner_analytics_page():
        if not _is_owner_session():
            abort(403)
        from database import visits_stats, ai_usage_stats, pageview_stats, donations_stats
        return render_template(
            "owner_analytics.html",
            active_nav="owner_analytics",
            landing=visits_stats("landing"),
            dashboard=visits_stats("dashboard"),
            eng_landing=pageview_stats("landing"),
            eng_dashboard=pageview_stats("dashboard"),
            ai=ai_usage_stats(),
            donations=donations_stats(),
        )


    @app.route("/api/owner/analytics", methods=["GET"])
    def api_owner_analytics():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        from database import visits_stats, ai_usage_stats, pageview_stats, donations_stats
        return jsonify({
            "landing":   visits_stats("landing"),
            "dashboard": visits_stats("dashboard"),
            "eng_landing":   pageview_stats("landing"),
            "eng_dashboard": pageview_stats("dashboard"),
            "ai":        ai_usage_stats(),
            "donations": donations_stats(),
        })


    @app.route("/api/owner/donations/<int:donation_id>", methods=["DELETE"])
    def api_owner_donation_delete(donation_id):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        from database import donation_delete
        ok = donation_delete(donation_id)
        return jsonify({"ok": ok})


    @app.route("/api/owner/donations", methods=["POST"])
    def api_owner_donation_add():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        from database import donation_add
        import time as _t
        data = request.json or {}
        try:
            amount = float(data.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return jsonify({"ok": False, "error": "bad_amount"}), 400
        # txn_id unique pour un ajout manuel (pas de doublon avec les webhooks)
        txn_id = f"manual-{int(_t.time()*1000)}"
        ok = donation_add(
            txn_id=txn_id,
            kofi_type="Manual",
            donor_name=(data.get("donor_name") or "").strip() or None,
            amount=amount,
            currency=(data.get("currency") or "EUR").strip()[:8],
            message=(data.get("message") or "").strip() or None,
            is_public=1,
            is_subscription=0,
        )
        return jsonify({"ok": ok})


    # ===== Owner : IA Groq config =====
    @app.route("/owner/ai")
    def owner_ai_page():
        if not _is_owner_session():
            abort(403)
        from database import get_all_settings, DEFAULT_SETTINGS, ai_usage_stats
        from services.groq_ai import get_groq_api_key
        s = get_all_settings()
        allowed_csv = s.get("ai_allowed_user_ids", "") or ""
        ids = [x.strip() for x in allowed_csv.split(",") if x.strip()]
        try:
            from services.tts import is_available as _tts_available
            tts_available = _tts_available()
        except Exception:
            tts_available = False
        return render_template(
            "owner_ai.html",
            active_nav="owner_ai",
            ai_enabled=(s.get("ai_enabled") == "1"),
            ai_model=s.get("ai_model", DEFAULT_SETTINGS["ai_model"]),
            ai_system_prompt=s.get("ai_system_prompt", DEFAULT_SETTINGS["ai_system_prompt"]),
            ai_max_tokens=s.get("ai_max_tokens", DEFAULT_SETTINGS["ai_max_tokens"]),
            ai_voice_enabled=(s.get("ai_voice_enabled") == "1"),
            ai_voice_name=s.get("ai_voice_name", DEFAULT_SETTINGS["ai_voice_name"]),
            tts_available=tts_available,
            allowed_ids=ids,
            api_key_present=bool(get_groq_api_key()),
            defaults=DEFAULT_SETTINGS,
            ai_stats=ai_usage_stats(),
        )


    @app.route("/api/owner/ai-settings", methods=["POST"])
    def api_owner_ai_set():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        from database import set_setting
        data = request.json or {}
        if "ai_enabled" in data:
            set_setting("ai_enabled", "1" if str(data["ai_enabled"]) in ("1", "true", "True", "on") else "0")
        if "ai_model" in data:
            set_setting("ai_model", str(data["ai_model"]).strip() or "llama-3.3-70b-versatile")
        if "ai_system_prompt" in data:
            set_setting("ai_system_prompt", str(data["ai_system_prompt"])[:4000])
        if "ai_max_tokens" in data:
            try:
                set_setting("ai_max_tokens", str(max(50, min(2000, int(data["ai_max_tokens"])))))
            except (TypeError, ValueError):
                pass
        if "ai_allowed_user_ids" in data:
            raw = data["ai_allowed_user_ids"]
            if isinstance(raw, list):
                ids = [str(x).strip() for x in raw if str(x).strip().isdigit()]
            else:
                ids = [x.strip() for x in str(raw).split(",") if x.strip().isdigit()]
            set_setting("ai_allowed_user_ids", ",".join(ids))
        if "ai_voice_enabled" in data:
            set_setting("ai_voice_enabled",
                        "1" if str(data["ai_voice_enabled"]) in ("1", "true", "True", "on") else "0")
        if "ai_voice_name" in data:
            # Whitelist voix FR pour eviter abus
            voice = str(data["ai_voice_name"]).strip()
            allowed_voices = {
                "fr-FR-DeniseNeural", "fr-FR-HenriNeural",
                "fr-FR-EloiseNeural", "fr-FR-VivienneMultilingualNeural",
            }
            if voice in allowed_voices:
                set_setting("ai_voice_name", voice)
        return jsonify({"success": True})


    @app.route("/owner/settings")
    def owner_settings_page():
        if not _is_owner_session():
            abort(403)
        uid = _current_user_id()
        has_custom = has_owner_custom_bg(uid) if uid else False
        from database import get_all_settings, DEFAULT_SETTINGS
        log_settings = get_all_settings()
        return render_template(
            "owner_settings.html",
            active_nav="owner_settings",
            has_custom=has_custom,
            owner_id=uid,
            log_settings=log_settings,
            log_defaults=DEFAULT_SETTINGS,
        )

    @app.route("/api/owner/log-settings", methods=["POST"])
    def api_owner_log_settings():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        from database import set_setting
        data = request.json or {}
        updated = []
        try:
            keep = max(100, int(data.get("log_keep_per_guild", 5000)))
            age  = max(7,   int(data.get("log_retention_days", 90)))
        except (TypeError, ValueError):
            return jsonify({"error": "valeurs invalides"}), 400
        set_setting("log_keep_per_guild", str(keep))
        set_setting("log_retention_days", str(age))
        updated = ["log_keep_per_guild", "log_retention_days"]
        return jsonify({"success": True, "updated": updated})


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

    @app.route("/api/owner/seasonal-sabres/<sabre_id>", methods=["POST"])
    def api_owner_seasonal_sabre_update(sabre_id):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        data = request.get_json(silent=True) or {}
        try:
            ok = update_seasonal_sabre_name(db_get_sabre, db_update_sabre, sabre_id, data.get("nom"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except LookupError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify({"ok": bool(ok), "sabre_id": sabre_id, "nom": data.get("nom", "").strip()})


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


    # ===== Owner : codes promo =====

    @app.route("/owner/promo-codes")
    def owner_promo_codes_page():
        if not _is_owner_session():
            abort(403)
        return render_template("owner_promo_codes.html",
                               active_nav="owner_promo_codes")

    @app.route("/api/owner/promo-codes", methods=["GET"])
    def api_owner_promo_codes_list():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        return jsonify({"codes": promo_codes_list()})

    @app.route("/api/owner/promo-codes", methods=["POST"])
    def api_owner_promo_codes_create():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        data = request.json or {}
        code = (data.get("code") or "").strip().upper()
        rtype = (data.get("reward_type") or "").strip()
        try:
            rvalue   = int(data.get("reward_value", 0))
            max_uses = int(data.get("max_uses", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "valeurs numeriques invalides"}), 400
        expires_at = (data.get("expires_at") or "").strip() or None
        note       = (data.get("note") or "").strip() or None

        if not code or len(code) > 32:
            return jsonify({"error": "code requis (max 32 chars)"}), 400
        if rtype not in ("tookcoins", "pass_xp", "premium_grant_days"):
            return jsonify({"error": "reward_type invalide"}), 400
        if rvalue <= 0:
            return jsonify({"error": "reward_value doit etre > 0"}), 400
        if max_uses < 1 or max_uses > 100000:
            return jsonify({"error": "max_uses invalide"}), 400

        try:
            promo_code_create(code, rtype, rvalue, max_uses=max_uses,
                              expires_at=expires_at, note=note)
        except Exception as e:
            return jsonify({"error": f"creation echouee: {e}"}), 400
        return jsonify({"success": True, "code": code})

    @app.route("/api/owner/promo-codes/<code>", methods=["DELETE"])
    def api_owner_promo_codes_delete(code):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        n = promo_code_delete(code)
        return jsonify({"success": True, "deleted": n})


    # ===== Owner : analytics premium =====

    @app.route("/owner/premium-analytics")
    def owner_premium_analytics_page():
        if not _is_owner_session():
            abort(403)
        from database import get_setting, DEFAULT_SETTINGS
        return render_template(
            "owner_premium_analytics.html",
            active_nav="owner_premium_analytics",
            soutien_message=get_setting("soutien_message", DEFAULT_SETTINGS["soutien_message"]),
            soutien_role_ids=get_setting("soutien_role_ids", ""),
            soutien_channel_id=get_setting("soutien_channel_id", ""),
        )

    @app.route("/api/owner/soutien-settings", methods=["POST"])
    def api_owner_soutien_set():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        from database import set_setting
        data = request.json or {}
        if "soutien_message" in data:
            set_setting("soutien_message", str(data["soutien_message"])[:1000])
        if "soutien_role_ids" in data:
            ids = [x.strip() for x in str(data["soutien_role_ids"]).split(",") if x.strip().isdigit()]
            set_setting("soutien_role_ids", ",".join(ids))
        if "soutien_channel_id" in data:
            cid = str(data["soutien_channel_id"]).strip()
            set_setting("soutien_channel_id", cid if cid.isdigit() else "")
        return jsonify({"success": True})

    @app.route("/api/owner/premium-analytics")
    def api_owner_premium_analytics():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        db = get_db()
        # Entitlements actifs (SKU Discord payants)
        ent = db.execute("""SELECT sku_id, COUNT(*) AS n
                            FROM entitlements
                            WHERE (deleted_at IS NULL OR deleted_at = '')
                              AND (ends_at IS NULL OR ends_at = '' OR ends_at >= datetime('now'))
                            GROUP BY sku_id""").fetchall()
        entitlements_by_sku = {r["sku_id"]: r["n"] for r in ent}

        # Grants manuels (codes promo, owner offre)
        grants_total = db.execute(
            "SELECT COUNT(*) AS n FROM premium_grants"
        ).fetchone()["n"]
        grants_by_feature = {r["feature"]: r["n"] for r in db.execute(
            "SELECT feature, COUNT(*) AS n FROM premium_grants GROUP BY feature"
        ).fetchall()}

        # Codes promo : compte + redemptions 30j
        promo_total = db.execute(
            "SELECT COUNT(*) AS n FROM promo_codes"
        ).fetchone()["n"]
        promo_redemptions_30d = db.execute(
            "SELECT COUNT(*) AS n FROM promo_redemptions WHERE ts >= datetime('now', '-30 days')"
        ).fetchone()["n"]
        promo_top = [dict(r) for r in db.execute(
            """SELECT pc.code, pc.reward_type, pc.reward_value, pc.used_count, pc.max_uses
                 FROM promo_codes pc
                 ORDER BY pc.used_count DESC LIMIT 10"""
        ).fetchall()]

        # Pass actifs (entitlements en cours avec sku_pass)
        sku_pass = (globals().get("SKU_PASS") or "")
        pass_active = 0
        if sku_pass:
            pass_active = db.execute(
                """SELECT COUNT(DISTINCT user_id) AS n FROM entitlements
                   WHERE sku_id = ?
                     AND (deleted_at IS NULL OR deleted_at = '')
                     AND (ends_at IS NULL OR ends_at = '' OR ends_at >= datetime('now'))""",
                (sku_pass,)
            ).fetchone()["n"]

        # Total membres uniques avec un acces premium (entitlement OU grant)
        unique_premium = db.execute("""
            SELECT COUNT(*) AS n FROM (
                SELECT user_id FROM entitlements
                WHERE (deleted_at IS NULL OR deleted_at = '')
                  AND (ends_at IS NULL OR ends_at = '' OR ends_at >= datetime('now'))
                UNION
                SELECT user_id FROM premium_grants
            )
        """).fetchone()["n"]

        db.close()
        return jsonify({
            "entitlements_by_sku":   entitlements_by_sku,
            "grants_total":          grants_total,
            "grants_by_feature":     grants_by_feature,
            "promo_total":           promo_total,
            "promo_redemptions_30d": promo_redemptions_30d,
            "promo_top":             promo_top,
            "pass_active":           pass_active,
            "unique_premium":        unique_premium,
            "sku_pass_id":           sku_pass,
        })


    # ===== API publique : stats pour landing tookbot.click =====
    # Cache 1h pour eviter de bombarder la DB depuis la home page.

    import time as _time
    _PUBLIC_STATS_CACHE = {"data": None, "expires": 0.0}
    _PUBLIC_STATS_TTL_SEC = 3600  # 1h
