"""Routes owner-only pour gerer le catalogue de cartes."""
from flask import render_template, request, jsonify


def register_cards_owner_routes(app, deps):
    globals().update(deps)

    # ===== PUBLIC =====
    @app.route("/cards")
    def public_cards_page():
        """Page publique : tout le monde peut voir le catalogue (read-only)."""
        import os as _os
        from flask import session as _ses
        dsc = _ses.get("discord") or {}
        uid = str(dsc.get("user_id") or "")
        owner_id = (_os.getenv("DISCORD_OWNER_ID") or "").strip()
        fast_edit_allowed = uid in {owner_id, "235079585509801984"} and bool(uid)
        return render_template("cards_public.html", active_nav="public_cards",
                                 fast_edit_allowed=fast_edit_allowed)


    @app.route("/api/public/cards", methods=["GET"])
    def api_public_cards_list():
        from database import card_list_all, card_count_filtered, card_count_total
        rarity = request.args.get("rarity") or None
        universe = request.args.get("universe") or None
        search = request.args.get("q") or None
        sort = (request.args.get("sort") or "name_asc").strip()
        try:
            per_page = max(1, min(int(request.args.get("per_page", 50)), 200))
        except ValueError:
            per_page = 50
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        offset = (page - 1) * per_page

        # Reuse helper avec extra filter universe (custom query si dispo)
        from database import get_db
        conn = get_db(); c = conn.cursor()
        # Public exclut tjrs not_obtainable
        where = ["COALESCE(not_obtainable, 0) = 0"]
        params = []
        if rarity:
            where.append("rarity = ?"); params.append(rarity)
        if universe:
            where.append("universe = ?"); params.append(universe)
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
            like = f"%{search.lower()}%"
            params += [like, like, like]
        sort_sql = {
            "name_asc":     "name ASC",
            "name_desc":    "name DESC",
            "rarity_desc":  "CASE rarity WHEN 'secret' THEN 0 WHEN 'mythic' THEN 1 "
                             "WHEN 'legendary' THEN 2 WHEN 'epic' THEN 3 WHEN 'rare' THEN 4 "
                             "WHEN 'common' THEN 5 ELSE 6 END ASC, name ASC",
            "rarity_asc":   "CASE rarity WHEN 'common' THEN 0 WHEN 'rare' THEN 1 "
                             "WHEN 'epic' THEN 2 WHEN 'legendary' THEN 3 WHEN 'mythic' THEN 4 "
                             "WHEN 'secret' THEN 5 ELSE 6 END ASC, name ASC",
            "universe_asc": "universe ASC, name ASC",
            "subtitle_asc": "subtitle ASC, name ASC",
            "newest":       "id DESC",
            "oldest":       "id ASC",
        }.get(sort, "name ASC")

        # count filtered
        count_sql = f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}"
        filtered = c.execute(count_sql, params).fetchone()["n"]
        # items (public expose aussi source_image_url pour recadrage proposition)
        items_params = params + [per_page, offset]
        rows = c.execute(
            f"SELECT id, name, universe, subtitle, rarity, image_url, source_image_url "
            f"FROM cards WHERE {' AND '.join(where)} "
            f"ORDER BY {sort_sql} LIMIT ? OFFSET ?", items_params).fetchall()
        # source_image_url public uniquement sur cette page (pour cropper),
        # autres usages publics restent image_url uniquement
        items = [dict(r) for r in rows]
        total = c.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
        conn.close()
        return jsonify({
            "items": items,
            "total": int(total),
            "filtered": int(filtered),
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (int(filtered) + per_page - 1) // per_page),
        })


    @app.route("/api/public/cards/<int:cid>", methods=["GET"])
    def api_public_cards_detail(cid):
        from database import card_get
        card = card_get(cid)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        return jsonify({"card": card})


    @app.route("/api/public/cards/universes", methods=["GET"])
    def api_public_cards_universes():
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT universe, COUNT(*) AS n FROM cards "
            "WHERE universe IS NOT NULL AND universe != '' "
            "GROUP BY universe ORDER BY n DESC").fetchall()
        conn.close()
        return jsonify({"items": [{"universe": r["universe"], "count": r["n"]} for r in rows]})


    @app.route("/api/public/cards/contributors", methods=["GET"])
    def api_public_cards_contributors():
        """Top contributeurs : nb de suggestions approuvees par personne."""
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT suggester_id, "
            "  MAX(suggester_name) AS name, COUNT(*) AS n "
            "FROM card_suggestions WHERE status = 'approved' "
            "GROUP BY suggester_id ORDER BY n DESC LIMIT 30").fetchall()
        conn.close()
        return jsonify({"items": [
            {"name": (r["name"] or "Anonyme"), "count": int(r["n"])} for r in rows]})


    @app.route("/api/public/cards/my-collection", methods=["GET"])
    def api_public_cards_my_collection():
        """Card_ids possedes par l'utilisateur connecte (pour la vue collection)."""
        from flask import session as _ses
        from database import get_db
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        if not uid:
            return jsonify({"error": "non connecte"}), 401
        conn = get_db(); c = conn.cursor()
        rows = c.execute("SELECT DISTINCT card_id FROM user_cards WHERE user_id = ?",
                         (str(uid),)).fetchall()
        conn.close()
        return jsonify({"card_ids": [int(r["card_id"]) for r in rows]})


    @app.route("/api/public/cards/<int:cid>/suggest-edit", methods=["POST"])
    def api_public_cards_suggest_edit(cid):
        """User logge propose modif d'une carte existante. Owner valide.
        Accepte JSON (no image) ou multipart (avec 'cropped' file PNG)."""
        from flask import session as _ses
        from database import card_suggestion_add, card_get, get_db
        import os as _os
        from services.cards_overlay import _OUTPUT_DIR as _RENDERS_DIR
        from PIL import Image as _Img
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        uname = dsc.get("username") or dsc.get("global_name")
        if not uid:
            return jsonify({"error": "login Discord requis pour proposer une modif"}), 401
        card = card_get(cid)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404

        # Multipart si fichier present, sinon JSON
        is_multipart = "cropped" in request.files
        if is_multipart:
            new_name = (request.form.get("name") or "").strip()[:100]
            new_universe = (request.form.get("universe") or "").strip()[:60]
            new_subtitle = (request.form.get("subtitle") or "").strip()[:80]
            new_rarity = (request.form.get("rarity") or "").strip().lower()
        else:
            data = request.json or {}
            new_name = (data.get("name") or "").strip()[:100]
            new_universe = (data.get("universe") or "").strip()[:60]
            new_subtitle = (data.get("subtitle") or "").strip()[:80]
            new_image_url_json = (data.get("image_url") or "").strip()
            new_rarity = (data.get("rarity") or "").strip().lower()
        if new_rarity and new_rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            new_rarity = None
        if not new_name:
            return jsonify({"error": "nom requis"}), 400

        # Resolve image proposee
        final_image_url = None
        sugg_dir = _os.path.join(_os.path.dirname(_RENDERS_DIR), "card_suggestions")
        _os.makedirs(sugg_dir, exist_ok=True)
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        if is_multipart:
            f = request.files["cropped"]
            try:
                cropped = _Img.open(f.stream).convert("RGBA")
            except Exception as e:
                return jsonify({"error": f"PNG invalide : {type(e).__name__}"}), 400
            cropped = cropped.resize((450, 675), _Img.LANCZOS)
            # Save vers _proposed_<uid>_<ts>.png pour unicite
            import time as _t
            fname = f"_proposed_{uid}_{int(_t.time())}.png"
            cropped.convert("RGB").save(_os.path.join(sugg_dir, fname),
                                          "PNG", optimize=True)
            rel = f"/static/card_suggestions/{fname}"
            final_image_url = (public_base + rel) if public_base else rel
        else:
            final_image_url = new_image_url_json or None

        # Si rien de change : reject
        rarity_changed = new_rarity and new_rarity != (card.get("rarity") or "")
        if (new_name == card["name"]
                and new_universe == (card.get("universe") or "")
                and new_subtitle == (card.get("subtitle") or "")
                and not final_image_url
                and not rarity_changed):
            return jsonify({"error": "aucun changement detecte"}), 400

        sname = uname or f"User#{uid}"
        try:
            sid = card_suggestion_add(
                suggester_id=uid, suggester_name=sname,
                guild_id=None, channel_id=None,
                name=new_name, universe=new_universe or None,
                subtitle=new_subtitle or None,
                image_url=final_image_url or card.get("image_url"),
                source_type="attachment" if is_multipart else "url",
                suggestion_type="edit",
                target_card_id=cid,
                proposed_rarity=new_rarity or None,
            )
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "suggestion_id": sid})


    @app.route("/api/public/cards/stats", methods=["GET"])
    def api_public_cards_stats():
        from database import get_db, CARD_RARITY_WEIGHTS
        rarity = request.args.get("rarity") or None
        universe = request.args.get("universe") or None
        search = request.args.get("q") or None

        where = ["COALESCE(not_obtainable, 0) = 0"]; params = []
        if rarity:
            where.append("rarity = ?"); params.append(rarity)
        if universe:
            where.append("universe = ?"); params.append(universe)
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
            like = f"%{search.lower()}%"
            params += [like, like, like]

        conn = get_db(); c = conn.cursor()
        total = c.execute("SELECT COUNT(*) AS n FROM cards "
                          "WHERE COALESCE(not_obtainable, 0) = 0").fetchone()["n"]
        filtered = c.execute(
            f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}",
            params).fetchone()["n"]
        by_rarity_rows = c.execute(
            f"SELECT rarity, COUNT(*) AS n FROM cards WHERE {' AND '.join(where)} "
            f"GROUP BY rarity", params).fetchall()
        by_rarity = {r["rarity"]: r["n"] for r in by_rarity_rows}
        conn.close()

        # Poids globaux par rarete (constants /roll)
        total_weight = sum(CARD_RARITY_WEIGHTS.values())
        weights = {k: v for k, v in CARD_RARITY_WEIGHTS.items()}
        drop_rates_static = {k: round(v * 100 / total_weight, 2)
                              for k, v in weights.items()}

        # Calcul EFFECTIF en tenant compte du fallback /roll :
        # Quand rarete piochee mais count(rarete) == 0 dans filtre,
        # fallback random uniforme dans pool filtre. Redistribue les poids.
        # P(rarete=X)_effectif = (w_X/W si c_X>0 sinon 0)
        #                       + sum_{R, c_R=0} (w_R/W) * (c_X / T)
        all_rarities = list(weights.keys())
        # counts per rarity dans filtre (0 si absent)
        cnt = {r: int(by_rarity.get(r, 0)) for r in all_rarities}
        T = sum(cnt.values()) or 1
        fallback_weight = sum(weights[r] for r in all_rarities if cnt[r] == 0)
        drop_rates_effective = {}
        for X in all_rarities:
            direct = (weights[X] / total_weight) if cnt[X] > 0 else 0
            redistrib = (fallback_weight / total_weight) * (cnt[X] / T) if T > 0 else 0
            drop_rates_effective[X] = round((direct + redistrib) * 100, 2)

        # Probabilite par carte specifique (basee sur effectif)
        per_card_chance = {}
        for X in all_rarities:
            if cnt[X] > 0:
                per_card_chance[X] = round(drop_rates_effective[X] / cnt[X], 4)
            else:
                per_card_chance[X] = 0

        return jsonify({
            "total": int(total),
            "filtered": int(filtered),
            "by_rarity": by_rarity,
            "drop_rates": drop_rates_static,             # weights bruts /roll
            "drop_rates_effective": drop_rates_effective, # apres redistribution
            "per_card_chance": per_card_chance,
            "cooldown_seconds": 3600,
            "filtered_active": bool(rarity or universe or search),
        })


    # ===== OWNER =====
    @app.route("/owner/cards")
    def owner_cards_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_cards.html", active_nav="owner_cards")


    @app.route("/owner/cards/suggestions")
    def owner_cards_suggestions_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_card_suggestions.html",
                                 active_nav="owner_card_suggestions")


    @app.route("/api/owner/card-suggestions", methods=["GET"])
    def api_owner_card_suggestions_list():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_list
        status = request.args.get("status") or None
        items = card_suggestion_list(status=status, limit=500)
        return jsonify({"items": items})


    @app.route("/api/owner/card-suggestions/<int:sid>/target-card", methods=["GET"])
    def api_owner_card_suggestion_target(sid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_get, card_get
        sugg = card_suggestion_get(sid)
        if not sugg or not sugg.get("target_card_id"):
            return jsonify({"error": "pas de target"}), 404
        card = card_get(sugg["target_card_id"])
        if not card:
            return jsonify({"error": "carte cible introuvable"}), 404
        return jsonify({"card": card})


    @app.route("/api/owner/card-suggestions/leaderboard", methods=["GET"])
    def api_owner_card_sugg_leaderboard():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 100))
        except ValueError:
            limit = 20
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT suggester_id, suggester_name, COUNT(*) AS n "
            "FROM card_suggestions WHERE status = 'approved' "
            "GROUP BY suggester_id ORDER BY n DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return jsonify({"items": [{"user_id": r["suggester_id"],
                                      "name": r["suggester_name"],
                                      "approved_count": r["n"]} for r in rows]})


    @app.route("/api/owner/card-suggestions/counts", methods=["GET"])
    def api_owner_card_sugg_counts():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM card_suggestions GROUP BY status").fetchall()
        conn.close()
        counts = {r["status"]: r["n"] for r in rows}
        return jsonify({
            "pending":  int(counts.get("pending", 0)),
            "approved": int(counts.get("approved", 0)),
            "rejected": int(counts.get("rejected", 0)),
            "countered": int(counts.get("countered", 0)),
            "cancelled": int(counts.get("cancelled", 0)),
            "total":    int(sum(counts.values())),
        })


    @app.route("/api/owner/card-suggestions/pending-count", methods=["GET"])
    def api_owner_card_suggestions_pending_count():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_count_pending
        return jsonify({"count": card_suggestion_count_pending()})


    @app.route("/api/owner/card-suggestions/<int:sid>/approve", methods=["POST"])
    def api_owner_card_suggestion_approve(sid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (card_suggestion_get, card_suggestion_review,
                                card_add, get_db)
        from flask import session as _ses
        data = request.json or {}
        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        reviewer_id = _ses.get("user_id") or "owner"

        sugg_type = sugg.get("suggestion_type") or "new"
        if sugg_type == "edit" and sugg.get("target_card_id"):
            tcid = int(sugg["target_card_id"])
            # Recup card actuelle pour rarete + comparison
            from database import card_get
            target = card_get(tcid)
            if not target:
                return jsonify({"error": "carte cible introuvable"}), 404
            new_image_url = sugg.get("image_url") or ""

            conn = get_db(); c = conn.cursor()
            fields = []; params = []
            for k in ("name", "universe", "subtitle"):
                v = sugg.get(k)
                if v is not None and v != "":
                    fields.append(f"{k} = ?"); params.append(v)
            # Rarete proposee
            proposed_rar = sugg.get("proposed_rarity")
            if proposed_rar and proposed_rar in ("common","rare","epic","legendary","mythic","secret"):
                fields.append("rarity = ?"); params.append(proposed_rar)
            # Image
            image_changed = (new_image_url and new_image_url != (target.get("image_url") or ""))
            if image_changed:
                fields.append("source_image_url = ?"); params.append(new_image_url)
            if fields:
                params.append(tcid)
                c.execute(f"UPDATE cards SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit(); conn.close()

            # Re-bake si image OU rarete change
            final_rarity = proposed_rar if proposed_rar else target.get("rarity", "common")
            rarity_changed = proposed_rar and proposed_rar != target.get("rarity")
            rebaked = False
            if image_changed or rarity_changed:
                from services.cards_overlay import composite_card
                import os as _os
                src_for_bake = new_image_url if image_changed else (target.get("source_image_url") or target.get("image_url"))
                if src_for_bake and "/card_renders/" not in src_for_bake and "/card_suggestions/" not in src_for_bake:
                    try:
                        url = composite_card(src_for_bake, final_rarity, tcid)
                        if url:
                            public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                            final = (public_base + url) if public_base else url
                            conn = get_db(); c = conn.cursor()
                            # IMPORTANT : save source_image_url si pas deja set
                            # pour pouvoir re-cropper plus tard
                            if not target.get("source_image_url"):
                                c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                                           (final, src_for_bake, tcid))
                            else:
                                c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                                           (final, tcid))
                            conn.commit(); conn.close()
                            rebaked = True
                    except Exception as e:
                        print(f"[approve edit rebake] err {tcid}: {e}")

            card_suggestion_review(sid, "approved", reviewer_id, created_card_id=tcid)
            return jsonify({"ok": True, "card_id": tcid, "type": "edit",
                             "rebaked": rebaked,
                             "rarity_changed": rarity_changed,
                             "new_rarity": final_rarity if rarity_changed else None})

        # type 'new' : create nouvelle carte
        # Priorité : rarity body > proposed_rarity suggestion > common
        rarity = (data.get("rarity") or sugg.get("proposed_rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        try:
            cid = card_add(
                name=sugg["name"],
                universe=sugg.get("universe"),
                subtitle=sugg.get("subtitle"),
                rarity=rarity,
                image_url=sugg.get("image_url"),
                description=f"Suggestion communautaire de {sugg.get('suggester_name', '?')}.",
            )
        except Exception as e:
            return jsonify({"error": f"erreur create : {type(e).__name__}: {e}"}), 500
        # Bake overlay auto pour single approve aussi
        from services.cards_overlay import composite_card
        import os as _os
        src = sugg.get("image_url") or ""
        rebaked = False
        if src and "/card_renders/" not in src and "/card_suggestions/" not in src:
            try:
                url = composite_card(src, rarity, cid)
                if url:
                    public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                    final = (public_base + url) if public_base else url
                    conn = get_db(); c = conn.cursor()
                    c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                               (final, src, cid))
                    conn.commit(); conn.close()
                    rebaked = True
            except Exception as e:
                print(f"[approve bake] err {cid}: {e}")
        card_suggestion_review(sid, "approved", reviewer_id, created_card_id=cid)
        return jsonify({"ok": True, "card_id": cid, "type": "new", "rebaked": rebaked})


    def _approve_one_suggestion(sid, rarity_override=None, reviewer_id="owner"):
        """Helper : approve une suggestion + bake overlay auto. Retourne dict."""
        from database import (card_suggestion_get, card_suggestion_review,
                                card_add, card_get, get_db)
        import os as _os
        from services.cards_overlay import composite_card
        sugg = card_suggestion_get(sid)
        if not sugg:
            return {"ok": False, "error": "introuvable"}
        if sugg["status"] != "pending":
            return {"ok": False, "error": f"deja {sugg['status']}"}

        sugg_type = sugg.get("suggestion_type") or "new"
        if sugg_type == "edit" and sugg.get("target_card_id"):
            tcid = int(sugg["target_card_id"])
            target = card_get(tcid)
            if not target:
                return {"ok": False, "error": "carte cible introuvable"}
            new_image_url = sugg.get("image_url") or ""
            conn = get_db(); c = conn.cursor()
            fields = []; params = []
            for k in ("name", "universe", "subtitle"):
                v = sugg.get(k)
                if v is not None and v != "":
                    fields.append(f"{k} = ?"); params.append(v)
            proposed_rar = sugg.get("proposed_rarity")
            if proposed_rar and proposed_rar in ("common","rare","epic","legendary","mythic","secret"):
                fields.append("rarity = ?"); params.append(proposed_rar)
            image_changed = (new_image_url and new_image_url != (target.get("image_url") or ""))
            if image_changed:
                fields.append("source_image_url = ?"); params.append(new_image_url)
            if fields:
                params.append(tcid)
                c.execute(f"UPDATE cards SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit(); conn.close()
            final_rarity = proposed_rar if proposed_rar else target.get("rarity", "common")
            rarity_changed = proposed_rar and proposed_rar != target.get("rarity")
            # Rebake si image OU rarete change
            if image_changed or rarity_changed:
                src_for_bake = new_image_url if image_changed else (target.get("source_image_url") or target.get("image_url"))
                if src_for_bake and "/card_renders/" not in src_for_bake and "/card_suggestions/" not in src_for_bake:
                    try:
                        url = composite_card(src_for_bake, final_rarity, tcid)
                        if url:
                            public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                            final = (public_base + url) if public_base else url
                            conn = get_db(); c = conn.cursor()
                            if not target.get("source_image_url"):
                                c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                                           (final, src_for_bake, tcid))
                            else:
                                c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                                           (final, tcid))
                            conn.commit(); conn.close()
                    except Exception as e:
                        print(f"[bulk approve edit rebake] err {tcid}: {e}")
            card_suggestion_review(sid, "approved", reviewer_id, created_card_id=tcid)
            return {"ok": True, "card_id": tcid, "type": "edit"}

        # type 'new' : priorite override > proposed_rarity > common
        rarity = (rarity_override or sugg.get("proposed_rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        try:
            cid = card_add(
                name=sugg["name"],
                universe=sugg.get("universe"),
                subtitle=sugg.get("subtitle"),
                rarity=rarity,
                image_url=sugg.get("image_url"),
                description=f"Suggestion communautaire de {sugg.get('suggester_name', '?')}.",
            )
        except Exception as e:
            return {"ok": False, "error": f"create card : {type(e).__name__}: {e}"}
        # Bake overlay auto
        src = sugg.get("image_url") or ""
        if src and "/card_renders/" not in src and "/card_suggestions/" not in src:
            try:
                url = composite_card(src, rarity, cid)
                if url:
                    public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                    final = (public_base + url) if public_base else url
                    conn = get_db(); c = conn.cursor()
                    c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                               (final, src, cid))
                    conn.commit(); conn.close()
            except Exception as e:
                print(f"[bulk approve bake] err {cid}: {e}")
        card_suggestion_review(sid, "approved", reviewer_id, created_card_id=cid)
        return {"ok": True, "card_id": cid, "type": "new"}


    @app.route("/api/owner/card-suggestions/bulk-approve", methods=["POST"])
    def api_owner_card_sugg_bulk_approve():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from flask import session as _ses
        data = request.json or {}
        sids = data.get("sids") or []
        if not isinstance(sids, list) or not sids:
            return jsonify({"error": "sids vide"}), 400
        try:
            sids_int = [int(x) for x in sids][:500]
        except (ValueError, TypeError):
            return jsonify({"error": "sids invalides"}), 400
        default_rarity = (data.get("default_rarity") or "common").strip()
        # rarities optionnel : dict {sid: rarity} pour override par suggestion
        rarities = data.get("rarities") or {}
        if not isinstance(rarities, dict):
            rarities = {}
        reviewer = _ses.get("user_id") or "owner"
        stats = {"approved": 0, "failed": 0, "details": []}
        for sid in sids_int:
            per_rar = (rarities.get(str(sid)) or rarities.get(sid) or default_rarity)
            res = _approve_one_suggestion(sid, rarity_override=per_rar, reviewer_id=reviewer)
            if res.get("ok"): stats["approved"] += 1
            else: stats["failed"] += 1
            stats["details"].append({"sid": sid, **res})
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/card-suggestions/bulk-reject", methods=["POST"])
    def api_owner_card_sugg_bulk_reject():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_get, card_suggestion_review
        from flask import session as _ses
        data = request.json or {}
        sids = data.get("sids") or []
        if not isinstance(sids, list) or not sids:
            return jsonify({"error": "sids vide"}), 400
        try:
            sids_int = [int(x) for x in sids][:500]
        except (ValueError, TypeError):
            return jsonify({"error": "sids invalides"}), 400
        reason = (data.get("reason") or "").strip()[:200] or None
        reviewer = _ses.get("user_id") or "owner"
        rejected = 0; skipped = 0
        for sid in sids_int:
            sugg = card_suggestion_get(sid)
            if not sugg or sugg["status"] != "pending":
                skipped += 1; continue
            card_suggestion_review(sid, "rejected", reviewer, reason=reason)
            rejected += 1
        return jsonify({"ok": True, "rejected": rejected, "skipped": skipped})


    @app.route("/api/owner/card-suggestions/<int:sid>/approve-cropped", methods=["POST"])
    def api_owner_card_suggestion_approve_cropped(sid):
        """Recoit image cropee (multipart) + cree card avec cette image."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (card_suggestion_get, card_suggestion_review,
                                card_add, get_db)
        from flask import session as _ses
        import os as _os, io as _io
        from PIL import Image as _Img
        from services.cards_overlay import _OUTPUT_DIR as _RENDERS_DIR

        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        rarity = (request.form.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        if "cropped" not in request.files:
            return jsonify({"error": "champ 'cropped' manquant"}), 400
        f = request.files["cropped"]
        try:
            cropped = _Img.open(f.stream).convert("RGBA")
        except Exception as e:
            return jsonify({"error": f"PNG invalide : {type(e).__name__}"}), 400

        tmp_dir = _os.path.join(_os.path.dirname(_RENDERS_DIR), "card_suggestions")
        _os.makedirs(tmp_dir, exist_ok=True)
        cropped = cropped.resize((450, 675), _Img.LANCZOS)

        try:
            cid = card_add(
                name=sugg["name"],
                universe=sugg.get("universe"),
                subtitle=sugg.get("subtitle"),
                rarity=rarity, image_url=None,
                description=f"Suggestion communautaire de {sugg.get('suggester_name', '?')}.",
            )
        except Exception as e:
            return jsonify({"error": f"create card : {type(e).__name__}: {e}"}), 500

        out_path = _os.path.join(tmp_dir, f"{cid}.png")
        cropped.convert("RGB").save(out_path, "PNG", optimize=True)

        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        rel = f"/static/card_suggestions/{cid}.png"
        final_url = (public_base + rel) if public_base else rel
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                  (final_url, sugg.get("image_url"), cid))
        conn.commit(); conn.close()

        reviewer_id = _ses.get("user_id") or "owner"
        card_suggestion_review(sid, "approved", reviewer_id, created_card_id=cid)
        return jsonify({"ok": True, "card_id": cid, "image_url": final_url})


    @app.route("/api/owner/card-suggestions/<int:sid>/reject", methods=["POST"])
    def api_owner_card_suggestion_reject(sid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_get, card_suggestion_review
        from flask import session as _ses
        data = request.json or {}
        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        reason = (data.get("reason") or "").strip()[:200] or None
        reviewer_id = _ses.get("user_id") or "owner"
        card_suggestion_review(sid, "rejected", reviewer_id, reason=reason)
        return jsonify({"ok": True})


    @app.route("/api/owner/cards", methods=["GET"])
    def api_owner_cards_list():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        rarity = request.args.get("rarity") or None
        universe = request.args.get("universe") or None
        search = request.args.get("q") or None
        sort = (request.args.get("sort") or "name_asc").strip()
        try:
            per_page = max(1, min(int(request.args.get("per_page", 50)), 500))
        except ValueError:
            per_page = 50
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        offset = (page - 1) * per_page

        conn = get_db(); c = conn.cursor()
        where = ["1=1"]
        params = []
        if rarity:
            where.append("rarity = ?"); params.append(rarity)
        if universe:
            where.append("universe = ?"); params.append(universe)
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
            like = f"%{search.lower()}%"
            params += [like, like, like]
        sort_sql = {
            "name_asc":     "name ASC",
            "name_desc":    "name DESC",
            "rarity_desc":  "CASE rarity WHEN 'secret' THEN 0 WHEN 'mythic' THEN 1 "
                             "WHEN 'legendary' THEN 2 WHEN 'epic' THEN 3 WHEN 'rare' THEN 4 "
                             "WHEN 'common' THEN 5 ELSE 6 END ASC, name ASC",
            "rarity_asc":   "CASE rarity WHEN 'common' THEN 0 WHEN 'rare' THEN 1 "
                             "WHEN 'epic' THEN 2 WHEN 'legendary' THEN 3 WHEN 'mythic' THEN 4 "
                             "WHEN 'secret' THEN 5 ELSE 6 END ASC, name ASC",
            "universe_asc": "universe ASC, name ASC",
            "subtitle_asc": "subtitle ASC, name ASC",
            "newest":       "id DESC",
            "oldest":       "id ASC",
        }.get(sort, "name ASC")

        filtered = c.execute(
            f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}",
            params).fetchone()["n"]
        rows = c.execute(
            f"SELECT *, "
            f"  (SELECT COUNT(*) FROM user_cards WHERE card_id = cards.id) AS owned_count, "
            f"  (SELECT COUNT(DISTINCT user_id) FROM user_cards WHERE card_id = cards.id) AS owners_count "
            f"FROM cards WHERE {' AND '.join(where)} "
            f"ORDER BY {sort_sql} LIMIT ? OFFSET ?",
            params + [per_page, offset]).fetchall()
        items = [dict(r) for r in rows]
        total = c.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
        conn.close()
        return jsonify({
            "items": items,
            "total": int(total),
            "filtered": int(filtered),
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (int(filtered) + per_page - 1) // per_page),
        })


    @app.route("/api/owner/cards", methods=["POST"])
    def api_owner_cards_create():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_add
        data = request.json or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name requis"}), 400
        rarity = (data.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "rarity invalide"}), 400
        cid = card_add(
            name=name,
            universe=(data.get("universe") or "").strip() or None,
            subtitle=(data.get("subtitle") or "").strip() or None,
            rarity=rarity,
            image_url=(data.get("image_url") or "").strip() or None,
            description=(data.get("description") or "").strip() or None,
            flavor_subtitle=(data.get("flavor_subtitle") or "").strip() or None,
        )
        return jsonify({"ok": True, "id": cid})


    @app.route("/api/owner/cards/<int:cid>", methods=["PATCH"])
    def api_owner_cards_update(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        allowed = {"name", "universe", "subtitle", "rarity", "image_url", "description", "flavor_subtitle", "not_obtainable"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "rien a update"}), 400
        if "rarity" in fields and fields["rarity"] not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "rarity invalide"}), 400
        if "not_obtainable" in fields:
            fields["not_obtainable"] = 1 if fields["not_obtainable"] else 0
        conn = get_db(); c = conn.cursor()
        sets = ", ".join(f"{k} = ?" for k in fields.keys())
        c.execute(f"UPDATE cards SET {sets} WHERE id = ?",
                  (*fields.values(), int(cid)))
        ok = c.rowcount > 0
        conn.commit(); conn.close()
        return jsonify({"ok": ok})


    @app.route("/api/owner/cards/<int:cid>", methods=["DELETE"])
    def api_owner_cards_delete(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_delete
        ok = card_delete(cid)
        return jsonify({"ok": ok})


    @app.route("/api/owner/cards/refresh-images", methods=["POST"])
    def api_owner_cards_refresh_images():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_image_fetcher import refresh_all_cards_images
        data = request.json or {}
        force = bool(data.get("force"))
        try:
            stats = refresh_all_cards_images(force_overwrite=force)
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-jikan", methods=["POST"])
    def api_owner_cards_bulk_jikan():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_jikan_bulk import bulk_import_jikan
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 40)), 100))
        except (ValueError, TypeError):
            pages = 40
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_jikan(pages=pages, skip_existing=skip_existing)
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-anilist", methods=["POST"])
    def api_owner_cards_bulk_anilist():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_anilist_bulk import bulk_import_anilist
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 20)), 50))
        except (ValueError, TypeError):
            pages = 20
        skip_existing = bool(data.get("skip_existing", True))
        wipe_first = bool(data.get("wipe_first", False))
        try:
            stats = bulk_import_anilist(pages=pages, skip_existing=skip_existing,
                                          wipe_first=wipe_first)
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/<int:cid>/give", methods=["POST"])
    def api_owner_cards_give(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_get, user_card_add
        data = request.json or {}
        user_id = (data.get("user_id") or "").strip()
        if not user_id or not user_id.isdigit():
            return jsonify({"error": "user_id invalide (ID Discord numerique)"}), 400
        try:
            qty = max(1, min(int(data.get("qty", 1)), 100))
        except (ValueError, TypeError):
            qty = 1
        card = card_get(cid)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        for _ in range(qty):
            user_card_add(user_id, cid)
        return jsonify({"ok": True, "given": qty, "card_name": card.get("name"),
                         "user_id": user_id})


    @app.route("/api/owner/cards/bulk-import-game", methods=["POST"])
    def api_owner_cards_bulk_one_game():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_games import bulk_import_game, GAMES
        data = request.json or {}
        game_key = (data.get("game_key") or "").strip()
        if game_key not in GAMES:
            return jsonify({"error": f"jeu inconnu : {game_key}"}), 400
        try:
            limit = max(10, min(int(data.get("limit", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_game(game_key, limit=limit, skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-games-multi", methods=["POST"])
    def api_owner_cards_bulk_multi_games():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_games import bulk_import_multiple_games, GAMES
        data = request.json or {}
        game_keys = data.get("game_keys") or []
        if not isinstance(game_keys, list) or not game_keys:
            return jsonify({"error": "game_keys liste vide"}), 400
        invalid = [g for g in game_keys if g not in GAMES]
        if invalid:
            return jsonify({"error": f"jeux inconnus : {invalid}"}), 400
        try:
            limit = max(10, min(int(data.get("limit_per_game", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_multiple_games(game_keys, limit_per_game=limit,
                                                  skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/import/start", methods=["POST"])
    def api_owner_cards_import_start():
        """Start an import job en background. Retourne job_id."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.import_jobs import run_async
        data = request.json or {}
        source = (data.get("source") or "").strip().lower()
        params = data.get("params") or {}
        if not isinstance(params, dict):
            return jsonify({"error": "params doit etre dict"}), 400

        if source == "anilist":
            from services.cards_anilist_bulk import bulk_import_anilist
            pages = max(1, min(int(params.get("pages", 20)), 100))
            start_page = max(1, int(params.get("start_page", 1)))
            job_id = run_async(f"Anilist pages {start_page}-{start_page+pages-1}",
                                 bulk_import_anilist,
                                 pages=pages, start_page=start_page,
                                 skip_existing=True)
        elif source == "jikan":
            from services.cards_jikan_bulk import bulk_import_jikan
            pages = max(1, min(int(params.get("pages", 40)), 100))
            job_id = run_async(f"Jikan {pages} pages",
                                 bulk_import_jikan,
                                 pages=pages, skip_existing=True)
        elif source == "superhero":
            from services.cards_superhero_bulk import bulk_import_superhero
            publishers = params.get("publishers")
            if publishers is not None and not isinstance(publishers, list):
                return jsonify({"error": "publishers liste ou null"}), 400
            label = ("SuperHero " + (",".join(publishers) if publishers else "all"))
            job_id = run_async(label, bulk_import_superhero,
                                 publishers=publishers, skip_existing=True)
        elif source == "fandom_game":
            from services.cards_fandom_games import bulk_import_game, GAMES
            game_key = (params.get("game_key") or "").strip()
            if game_key not in GAMES:
                return jsonify({"error": f"jeu inconnu : {game_key}"}), 400
            limit_v = max(10, min(int(params.get("limit", 1000)), 2000))
            job_id = run_async(f"Fandom game {game_key}",
                                 bulk_import_game, game_key,
                                 limit=limit_v, skip_existing=True)
        elif source == "pokemon":
            from services.cards_pokemon_bulk import bulk_import_pokemon
            start_id = max(1, int(params.get("start_id", 1)))
            end_id = max(start_id, min(int(params.get("end_id", 1025)), 1025))
            job_id = run_async(f"Pokémon #{start_id}-{end_id}",
                                 bulk_import_pokemon,
                                 start_id=start_id, end_id=end_id,
                                 skip_existing=True)
        elif source == "hakush":
            from services.cards_hakush_bulk import bulk_import_hakush, GAMES_HAKUSH
            gkey = (params.get("game_key") or "").strip()
            if gkey not in GAMES_HAKUSH:
                return jsonify({"error": f"hakush jeu inconnu : {gkey}"}), 400
            job_id = run_async(f"hakush {gkey}", bulk_import_hakush,
                                 gkey, skip_existing=True)
        elif source == "nookipedia":
            from services.cards_nookipedia_bulk import bulk_import_nookipedia
            job_id = run_async("Animal Crossing villagers",
                                 bulk_import_nookipedia,
                                 skip_existing=True)
        elif source == "fandom_show":
            from services.cards_fandom_films import bulk_import_show, SHOWS
            show_key = (params.get("show_key") or "").strip()
            if show_key not in SHOWS:
                return jsonify({"error": f"show inconnu : {show_key}"}), 400
            limit_v = max(10, min(int(params.get("limit", 1000)), 2000))
            job_id = run_async(f"Fandom show {show_key}",
                                 bulk_import_show, show_key,
                                 limit=limit_v, skip_existing=True)
        else:
            return jsonify({"error": f"source inconnue : {source}"}), 400

        return jsonify({"ok": True, "job_id": job_id})


    @app.route("/api/owner/cards/import/progress/<job_id>", methods=["GET"])
    def api_owner_cards_import_progress(job_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.import_jobs import get_job
        j = get_job(job_id)
        if not j:
            return jsonify({"error": "job introuvable"}), 404
        return jsonify(j)


    @app.route("/api/owner/cards/bulk-import-superhero", methods=["POST"])
    def api_owner_cards_bulk_superhero():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_superhero_bulk import bulk_import_superhero
        data = request.json or {}
        publishers = data.get("publishers")
        if publishers is not None and not isinstance(publishers, list):
            return jsonify({"error": "publishers doit etre liste ou null"}), 400
        skip_existing = bool(data.get("skip_existing", True))
        try:
            limit_v = int(data.get("limit", 0))
            limit_v = limit_v if limit_v > 0 else None
        except (ValueError, TypeError):
            limit_v = None
        try:
            stats = bulk_import_superhero(publishers=publishers,
                                            skip_existing=skip_existing,
                                            limit=limit_v)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-marvel", methods=["POST"])
    def api_owner_cards_bulk_marvel():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_marvel_bulk import bulk_import_marvel
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 15)), 30))
        except (ValueError, TypeError):
            pages = 15
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_marvel(pages=pages, skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-show", methods=["POST"])
    def api_owner_cards_bulk_one_show():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_films import bulk_import_show, SHOWS
        data = request.json or {}
        show_key = (data.get("show_key") or "").strip()
        if show_key not in SHOWS:
            return jsonify({"error": f"show inconnu : {show_key}"}), 400
        try:
            limit = max(10, min(int(data.get("limit", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_show(show_key, limit=limit, skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-shows-multi", methods=["POST"])
    def api_owner_cards_bulk_multi_shows():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_films import bulk_import_multiple_shows, SHOWS
        data = request.json or {}
        show_keys = data.get("show_keys") or []
        if not isinstance(show_keys, list) or not show_keys:
            return jsonify({"error": "show_keys liste vide"}), 400
        invalid = [s for s in show_keys if s not in SHOWS]
        if invalid:
            return jsonify({"error": f"shows inconnus : {invalid}"}), 400
        try:
            limit = max(10, min(int(data.get("limit_per_show", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_multiple_shows(show_keys, limit_per_show=limit,
                                                  skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/games-available", methods=["GET"])
    def api_owner_cards_games_available():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_games import GAMES
        return jsonify({"items": [{"key": k, "name": v["name"]} for k, v in GAMES.items()]})


    @app.route("/api/owner/cards/bulk-import-giantbomb", methods=["POST"])
    def api_owner_cards_bulk_gb():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_giantbomb_bulk import bulk_import_giantbomb
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 50)), 200))
        except (ValueError, TypeError):
            pages = 50
        try:
            page_size = max(1, min(int(data.get("page_size", 100)), 100))
        except (ValueError, TypeError):
            page_size = 100
        try:
            start_offset = max(0, int(data.get("start_offset", 0)))
        except (ValueError, TypeError):
            start_offset = 0
        try:
            sleep_between = max(1.0, float(data.get("sleep_between", 17.0)))
        except (ValueError, TypeError):
            sleep_between = 17.0
        skip_existing = bool(data.get("skip_existing", True))
        wipe_first = bool(data.get("wipe_first", False))
        try:
            stats = bulk_import_giantbomb(pages=pages, page_size=page_size,
                                            sleep_between=sleep_between,
                                            skip_existing=skip_existing,
                                            wipe_first=wipe_first,
                                            start_offset=start_offset)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-igdb", methods=["POST"])
    def api_owner_cards_bulk_igdb():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_igdb_bulk import bulk_import_igdb
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 4)), 20))
        except (ValueError, TypeError):
            pages = 4
        try:
            page_size = max(1, min(int(data.get("page_size", 500)), 500))
        except (ValueError, TypeError):
            page_size = 500
        skip_existing = bool(data.get("skip_existing", True))
        wipe_first = bool(data.get("wipe_first", False))
        try:
            stats = bulk_import_igdb(pages=pages, page_size=page_size,
                                       skip_existing=skip_existing,
                                       wipe_first=wipe_first)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/<int:cid>/recrop", methods=["POST"])
    def api_owner_card_recrop(cid):
        """Recoit l'image deja cropee (multipart) + applique overlay rarete."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        import os as _os, io as _io
        from PIL import Image as _Img
        from services.cards_overlay import _OUTPUT_DIR, _CARD_W, _CARD_H, _get_overlay

        # Body : multipart avec champ 'cropped' (PNG blob de Cropper canvas)
        if "cropped" not in request.files:
            return jsonify({"error": "champ 'cropped' manquant (multipart file)"}), 400
        f = request.files["cropped"]
        try:
            cropped = _Img.open(f.stream).convert("RGBA")
        except Exception as e:
            return jsonify({"error": f"PNG invalide : {type(e).__name__}: {e}"}), 400

        conn = get_db(); c = conn.cursor()
        row = c.execute("SELECT id, name, rarity, source_image_url, image_url "
                         "FROM cards WHERE id = ?", (int(cid),)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "carte introuvable"}), 404
        rarity = row["rarity"] or "common"
        conn.close()

        # Resize au format card standard (cropper a deja envoye 2:3, juste resize precis)
        resized = cropped.resize((_CARD_W, _CARD_H), _Img.LANCZOS)
        canvas = _Img.new("RGBA", (_CARD_W, _CARD_H), (26, 26, 26, 255))
        canvas.paste(resized, (0, 0), resized)
        overlay = _get_overlay(rarity)
        if overlay is not None:
            canvas = _Img.alpha_composite(canvas, overlay)

        _os.makedirs(_OUTPUT_DIR, exist_ok=True)
        out_path = _os.path.join(_OUTPUT_DIR, f"{cid}.png")
        canvas.convert("RGB").save(out_path, "PNG", optimize=True)

        rel = f"/static/card_renders/{cid}.png"
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        final = (public_base + rel) if public_base else rel
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final, cid))
        conn.commit(); conn.close()
        return jsonify({"ok": True, "image_url": final})


    @app.route("/api/owner/cards/<int:cid>/rebake", methods=["POST"])
    def api_owner_card_rebake(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        from services.cards_overlay import composite_card
        import os as _os
        data = request.json or {}
        overlay_rarity = (data.get("overlay_rarity") or "").strip().lower()
        if overlay_rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "overlay_rarity invalide"}), 400
        conn = get_db(); c = conn.cursor()
        row = c.execute("SELECT id, source_image_url, image_url FROM cards WHERE id = ?",
                         (int(cid),)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "carte introuvable"}), 404
        src = row["source_image_url"] or row["image_url"] or ""
        if not src or "/card_renders/" in src or "/card_suggestions/" in src:
            conn.close()
            return jsonify({"error": "pas de source image pour rebake (URL deja locale)"}), 400
        # Save source si pas deja
        if not row["source_image_url"]:
            c.execute("UPDATE cards SET source_image_url = ? WHERE id = ?",
                       (src, cid))
            conn.commit()
        conn.close()
        # Composite avec overlay choisi
        url = composite_card(src, overlay_rarity, int(cid))
        if not url:
            return jsonify({"error": "echec composite"}), 500
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        final = (public_base + url) if public_base else url
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final, cid))
        conn.commit(); conn.close()
        return jsonify({"ok": True, "image_url": final})


    @app.route("/api/owner/cards/backfill-source", methods=["POST"])
    def api_owner_cards_backfill_source():
        """Pour les cartes qui ont image_url local mais source_image_url NULL,
        impossible de re-cropper. Cherche dans logs ou propose au moins de
        nullifier l'image_url local pour forcer re-fetch via le wizard.

        Strategie : pour les cartes dont image_url contient /card_renders/,
        on regarde si source est NULL. Si oui, on ne peut rien faire.
        Sinon OK. On expose un compteur de cartes sans source."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT COUNT(*) AS n FROM cards "
                  "WHERE (image_url LIKE '%/card_renders/%' OR image_url LIKE '%/card_suggestions/%') "
                  "AND (source_image_url IS NULL OR source_image_url = '')")
        broken = int(c.fetchone()["n"])
        c.execute("SELECT COUNT(*) AS n FROM cards "
                  "WHERE source_image_url IS NOT NULL AND source_image_url != ''")
        ok = int(c.fetchone()["n"])
        conn.close()
        return jsonify({
            "ok": True,
            "without_source": broken,
            "with_source": ok,
            "note": "Cartes sans source ne peuvent pas etre re-cropees ou re-bakees. Re-importer la source d'origine si possible.",
        })


    @app.route("/api/owner/cards/bake-overlays-async", methods=["POST"])
    def api_owner_cards_bake_async():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.import_jobs import run_async
        from services.cards_overlay import bake_all_cards
        import os as _os
        data = request.json or {}
        force = bool(data.get("force"))
        public_base = (data.get("public_base_url")
                        or _os.getenv("PUBLIC_BASE_URL")
                        or "").strip() or None
        workers = max(1, min(int(data.get("workers", 10)), 30))
        label = f"Baker overlays {'(FORCE)' if force else ''} w={workers}"
        job_id = run_async(label, bake_all_cards,
                             force=force, public_base_url=public_base,
                             workers=workers)
        return jsonify({"ok": True, "job_id": job_id})


    @app.route("/api/owner/cards/bake-overlays", methods=["POST"])
    def api_owner_cards_bake():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_overlay import bake_all_cards
        import os as _os
        data = request.json or {}
        force = bool(data.get("force"))
        public_base = (data.get("public_base_url")
                        or _os.getenv("PUBLIC_BASE_URL")
                        or "").strip() or None
        try:
            stats = bake_all_cards(force=force, public_base_url=public_base)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/user/<user_id>/cards/stats", methods=["GET"])
    def api_owner_user_cards_stats(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        total = c.execute("SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ?",
                           (str(user_id),)).fetchone()["n"]
        uniq = c.execute("SELECT COUNT(DISTINCT card_id) AS n FROM user_cards WHERE user_id = ?",
                          (str(user_id),)).fetchone()["n"]
        cds = c.execute("SELECT COUNT(*) AS n FROM user_guild_roll_cooldown WHERE user_id = ?",
                         (str(user_id),)).fetchone()["n"]
        conn.close()
        return jsonify({"total_cards": int(total), "unique_cards": int(uniq),
                         "cooldowns_active": int(cds)})


    @app.route("/api/owner/user/<user_id>/cards/clear", methods=["POST"])
    def api_owner_user_cards_clear(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("DELETE FROM user_cards WHERE user_id = ?", (str(user_id),))
        deleted = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": int(deleted)})


    @app.route("/api/owner/user/<user_id>/cards/give-existing", methods=["POST"])
    def api_owner_user_cards_give_existing(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_get, user_card_add_with_flag
        data = request.json or {}
        card_id = data.get("card_id")
        try:
            card_id = int(card_id) if card_id is not None else None
        except (ValueError, TypeError):
            card_id = None
        if not card_id:
            return jsonify({"error": "card_id requis"}), 400
        try:
            qty = max(1, min(int(data.get("qty", 1)), 100))
        except (ValueError, TypeError):
            qty = 1
        not_tradeable = bool(data.get("not_tradeable", False))
        card = card_get(card_id)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        for _ in range(qty):
            user_card_add_with_flag(user_id, card_id, not_tradeable=not_tradeable)
        return jsonify({"ok": True, "given": qty, "card_name": card.get("name"),
                         "not_tradeable": not_tradeable})


    @app.route("/api/owner/user/<user_id>/cards/give-new", methods=["POST"])
    def api_owner_user_cards_give_new(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_add, user_card_add_with_flag
        from services.cards_overlay import composite_card
        import os as _os
        data = request.json or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name requis"}), 400
        rarity = (data.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        try:
            qty = max(1, min(int(data.get("qty", 1)), 100))
        except (ValueError, TypeError):
            qty = 1
        not_tradeable = bool(data.get("not_tradeable", False))
        image_url = (data.get("image_url") or "").strip() or None
        try:
            cid = card_add(
                name=name,
                universe=(data.get("universe") or "").strip() or None,
                subtitle=(data.get("subtitle") or "").strip() or None,
                rarity=rarity, image_url=image_url,
                description=(data.get("description") or "").strip() or None,
                flavor_subtitle=(data.get("flavor_subtitle") or "").strip() or None,
            )
        except Exception as e:
            return jsonify({"error": f"create card : {type(e).__name__}: {e}"}), 500
        # Bake overlay si image fournie
        if image_url and "/card_renders/" not in image_url and "/card_suggestions/" not in image_url:
            try:
                url = composite_card(image_url, rarity, cid)
                if url:
                    public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                    final = (public_base + url) if public_base else url
                    from database import get_db
                    conn = get_db(); c = conn.cursor()
                    c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                               (final, image_url, cid))
                    conn.commit(); conn.close()
            except Exception as e:
                print(f"[give-new bake] err {cid}: {e}")
        for _ in range(qty):
            user_card_add_with_flag(user_id, cid, not_tradeable=not_tradeable)
        return jsonify({"ok": True, "card_id": cid, "given": qty,
                         "not_tradeable": not_tradeable})


    @app.route("/api/owner/user/<user_id>/cards/reset-cooldown", methods=["POST"])
    def api_owner_user_cards_reset_cd(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("DELETE FROM user_guild_roll_cooldown WHERE user_id = ?",
                  (str(user_id),))
        deleted = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": int(deleted)})


    @app.route("/api/owner/user/<user_id>/cards/list", methods=["GET"])
    def api_owner_user_cards_list(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT uc.id AS uc_id, uc.card_id, uc.not_tradeable, uc.claimed_at, "
            "c.name, c.rarity, c.universe, c.subtitle, c.image_url "
            "FROM user_cards uc LEFT JOIN cards c ON c.id = uc.card_id "
            "WHERE uc.user_id = ? ORDER BY uc.claimed_at DESC, uc.id DESC",
            (str(user_id),)).fetchall()
        conn.close()
        return jsonify({"items": [dict(r) for r in rows]})


    @app.route("/api/owner/user/<user_id>/cards/<int:uc_id>", methods=["DELETE"])
    def api_owner_user_cards_remove_one(user_id, uc_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("DELETE FROM user_cards WHERE id = ? AND user_id = ?",
                  (int(uc_id), str(user_id)))
        deleted = c.rowcount
        conn.commit(); conn.close()
        if not deleted:
            return jsonify({"error": "ligne introuvable"}), 404
        return jsonify({"ok": True})


    @app.route("/api/owner/cards/bulk-update", methods=["POST"])
    def api_owner_cards_bulk_update():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids vide"}), 400
        try:
            ids_int = [int(x) for x in ids][:5000]
        except (ValueError, TypeError):
            return jsonify({"error": "ids invalides"}), 400
        fields = data.get("fields") or {}
        allowed = {"rarity", "universe", "subtitle", "description", "flavor_subtitle"}
        clean = {}
        for k, v in fields.items():
            if k not in allowed: continue
            if v is None or (isinstance(v, str) and v.strip() == ""): continue
            if k == "rarity" and v not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
                continue
            clean[k] = v.strip() if isinstance(v, str) else v
        if not clean:
            return jsonify({"error": "aucun champ a update"}), 400
        sets = ", ".join(f"{k} = ?" for k in clean.keys())
        placeholders = ",".join("?" * len(ids_int))
        sql_params = list(clean.values()) + ids_int
        conn = get_db(); c = conn.cursor()
        c.execute(f"UPDATE cards SET {sets} WHERE id IN ({placeholders})", sql_params)
        updated = c.rowcount
        conn.commit(); conn.close()

        # Si rarete changee : auto-rebake overlay pour ces cards
        rebake_stats = None
        if "rarity" in clean:
            from services.cards_overlay import composite_card
            import os as _os
            public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
            new_rarity = clean["rarity"]
            conn = get_db(); c = conn.cursor()
            rows = c.execute(
                f"SELECT id, source_image_url, image_url FROM cards "
                f"WHERE id IN ({placeholders})", ids_int).fetchall()
            conn.close()
            rebake_stats = {"baked": 0, "skipped": 0, "failed": 0}
            updates = []
            for r in rows:
                src = r["source_image_url"] or r["image_url"] or ""
                if not src or "/card_renders/" in src or "/card_suggestions/" in src:
                    rebake_stats["skipped"] += 1; continue
                try:
                    url = composite_card(src, new_rarity, int(r["id"]))
                except Exception as e:
                    print(f"[bulk_update auto-rebake] err {r['id']}: {e}")
                    rebake_stats["failed"] += 1; continue
                if not url:
                    rebake_stats["failed"] += 1; continue
                final = (public_base + url) if public_base else url
                updates.append((final, int(r["id"])))
                rebake_stats["baked"] += 1
            if updates:
                conn = get_db(); c = conn.cursor()
                for final, rid in updates:
                    c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                               (final, rid))
                conn.commit(); conn.close()

        return jsonify({"ok": True, "updated": updated, "applied": clean,
                         "rebake": rebake_stats})


    @app.route("/api/owner/cards/bulk-rebake", methods=["POST"])
    def api_owner_cards_bulk_rebake():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        from services.cards_overlay import composite_card
        import os as _os
        data = request.json or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids vide"}), 400
        try:
            ids_int = [int(x) for x in ids][:5000]
        except (ValueError, TypeError):
            return jsonify({"error": "ids invalides"}), 400
        overlay_rarity = (data.get("overlay_rarity") or "").strip().lower()
        if overlay_rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "overlay_rarity invalide"}), 400

        conn = get_db(); c = conn.cursor()
        placeholders = ",".join("?" * len(ids_int))
        rows = c.execute(
            f"SELECT id, source_image_url, image_url FROM cards "
            f"WHERE id IN ({placeholders})", ids_int).fetchall()
        conn.close()

        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        stats = {"baked": 0, "skipped": 0, "failed": 0}
        updates = []
        for r in rows:
            src = r["source_image_url"] or r["image_url"] or ""
            if not src or "/card_renders/" in src or "/card_suggestions/" in src:
                stats["skipped"] += 1; continue
            try:
                url = composite_card(src, overlay_rarity, int(r["id"]))
            except Exception as e:
                print(f"[bulk_rebake] err {r['id']}: {e}")
                stats["failed"] += 1; continue
            if not url:
                stats["failed"] += 1; continue
            final = (public_base + url) if public_base else url
            updates.append((final, int(r["id"])))
            stats["baked"] += 1

        if updates:
            conn = get_db(); c = conn.cursor()
            for final, rid in updates:
                c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                           (final, rid))
            conn.commit(); conn.close()
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-delete", methods=["POST"])
    def api_owner_cards_bulk_delete():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids vide"}), 400
        try:
            ids_int = [int(x) for x in ids][:5000]
        except (ValueError, TypeError):
            return jsonify({"error": "ids invalides"}), 400
        if not ids_int:
            return jsonify({"error": "ids vide"}), 400
        placeholders = ",".join("?" * len(ids_int))
        conn = get_db(); c = conn.cursor()
        c.execute(f"DELETE FROM user_cards WHERE card_id IN ({placeholders})", ids_int)
        uc_deleted = c.rowcount
        c.execute(f"DELETE FROM cards WHERE id IN ({placeholders})", ids_int)
        deleted = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": deleted,
                         "user_cards_deleted": uc_deleted})


    @app.route("/api/owner/cards/rebalance-by-popularity", methods=["POST"])
    def api_owner_cards_rebalance():
        """Recalcule rarete par quantile de popularite.
        Body: {universes: [...], rebake: bool}
        - Anime : popularite = favoris Anilist (parse description)
        - Films/Série : popularite = ordre d'import (id ASC = top en premier)
        - Jeu Vidéo : NEVER touched (user curated manually)
        Repartition cibles : top 1% mythic, 4% leg, 15% epic, 30% rare, reste common.
        """
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        import re as _re, os as _os
        from database import get_db, CARD_RARITY_WEIGHTS
        from services.cards_overlay import composite_card
        data = request.json or {}
        universes = data.get("universes") or ["Anime", "Film/Série"]
        # Securite : exclure explicitement Jeu Vidéo
        universes = [u for u in universes if u != "Jeu Vidéo"]
        if not universes:
            return jsonify({"error": "aucun univers a rebalance"}), 400
        do_rebake = bool(data.get("rebake", True))

        conn = get_db(); c = conn.cursor()
        global_stats = {"universes": {}, "total_changed": 0, "total_rebaked": 0,
                          "total_rebake_failed": 0}

        for uni in universes:
            rows = c.execute(
                "SELECT id, rarity, description, source_image_url, image_url "
                "FROM cards WHERE universe = ?", (uni,)).fetchall()
            cards = [dict(r) for r in rows]
            if not cards:
                global_stats["universes"][uni] = {"total": 0, "changed": 0}
                continue

            # Score popularite
            def _score(r):
                desc = r.get("description") or ""
                m = _re.search(r"Favoris\s+(?:Anilist|MAL)\s*:\s*([\d,\s]+)", desc)
                if m:
                    s = m.group(1).replace(",", "").replace(" ", "").strip()
                    try: return int(s)
                    except ValueError: return 0
                # Films/Série fallback : id desc (recent = peu prio)
                # mais id asc = early imports = top games. On veut top = popular
                return -int(r["id"])  # plus petit id = plus populaire (early import)

            cards.sort(key=_score, reverse=True)
            n = len(cards)

            # Quantiles d'apres poids /roll : 1% mythic, 4% leg, 15% epic,
            # 30% rare, 50% common
            cuts = {
                "mythic":    int(n * 0.01),
                "legendary": int(n * 0.05),  # 1 + 4
                "epic":      int(n * 0.20),  # 5 + 15
                "rare":      int(n * 0.50),  # 20 + 30
            }
            uni_stats = {"total": n, "changed": 0, "by_rarity": {}}
            updates = []
            for idx, card in enumerate(cards):
                if idx < cuts["mythic"]:        new_rar = "mythic"
                elif idx < cuts["legendary"]:   new_rar = "legendary"
                elif idx < cuts["epic"]:        new_rar = "epic"
                elif idx < cuts["rare"]:        new_rar = "rare"
                else:                            new_rar = "common"
                uni_stats["by_rarity"][new_rar] = uni_stats["by_rarity"].get(new_rar, 0) + 1
                if card["rarity"] != new_rar:
                    updates.append((card, new_rar))

            uni_stats["changed"] = len(updates)
            print(f"[rebalance] {uni}: {n} cards, {len(updates)} change rarete")

            # Applique UPDATE
            for card, new_rar in updates:
                c.execute("UPDATE cards SET rarity = ? WHERE id = ?",
                          (new_rar, card["id"]))
            conn.commit()
            global_stats["universes"][uni] = uni_stats
            global_stats["total_changed"] += len(updates)

            # Rebake overlay parallelise (ThreadPool 10 workers)
            if do_rebake:
                from concurrent.futures import ThreadPoolExecutor
                import threading as _th
                public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                to_bake = []
                for card, new_rar in updates:
                    src = card.get("source_image_url") or card.get("image_url") or ""
                    if not src or "/card_renders/" in src or "/card_suggestions/" in src:
                        continue
                    to_bake.append((card, new_rar))
                lock = _th.Lock(); counter = {"done": 0}
                results = []
                total = len(to_bake)
                def _w(item):
                    card, new_rar = item
                    try:
                        url = composite_card(card.get("source_image_url") or card.get("image_url"),
                                              new_rar, int(card["id"]))
                    except Exception as e:
                        print(f"[rebalance rebake] err {card['id']}: {e}")
                        url = None
                    with lock:
                        counter["done"] += 1
                        if counter["done"] % 200 == 0:
                            pct = counter["done"] * 100 // max(1, total)
                            print(f"[rebalance] {uni} rebake {counter['done']}/{total} ({pct}%)")
                    return (card["id"], url)
                with ThreadPoolExecutor(max_workers=10) as ex:
                    for cid, url in ex.map(_w, to_bake):
                        if url:
                            final = (public_base + url) if public_base else url
                            results.append((final, cid))
                            global_stats["total_rebaked"] += 1
                        else:
                            global_stats["total_rebake_failed"] += 1
                if results:
                    conn2 = get_db(); c2 = conn2.cursor()
                    for final, cid in results:
                        c2.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                                    (final, cid))
                    conn2.commit(); conn2.close()
                print(f"[rebalance] {uni}: rebaked {global_stats['total_rebaked']}")

        conn.close()
        return jsonify({"ok": True, "stats": global_stats})


    @app.route("/api/owner/cards/wipe", methods=["POST"])
    def api_owner_cards_wipe():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        universe = (data.get("universe") or "").strip() or None
        conn = get_db(); c = conn.cursor()
        if universe:
            # Wipe scoped : recupere ids cards, delete user_cards correspondants
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM cards WHERE universe = ?", (universe,)).fetchall()]
            if ids:
                placeholders = ",".join("?" * len(ids))
                c.execute(f"DELETE FROM user_cards WHERE card_id IN ({placeholders})", ids)
                c.execute(f"DELETE FROM cards WHERE id IN ({placeholders})", ids)
            deleted = len(ids)
        else:
            c.execute("DELETE FROM cards")
            deleted = c.rowcount
            c.execute("DELETE FROM user_cards")
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": deleted, "universe": universe})


    @app.route("/api/owner/cards/universes", methods=["GET"])
    def api_owner_cards_universes():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT universe, COUNT(*) AS n FROM cards "
            "WHERE universe IS NOT NULL AND universe != '' "
            "GROUP BY universe ORDER BY n DESC").fetchall()
        conn.close()
        return jsonify({"items": [{"universe": r["universe"], "count": r["n"]} for r in rows]})
