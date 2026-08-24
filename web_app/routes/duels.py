from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

from services.i18n import t

def register_duel_routes(app, deps):
    globals().update(deps)

    def _owner_guard_json():
        """Return a 403 response if the user is NOT the bot owner, otherwise None.
        Duel admin (editing everyone's profiles/sabers) = bot owner ONLY."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return None

    @app.route("/duels")
    def duels_page():
        if not _is_owner_session():
            abort(403)
        return render_template("duels.html", raretes=RARETES)

    @app.route("/api/duels/users")
    def api_duels_users():
        g = _owner_guard_json()
        if g: return g
        q = request.args.get("q", "").strip().lower()
        rows = admin_lister_duel_users()
        if q:
            rows = [r for r in rows if q in (r.get("username") or "").lower() or q in str(r.get("user_id"))]
        return jsonify({"users": rows})

    @app.route("/api/duels/user/<user_id>")
    def api_duels_user(user_id):
        g = _owner_guard_json()
        if g: return g
        data = admin_get_full_duel_user(user_id)
        if not data:
            return jsonify({"error": t("api.duels.profile_not_found")}), 404
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
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        ok = admin_update_duel_profil(user_id, data)
        if not ok:
            return jsonify({"error": t("api.duels.nothing_updated")}), 400
        return jsonify({"success": True})

    @app.route("/api/duels/user/<user_id>/sabres/add", methods=["POST"])
    def api_duels_user_sabre_add(user_id):
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        sabre_id = data.get("sabre_id")
        if not sabre_id or not db_get_sabre(sabre_id):
            return jsonify({"error": t("api.duels.unknown_saber")}), 400
        db_ajouter_sabre_collection(user_id, sabre_id)
        return jsonify({"success": True})

    @app.route("/api/duels/user/<user_id>/sabres/remove", methods=["POST"])
    def api_duels_user_sabre_remove(user_id):
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        sabre_id = data.get("sabre_id")
        if not sabre_id:
            return jsonify({"error": t("api.duels.saber_id_required")}), 400
        admin_supprimer_sabre_collection(user_id, sabre_id)
        return jsonify({"success": True})

    @app.route("/api/sabres")
    def api_sabres_list():
        """Public saber list: excludes the Battle Pass seasonal sabers
        (they have their own owner endpoint /api/owner/seasonal-sabres)."""
        g = _owner_guard_json()
        if g: return g
        sabres = db_get_tous_sabres()
        filtered = [s for s in sabres.values() if not s["id"].startswith("season_")]
        return jsonify({"sabres": filtered, "raretes": RARETES})

    @app.route("/api/sabres/create", methods=["POST"])
    def api_sabres_create():
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        if not data.get("id") or not data.get("nom") or not data.get("rarete"):
            return jsonify({"error": t("api.duels.saber_fields_required")}), 400
        if data["rarete"] not in RARETES:
            return jsonify({"error": t("api.duels.invalid_rarity")}), 400
        ok = db_create_sabre(data)
        if not ok:
            return jsonify({"error": t("api.duels.saber_id_taken")}), 400
        return jsonify({"success": True})

    @app.route("/api/sabres/<sabre_id>/update", methods=["POST"])
    def api_sabres_update(sabre_id):
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        if "rarete" in data and data["rarete"] not in RARETES:
            return jsonify({"error": t("api.duels.invalid_rarity")}), 400
        ok = db_update_sabre(sabre_id, data)
        if not ok:
            return jsonify({"error": t("api.duels.nothing_updated")}), 400
        return jsonify({"success": True})

    @app.route("/api/sabres/<sabre_id>/delete", methods=["POST"])
    def api_sabres_delete(sabre_id):
        g = _owner_guard_json()
        if g: return g
        if sabre_id == "bleu":
            return jsonify({"error": t("api.duels.default_saber_locked")}), 400
        ok = db_delete_sabre(sabre_id)
        if not ok:
            return jsonify({"error": t("api.duels.saber_not_found")}), 404
        return jsonify({"success": True})
