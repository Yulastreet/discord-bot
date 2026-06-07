"""Routes owner-only pour gerer le catalogue de cartes."""
from flask import render_template, request, jsonify


def register_cards_owner_routes(app, deps):
    globals().update(deps)

    @app.route("/owner/cards")
    def owner_cards_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_cards.html", active_nav="owner_cards")


    @app.route("/api/owner/cards", methods=["GET"])
    def api_owner_cards_list():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_list_all
        rarity = request.args.get("rarity") or None
        search = request.args.get("q") or None
        try:
            limit = max(1, min(int(request.args.get("limit", 500)), 5000))
        except ValueError:
            limit = 500
        return jsonify({
            "items": card_list_all(limit=limit, rarity=rarity, search=search),
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


    @app.route("/api/owner/cards/wipe", methods=["POST"])
    def api_owner_cards_wipe():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("DELETE FROM cards")
        c.execute("DELETE FROM user_cards")
        deleted = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": deleted})
