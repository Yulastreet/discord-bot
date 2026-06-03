from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

def register_pass_routes(app, deps):
    globals().update(deps)
    @app.route("/my-pass")
    def my_pass_page():
        uid = _current_user_id()
        if not uid:
            return redirect(url_for("oauth_login"))
        return render_template("my_pass.html", active_nav="my_pass")


    @app.route("/api/my/pass", methods=["GET"])
    def api_my_pass():
        """Etat complet du Pass pour l'user connecte (lecture seule)."""
        from seasonal_themes import bg_display_name as _bg_disp
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

        # Enrichit chaque unlock avec un display_name themed (sinon le front
        # affiche des IDs techniques type 'season_2026-06_R' ou 'liquid_chrome').
        db = get_db()
        sabre_rows = db.execute("SELECT id, nom FROM sabres").fetchall()
        sabre_names = {r["id"]: r["nom"] for r in sabre_rows}
        for u in unlocks:
            payload = u.get("payload") or {}
            disp = None
            t = u.get("type")
            if t == "bg":
                disp = _bg_disp(payload.get("bg_id") or "")
            elif t == "sabre":
                disp = sabre_names.get(payload.get("sabre_id") or "", payload.get("sabre_id") or "")
            elif t == "title":
                disp = payload.get("title") or ""
            elif t == "emoji":
                disp = payload.get("emoji") or ""
            elif t == "boost_xp":
                disp = f"XP x{payload.get('multiplier')} pendant {payload.get('hours')}h"
            u["display_name"] = disp

        # Roadmap des paliers (rewards definis pour la saison)
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
        is_owner_target = bool(DISCORD_OWNER_ID) and str(user_id) == str(DISCORD_OWNER_ID)
        has_grant = has_premium_grant(user_id, feature="pass", inherit_all=False)
        sku_pass = globals().get("SKU_PASS")
        has_entitlement = bool(sku_pass) and user_has_active_entitlement(user_id, sku_id=sku_pass)
        return jsonify({
            "user_id":         str(user_id),
            "has_pass":        has_pass,
            "is_owner":        is_owner_target,
            "has_grant":       has_grant,
            "has_entitlement": has_entitlement,
            "season":          season,
            "progress":        progress,
            "unlocks":         unlocks,
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
