"""Routes owner-only pour gerer le catalogue de cartes."""
from flask import render_template, request, jsonify


def register_cards_owner_routes(app, deps):
    globals().update(deps)

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
