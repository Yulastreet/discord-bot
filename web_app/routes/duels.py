from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

def register_duel_routes(app, deps):
    globals().update(deps)

    def _owner_guard_json():
        """Retourne une reponse 403 si l'user n'est PAS le bot-owner, sinon None.
        Le duel admin (editer profils/sabres de tout le monde) = bot-owner UNIQUEMENT."""
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
            return jsonify({"error": "Profil duel introuvable"}), 404
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
            return jsonify({"error": "Aucune mise à jour"}), 400
        return jsonify({"success": True})

    @app.route("/api/duels/user/<user_id>/sabres/add", methods=["POST"])
    def api_duels_user_sabre_add(user_id):
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        sabre_id = data.get("sabre_id")
        if not sabre_id or not db_get_sabre(sabre_id):
            return jsonify({"error": "Sabre inconnu"}), 400
        db_ajouter_sabre_collection(user_id, sabre_id)
        return jsonify({"success": True})

    @app.route("/api/duels/user/<user_id>/sabres/remove", methods=["POST"])
    def api_duels_user_sabre_remove(user_id):
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        sabre_id = data.get("sabre_id")
        if not sabre_id:
            return jsonify({"error": "sabre_id requis"}), 400
        admin_supprimer_sabre_collection(user_id, sabre_id)
        return jsonify({"success": True})

    @app.route("/api/sabres")
    def api_sabres_list():
        """Liste publique des sabres : exclut les sabres saisonniers du Battle Pass
        (qui ont leur propre endpoint owner /api/owner/seasonal-sabres)."""
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
            return jsonify({"error": "id, nom, rarete requis"}), 400
        if data["rarete"] not in RARETES:
            return jsonify({"error": "rareté invalide"}), 400
        ok = db_create_sabre(data)
        if not ok:
            return jsonify({"error": "Un sabre avec cet ID existe déjà"}), 400
        return jsonify({"success": True})

    @app.route("/api/sabres/<sabre_id>/update", methods=["POST"])
    def api_sabres_update(sabre_id):
        g = _owner_guard_json()
        if g: return g
        data = request.json or {}
        if "rarete" in data and data["rarete"] not in RARETES:
            return jsonify({"error": "rareté invalide"}), 400
        ok = db_update_sabre(sabre_id, data)
        if not ok:
            return jsonify({"error": "Aucune mise à jour"}), 400
        return jsonify({"success": True})

    @app.route("/api/sabres/<sabre_id>/delete", methods=["POST"])
    def api_sabres_delete(sabre_id):
        g = _owner_guard_json()
        if g: return g
        if sabre_id == "bleu":
            return jsonify({"error": "Le sabre 'bleu' ne peut pas être supprimé (sabre par défaut)"}), 400
        ok = db_delete_sabre(sabre_id)
        if not ok:
            return jsonify({"error": "Sabre introuvable"}), 404
        return jsonify({"success": True})
