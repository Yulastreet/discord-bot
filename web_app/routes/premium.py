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


    @app.route("/subscription")
    def subscription_page():
        uid = _current_user_id()
        if not uid:
            return redirect(url_for("oauth_login"))
        # User a TookBot+ ?
        # inherit_all=False : grant "all" (Premium niveau) ne debloque pas TookBot+.
        is_tookbot_plus = (
            has_premium_grant(uid, feature="tookbot_plus", inherit_all=False)
            or (globals().get("SKU_TOOKBOT_PLUS") and user_has_active_entitlement(uid, sku_id=globals().get("SKU_TOOKBOT_PLUS")))
        )
        # Detection trial : grant TookBot+ actif avec note 'trial_*' + expires_at futur
        trial_active = False
        trial_expires_at = None
        if is_tookbot_plus:
            try:
                conn = get_db(); c = conn.cursor()
                row = c.execute(
                    """SELECT expires_at, note FROM premium_grants
                       WHERE user_id = ? AND feature = 'tookbot_plus'
                         AND (expires_at IS NULL OR expires_at > datetime('now'))
                       ORDER BY granted_at DESC LIMIT 1""",
                    (str(uid),),
                ).fetchone()
                conn.close()
                if row and row["note"] and str(row["note"]).startswith("trial") and row["expires_at"]:
                    trial_active = True
                    trial_expires_at = row["expires_at"]
            except Exception:
                pass
        # Eligibilite trial : pas deja TookBot+ + jamais utilise de trial
        settings_p = get_premium_settings(uid) or {}
        trial_used_at = settings_p.get("trial_used_at")
        trial_eligible = (not is_tookbot_plus) and (not trial_used_at)
        return render_template(
            "subscription.html",
            is_tookbot_plus=bool(is_tookbot_plus),
            trial_active=trial_active,
            trial_expires_at=trial_expires_at,
            trial_eligible=trial_eligible,
            user=session.get("discord") or {},
            active_nav="subscription",
        )

    @app.route("/api/subscription/start-trial", methods=["POST"])
    def api_subscription_start_trial():
        """Demarre un trial TookBot+ 7 jours pour l'user connecte (1/lifetime)."""
        from database import start_tookbot_plus_trial
        uid = _current_user_id()
        if not uid:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        result = start_tookbot_plus_trial(uid, days=7)
        if not result["ok"]:
            err = result["error"]
            msg = {
                "trial_already_used": "Tu as deja utilise ton essai gratuit.",
                "already_active":     "Tu as deja TookBot+ actif.",
            }.get(err, "Erreur inconnue.")
            return jsonify({"ok": False, "error": err, "message": msg}), 400
        # Cree une notif cloche dashboard
        try:
            from database import dash_notif_add
            dash_notif_add(
                uid, "system",
                title="Trial TookBot+ active !",
                message=f"Tu as 7 jours pour tester. Expire le {result['expires_at']}.",
                link_url="/subscription",
            )
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "expires_at": result["expires_at"],
            "message": f"TookBot+ active jusqu'au {result['expires_at']} ! Profite des 7 jours.",
        })

    @app.route("/api/subscription/redeem-key", methods=["POST"])
    def api_subscription_redeem_key():
        """Redeem d'une cle d'activation TookBot+ par l'user connecte."""
        uid = _current_user_id()
        if not uid:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        data = request.json or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({"ok": False, "message": "Entre une clé d'activation."}), 400
        disc = session.get("discord") or {}
        ok, reason, expires_at = tookbot_plus_key_redeem(
            code, uid, username=disc.get("username"), avatar=disc.get("avatar"))
        if not ok:
            msg = {
                "code_invalid":     "Clé invalide.",
                "already_redeemed": "Cette clé a déjà été utilisée.",
            }.get(reason, "Impossible d'activer cette clé.")
            return jsonify({"ok": False, "error": reason, "message": msg}), 400
        try:
            from database import dash_notif_add
            dash_notif_add(
                uid, "system",
                title="TookBot+ activé !",
                message=f"Ta clé d'activation est validée. TookBot+ actif jusqu'au {expires_at}.",
                link_url="/subscription",
            )
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "expires_at": expires_at,
            "message": f"TookBot+ activé jusqu'au {expires_at} !",
        })

    @app.route("/api/owner/user/<user_id>/tookbot-plus", methods=["POST"])
    def api_owner_grant_tookbot_plus(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        add_premium_grant(user_id, feature="tookbot_plus", granted_by=_current_user_id(), note="owner grant")
        return jsonify({"ok": True, "granted": True})

    @app.route("/api/owner/user/<user_id>/tookbot-plus", methods=["DELETE"])
    def api_owner_revoke_tookbot_plus(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        remove_premium_grant(user_id, feature="tookbot_plus")
        return jsonify({"ok": True, "revoked": True})

    @app.route("/api/owner/user/<user_id>/tookbot-plus", methods=["GET"])
    def api_owner_check_tookbot_plus(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        has = has_premium_grant(user_id, feature="tookbot_plus", inherit_all=False)
        return jsonify({"user_id": str(user_id), "has_tookbot_plus": bool(has)})

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


    # Guild Boost retire : tous les endpoints renvoient un statut "always active"
    # pour ne pas casser l'UI legacy. Plus aucun gating.
    @app.route("/api/guild-boost/guild-status", methods=["GET"])
    def api_guild_boost_guild_status():
        return jsonify({"ok": True, "active": True, "deprecated": True})

    @app.route("/api/guild-boost/status", methods=["GET"])
    def api_guild_boost_status_disabled():
        return jsonify({
            "ok": True, "can_assign": False, "is_owner": False,
            "max_slots": 0, "used_slots": 0,
            "tiers": {"solo": False, "duo": False, "squad": False},
            "assignments": [], "guilds": [], "deprecated": True,
        })

    @app.route("/api/guild-boost/assign", methods=["POST"])
    @app.route("/api/guild-boost/unassign", methods=["POST"])
    def api_guild_boost_assign_disabled():
        return jsonify({"error": "Guild Boost a ete retire. Toutes les features sont gratuites."}), 410

    @app.route("/api/user/<user_id>/guild-boost", methods=["GET", "POST", "DELETE"])
    def api_user_guild_boost_disabled(user_id):
        return jsonify({"error": "Guild Boost a ete retire."}), 410

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
            from services.tts import is_available as _tts_available, elevenlabs_available
            tts_available = _tts_available()
            el_available = elevenlabs_available()
        except Exception:
            tts_available = False
            el_available = False
        return render_template(
            "owner_ai.html",
            active_nav="owner_ai",
            ai_enabled=(s.get("ai_enabled") == "1"),
            ai_model=s.get("ai_model", DEFAULT_SETTINGS["ai_model"]),
            ai_system_prompt=s.get("ai_system_prompt", DEFAULT_SETTINGS["ai_system_prompt"]),
            ai_max_tokens=s.get("ai_max_tokens", DEFAULT_SETTINGS["ai_max_tokens"]),
            ai_voice_enabled=(s.get("ai_voice_enabled") == "1"),
            ai_voice_name=s.get("ai_voice_name", DEFAULT_SETTINGS["ai_voice_name"]),
            ai_voice_provider=s.get("ai_voice_provider", DEFAULT_SETTINGS["ai_voice_provider"]),
            ai_elevenlabs_voice_id=s.get("ai_elevenlabs_voice_id",
                                          DEFAULT_SETTINGS["ai_elevenlabs_voice_id"]),
            ai_elevenlabs_model=s.get("ai_elevenlabs_model",
                                       DEFAULT_SETTINGS["ai_elevenlabs_model"]),
            tts_available=tts_available,
            elevenlabs_key_present=el_available,
            allowed_ids=ids,
            api_key_present=bool(get_groq_api_key()),
            defaults=DEFAULT_SETTINGS,
            ai_stats=ai_usage_stats(),
        )


    @app.route("/api/owner/elevenlabs-quota")
    def api_owner_elevenlabs_quota():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        try:
            from services.tts import get_elevenlabs_quota
            import asyncio as _asyncio
            quota = _asyncio.run(get_elevenlabs_quota())
        except Exception:
            quota = None
        return jsonify({"quota": quota})


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
            # Whitelist voix FR Edge pour eviter abus
            voice = str(data["ai_voice_name"]).strip()
            allowed_voices = {
                "fr-FR-DeniseNeural", "fr-FR-HenriNeural",
                "fr-FR-EloiseNeural", "fr-FR-VivienneMultilingualNeural",
            }
            if voice in allowed_voices:
                set_setting("ai_voice_name", voice)
        if "ai_voice_provider" in data:
            prov = str(data["ai_voice_provider"]).strip().lower()
            if prov in ("edge", "elevenlabs"):
                set_setting("ai_voice_provider", prov)
        if "ai_elevenlabs_voice_id" in data:
            vid = str(data["ai_elevenlabs_voice_id"]).strip()
            # voice IDs ElevenLabs : 20 chars alphanum standard, mais certains
            # IDs Voice Library / cloned peuvent etre + longs. On accepte 15-40.
            import re as _re
            if _re.match(r"^[A-Za-z0-9_-]{15,40}$", vid):
                set_setting("ai_elevenlabs_voice_id", vid)
        if "ai_elevenlabs_model" in data:
            model = str(data["ai_elevenlabs_model"]).strip()
            allowed_models = {
                "eleven_multilingual_v2", "eleven_turbo_v2_5",
                "eleven_flash_v2_5", "eleven_monolingual_v1",
            }
            if model in allowed_models:
                set_setting("ai_elevenlabs_model", model)
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
            print(f"[premium bg] decode err: {type(e).__name__}: {e}")
            return jsonify({"error": "Image illisible. Utilise un PNG ou JPG valide."}), 400

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


    # ===== Admin : edit XP/niveau d'un membre (refonte clean juin 2026) =====
    @app.route("/api/user/<user_id>/xp", methods=["POST"])
    def api_user_xp_set(user_id):
        """Modifie XP (ou niveau) d'un membre sur le serveur courant.

        Accepte JSON {xp: int} OU {level: int}. Si level fourni, calcule l'XP
        minimum pour ce niveau via xp_for_level() (formule canonique level^5).
        Utilise set_xp() de database.py qui UPSERT et recalcule level.
        Cree le row si l'user n'a pas encore d'XP sur ce serveur.
        """
        if not _is_admin_of_current_guild():
            return jsonify({"error": "admin_only"}), 403
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild_selected"}), 400

        data = request.get_json(silent=True) or {}
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
            new_xp = xp_for_level(target_level, g_id)
        else:
            return jsonify({"error": "missing_xp_or_level"}), 400

        # Recupere username existant si row deja la, sinon None (set_xp accepte)
        db = get_db()
        row = db.execute(
            "SELECT username FROM users WHERE guild_id = ? AND user_id = ?",
            (g_id, str(user_id)),
        ).fetchone()
        db.close()
        username = row["username"] if row else None

        set_xp(g_id, user_id, new_xp, username=username)
        new_level = get_level(new_xp, g_id)
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

    _PROMO_TYPES = ("tookcoins", "pass_xp", "premium_grant_days",
                    "roll", "epic_roll", "golden_roll")

    def _gen_promo_code(length=8):
        import secrets
        charset = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sans 0/O/1/I ambigus
        return "".join(secrets.choice(charset) for _ in range(length))

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
            count    = int(data.get("count", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "valeurs numeriques invalides"}), 400
        expires_at = (data.get("expires_at") or "").strip() or None
        note       = (data.get("note") or "").strip() or None

        if rtype not in _PROMO_TYPES:
            return jsonify({"error": "reward_type invalide"}), 400
        if rvalue <= 0:
            return jsonify({"error": "reward_value doit etre > 0"}), 400
        if max_uses < 1 or max_uses > 100000:
            return jsonify({"error": "max_uses invalide"}), 400
        if count < 1 or count > 500:
            return jsonify({"error": "count invalide (1-500)"}), 400

        # 1 seul code : utilise le code fourni (ou auto). Plusieurs : genere des
        # codes aleatoires, le champ 'code' sert alors de prefixe optionnel.
        created = []
        if count == 1:
            final = code or _gen_promo_code()
            if len(final) > 32:
                return jsonify({"error": "code requis (max 32 chars)"}), 400
            try:
                promo_code_create(final, rtype, rvalue, max_uses=max_uses,
                                  expires_at=expires_at, note=note)
            except Exception as e:
                return jsonify({"error": f"creation echouee: {e}"}), 400
            created.append(final)
        else:
            prefix = (code + "-") if code else ""
            for _ in range(count):
                for _try in range(6):
                    cand = (prefix + _gen_promo_code())[:32]
                    if promo_code_get(cand):
                        continue
                    try:
                        promo_code_create(cand, rtype, rvalue, max_uses=max_uses,
                                          expires_at=expires_at, note=note)
                        created.append(cand)
                        break
                    except Exception:
                        continue
        return jsonify({"success": True, "codes": created,
                        "code": created[0] if created else None,
                        "count": len(created)})

    @app.route("/api/owner/promo-codes/<code>", methods=["DELETE"])
    def api_owner_promo_codes_delete(code):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        n = promo_code_delete(code)
        return jsonify({"success": True, "deleted": n})


    # ===== Owner : cles d'activation TookBot+ =====

    def _gen_plus_key():
        import secrets
        charset = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sans 0/O/1/I ambigus
        grp = lambda n: "".join(secrets.choice(charset) for _ in range(n))
        return f"TBP-{grp(5)}-{grp(5)}-{grp(5)}"

    @app.route("/api/owner/tookbot-plus-keys", methods=["GET"])
    def api_owner_plus_keys_list():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        return jsonify({"keys": tookbot_plus_keys_list()})

    @app.route("/api/owner/tookbot-plus-keys", methods=["POST"])
    def api_owner_plus_keys_create():
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        data = request.json or {}
        try:
            duration_days = int(data.get("duration_days", 0))
            count = int(data.get("count", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "valeurs numeriques invalides"}), 400
        note = (data.get("note") or "").strip() or None
        if duration_days < 1 or duration_days > 3650:
            return jsonify({"error": "duree invalide (1-3650 jours)"}), 400
        if count < 1 or count > 500:
            return jsonify({"error": "count invalide (1-500)"}), 400
        by = _current_user_id()
        created = []
        for _ in range(count):
            for _try in range(6):
                cand = _gen_plus_key()
                if tookbot_plus_key_get(cand):
                    continue
                try:
                    tookbot_plus_key_create(cand, duration_days, created_by=by, note=note)
                    created.append(cand)
                    break
                except Exception:
                    continue
        return jsonify({"success": True, "keys": created, "count": len(created)})

    @app.route("/api/owner/tookbot-plus-keys/<code>", methods=["DELETE"])
    def api_owner_plus_keys_delete(code):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        n = tookbot_plus_key_delete(code)
        return jsonify({"success": True, "deleted": n})

    @app.route("/api/owner/tookbot-plus-keys/<code>/deactivate", methods=["POST"])
    def api_owner_plus_keys_deactivate(code):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        ok, uid = tookbot_plus_key_deactivate(code)
        if not ok:
            return jsonify({"success": False, "error": "cle non utilisee"}), 400
        return jsonify({"success": True, "revoked_user": uid})


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
