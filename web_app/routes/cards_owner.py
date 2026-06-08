"""Routes owner-only pour gerer le catalogue de cartes."""
from flask import render_template, request, jsonify


def register_cards_owner_routes(app, deps):
    globals().update(deps)

    # ===== PUBLIC =====
    @app.route("/cards")
    def public_cards_page():
        """Page publique : tout le monde peut voir le catalogue (read-only)."""
        return render_template("cards_public.html", active_nav="public_cards")


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
            "rarity_desc":  "CASE rarity WHEN 'mythic' THEN 0 WHEN 'legendary' THEN 1 "
                             "WHEN 'epic' THEN 2 WHEN 'rare' THEN 3 WHEN 'common' THEN 4 "
                             "ELSE 5 END ASC, name ASC",
            "rarity_asc":   "CASE rarity WHEN 'common' THEN 0 WHEN 'rare' THEN 1 "
                             "WHEN 'epic' THEN 2 WHEN 'legendary' THEN 3 WHEN 'mythic' THEN 4 "
                             "ELSE 5 END ASC, name ASC",
            "universe_asc": "universe ASC, name ASC",
            "newest":       "id DESC",
            "oldest":       "id ASC",
        }.get(sort, "name ASC")

        # count filtered
        count_sql = f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}"
        filtered = c.execute(count_sql, params).fetchone()["n"]
        # items
        items_params = params + [per_page, offset]
        rows = c.execute(
            f"SELECT id, name, universe, subtitle, rarity, image_url, source_image_url "
            f"FROM cards WHERE {' AND '.join(where)} "
            f"ORDER BY {sort_sql} LIMIT ? OFFSET ?", items_params).fetchall()
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


    @app.route("/api/public/cards/stats", methods=["GET"])
    def api_public_cards_stats():
        from database import get_db, CARD_RARITY_WEIGHTS
        conn = get_db(); c = conn.cursor()
        total = c.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
        by_rarity = {r["rarity"]: r["n"] for r in c.execute(
            "SELECT rarity, COUNT(*) AS n FROM cards GROUP BY rarity").fetchall()}
        conn.close()
        # Drop rates calculees depuis poids
        total_weight = sum(CARD_RARITY_WEIGHTS.values())
        drop_rates = {k: round(v * 100 / total_weight, 2)
                       for k, v in CARD_RARITY_WEIGHTS.items()}
        return jsonify({
            "total": int(total),
            "by_rarity": by_rarity,
            "drop_rates": drop_rates,
            "cooldown_seconds": 3600,
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
                                card_add)
        from flask import session as _ses
        data = request.json or {}
        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        rarity = (data.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic"):
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
        reviewer_id = _ses.get("user_id") or "owner"
        card_suggestion_review(sid, "approved", reviewer_id, created_card_id=cid)
        return jsonify({"ok": True, "card_id": cid})


    @app.route("/api/owner/card-suggestions/<int:sid>/approve-cropped", methods=["POST"])
    def api_owner_card_suggestion_approve_cropped(sid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (card_suggestion_get, card_suggestion_review,
                                card_add)
        from flask import session as _ses
        import os as _os, io as _io, urllib.request as _ureq
        from PIL import Image as _Img
        data = request.json or {}
        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        rarity = (data.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic"):
            rarity = "common"
        try:
            crop_x = max(0, int(data.get("crop_x", 0)))
            crop_y = max(0, int(data.get("crop_y", 0)))
            crop_w = max(1, int(data.get("crop_w", 1)))
            crop_h = max(1, int(data.get("crop_h", 1)))
        except (ValueError, TypeError):
            return jsonify({"error": "coords crop invalides"}), 400

        src_url = sugg.get("image_url")
        if not src_url:
            return jsonify({"error": "suggestion sans image"}), 400

        # Download source
        try:
            req = _ureq.Request(src_url, headers={
                "User-Agent": "TookBot/1.0 (https://tookbot.click)"})
            with _ureq.urlopen(req, timeout=20) as resp:
                img_data = resp.read()
            src = _Img.open(_io.BytesIO(img_data)).convert("RGBA")
        except Exception as e:
            return jsonify({"error": f"download image : {type(e).__name__}: {e}"}), 500

        # Crop avec coords client (px sur image native)
        sw, sh = src.size
        x0 = min(sw, crop_x)
        y0 = min(sh, crop_y)
        x1 = min(sw, crop_x + crop_w)
        y1 = min(sh, crop_y + crop_h)
        if x1 <= x0 or y1 <= y0:
            return jsonify({"error": "rectangle crop invalide"}), 400
        cropped = src.crop((x0, y0, x1, y1))

        # Save vers static/card_suggestions/<new_card_id>.png
        # On ne connait pas encore l'id, donc on save d'abord vers temp puis rename
        tmp_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__)))), "static", "card_suggestions")
        _os.makedirs(tmp_dir, exist_ok=True)
        # Resize portrait 450x675 (ratio 2:3, meme format que overlays)
        target_w, target_h = 450, 675
        cropped = cropped.resize((target_w, target_h), _Img.LANCZOS)

        # Insert card pour avoir id
        try:
            cid = card_add(
                name=sugg["name"],
                universe=sugg.get("universe"),
                subtitle=sugg.get("subtitle"),
                rarity=rarity,
                image_url=None,  # set apres avec URL finale
                description=f"Suggestion communautaire de {sugg.get('suggester_name', '?')}.",
            )
        except Exception as e:
            return jsonify({"error": f"create card : {type(e).__name__}: {e}"}), 500

        out_path = _os.path.join(tmp_dir, f"{cid}.png")
        try:
            cropped.convert("RGB").save(out_path, "PNG", optimize=True)
        except Exception as e:
            return jsonify({"error": f"save crop : {type(e).__name__}: {e}"}), 500

        # Update image_url
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        # Defaut dashboard.tookbot.click si pas set
        if not public_base:
            public_base = ""
        rel = f"/static/card_suggestions/{cid}.png"
        final_url = (public_base + rel) if public_base else rel
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                  (final_url, src_url, cid))
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
        from database import card_list_all, card_count_filtered, card_count_total
        rarity = request.args.get("rarity") or None
        search = request.args.get("q") or None
        try:
            per_page = max(1, min(int(request.args.get("per_page", 50)), 500))
        except ValueError:
            per_page = 50
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        offset = (page - 1) * per_page
        items = card_list_all(limit=per_page, offset=offset,
                                rarity=rarity, search=search)
        filtered = card_count_filtered(rarity=rarity, search=search)
        total = card_count_total()
        return jsonify({
            "items": items,
            "total": total,
            "filtered": filtered,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (filtered + per_page - 1) // per_page),
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
        if rarity not in ("common", "rare", "epic", "legendary", "mythic"):
            return jsonify({"error": "rarity invalide"}), 400
        cid = card_add(
            name=name,
            universe=(data.get("universe") or "").strip() or None,
            subtitle=(data.get("subtitle") or "").strip() or None,
            rarity=rarity,
            image_url=(data.get("image_url") or "").strip() or None,
            description=(data.get("description") or "").strip() or None,
        )
        return jsonify({"ok": True, "id": cid})


    @app.route("/api/owner/cards/<int:cid>", methods=["PATCH"])
    def api_owner_cards_update(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        allowed = {"name", "universe", "subtitle", "rarity", "image_url", "description"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "rien a update"}), 400
        if "rarity" in fields and fields["rarity"] not in ("common", "rare", "epic", "legendary", "mythic"):
            return jsonify({"error": "rarity invalide"}), 400
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
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        from services.cards_overlay import composite_card
        import os as _os, io as _io, urllib.request as _ureq
        from PIL import Image as _Img
        data = request.json or {}
        try:
            crop_x = max(0, int(data.get("crop_x", 0)))
            crop_y = max(0, int(data.get("crop_y", 0)))
            crop_w = max(1, int(data.get("crop_w", 1)))
            crop_h = max(1, int(data.get("crop_h", 1)))
        except (ValueError, TypeError):
            return jsonify({"error": "coords crop invalides"}), 400
        apply_overlay = bool(data.get("apply_overlay", True))

        conn = get_db(); c = conn.cursor()
        row = c.execute("SELECT id, name, rarity, source_image_url, image_url "
                         "FROM cards WHERE id = ?", (int(cid),)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "carte introuvable"}), 404
        src_url = row["source_image_url"] or row["image_url"] or ""
        conn.close()
        if not src_url:
            return jsonify({"error": "pas de source image"}), 400

        # Download source
        try:
            req = _ureq.Request(src_url, headers={
                "User-Agent": "TookBot/1.0 (https://tookbot.click)"})
            with _ureq.urlopen(req, timeout=20) as resp:
                img_data = resp.read()
            src = _Img.open(_io.BytesIO(img_data)).convert("RGBA")
        except Exception as e:
            return jsonify({"error": f"download : {type(e).__name__}: {e}"}), 500

        sw, sh = src.size
        x0 = min(sw, crop_x); y0 = min(sh, crop_y)
        x1 = min(sw, crop_x + crop_w); y1 = min(sh, crop_y + crop_h)
        if x1 <= x0 or y1 <= y0:
            return jsonify({"error": "rectangle invalide"}), 400
        cropped = src.crop((x0, y0, x1, y1))

        # Save cropped vers temp location (sera source d'overlay)
        from services.cards_overlay import _OUTPUT_DIR
        _os.makedirs(_OUTPUT_DIR, exist_ok=True)
        if apply_overlay:
            # Save crop temporaire sur disk pour que composite_card puisse le re-read
            tmp_path = _os.path.join(_OUTPUT_DIR, f"_recrop_{cid}_tmp.png")
            cropped.save(tmp_path, "PNG")
            # composite_card download via http, donc on a besoin URL local
            # Plus simple : composite manuel inline
            from services.cards_overlay import _CARD_W, _CARD_H, _get_overlay
            cw, ch = cropped.size
            target_ratio = _CARD_W / _CARD_H
            src_ratio = cw / ch
            if src_ratio > target_ratio:
                new_h = _CARD_H; new_w = int(cw * new_h / ch)
                resized = cropped.resize((new_w, new_h), _Img.LANCZOS)
                x0c = (new_w - _CARD_W) // 2
                resized = resized.crop((x0c, 0, x0c + _CARD_W, _CARD_H))
            else:
                new_w = _CARD_W; new_h = int(ch * new_w / cw)
                resized = cropped.resize((new_w, new_h), _Img.LANCZOS)
                y0c = (new_h - _CARD_H) // 2
                resized = resized.crop((0, y0c, _CARD_W, y0c + _CARD_H))
            overlay = _get_overlay(row["rarity"])
            canvas = _Img.new("RGBA", (_CARD_W, _CARD_H), (26, 26, 26, 255))
            canvas.paste(resized, (0, 0), resized)
            if overlay is not None:
                canvas = _Img.alpha_composite(canvas, overlay)
            out_path = _os.path.join(_OUTPUT_DIR, f"{cid}.png")
            canvas.convert("RGB").save(out_path, "PNG", optimize=True)
            try: _os.remove(tmp_path)
            except Exception: pass
        else:
            cropped = cropped.resize((450, 675), _Img.LANCZOS)
            out_path = _os.path.join(_OUTPUT_DIR, f"{cid}.png")
            cropped.convert("RGB").save(out_path, "PNG", optimize=True)

        rel = f"/static/card_renders/{cid}.png"
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        final = (public_base + rel) if public_base else rel
        # Save source si pas deja
        conn = get_db(); c = conn.cursor()
        if not row["source_image_url"]:
            c.execute("UPDATE cards SET source_image_url = ? WHERE id = ?",
                       (src_url, cid))
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
        if overlay_rarity not in ("common", "rare", "epic", "legendary", "mythic"):
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
