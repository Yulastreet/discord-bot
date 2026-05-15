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


    # ===== API publique : stats pour landing tookbot.click =====
    # Cache 1h pour eviter de bombarder la DB depuis la home page.

    import time as _time
    _PUBLIC_STATS_CACHE = {"data": None, "expires": 0.0}
    _PUBLIC_STATS_TTL_SEC = 3600  # 1h
