"""Routes owner-only : Card Shop (config 6 slots) + bordures (placement)."""
from flask import render_template, request, jsonify


def register_cards_shop_routes(app, deps):
    globals().update(deps)

    @app.route("/owner/cards/shop")
    def owner_cards_shop_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_card_shop.html",
                                 active_nav="owner_card_shop")

    # ===== SLOTS SHOP =====
    @app.route("/api/owner/card-shop/slots", methods=["GET"])
    def api_owner_card_shop_slots():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_shop_get_slots, card_get, border_get
        from services.card_shop import suggested_price
        slots = card_shop_get_slots()
        # Enrichit avec nom item + prix suggere
        for s in slots:
            s["item_name"] = None
            s["item_rarity"] = None
            if s.get("item_type") == "card" and s.get("item_ref"):
                try:
                    card = card_get(int(s["item_ref"]))
                    if card:
                        s["item_name"] = card.get("name")
                        s["item_image"] = card.get("image_url")
                        s["item_rarity"] = card.get("rarity")
                except (ValueError, TypeError):
                    pass
            elif s.get("item_type") == "border" and s.get("item_ref"):
                b = border_get(s["item_ref"])
                if b:
                    s["item_name"] = b.get("name")
            s["suggested_price"] = suggested_price(s.get("item_type"), s.get("item_ref"))
        return jsonify({"items": slots})

    @app.route("/api/owner/card-shop/slot/<int:slot>", methods=["POST"])
    def api_owner_card_shop_save_slot(slot):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_shop_set_slot
        if slot < 1 or slot > 6:
            return jsonify({"error": "slot 1-6"}), 400
        data = request.json or {}
        fields = {}
        if "item_type" in data:
            it = (data["item_type"] or "").strip().lower()
            fields["item_type"] = it if it in ("card", "border") else None
        if "item_ref" in data:
            fields["item_ref"] = str(data["item_ref"]).strip() if data["item_ref"] else None
        if "price" in data:
            try:
                fields["price"] = max(0, int(data["price"]))
            except (ValueError, TypeError):
                fields["price"] = 0
        # Si prix absent ou 0 : auto-remplit avec le prix suggere
        if fields.get("price", 0) <= 0 and fields.get("item_type") and fields.get("item_ref"):
            from services.card_shop import suggested_price
            sp = suggested_price(fields["item_type"], fields["item_ref"])
            if sp > 0:
                fields["price"] = sp
        if "label" in data:
            fields["label"] = (data["label"] or "").strip()[:60] or None
        if "subtitle" in data:
            fields["subtitle"] = (data["subtitle"] or "").strip()[:80] or None
        if "enabled" in data:
            fields["enabled"] = 1 if data["enabled"] else 0
        card_shop_set_slot(slot, **fields)
        return jsonify({"ok": True})

    @app.route("/api/owner/card-shop/preview", methods=["POST"])
    def api_owner_card_shop_preview():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.card_shop import build_shop_image
        import time as _t
        rel = build_shop_image(out_name="shop_preview.png")
        if not rel:
            return jsonify({"error": "génération échouée (bg manquant ?)"}), 500
        # cache-bust
        return jsonify({"ok": True, "url": f"{rel}?t={int(_t.time())}"})

    @app.route("/api/owner/card-shop/cards-search", methods=["GET"])
    def api_owner_card_shop_cards_search():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_list_all
        q = (request.args.get("q") or "").strip()
        rows = card_list_all(limit=30, search=q or None)
        return jsonify({"items": [
            {"id": r["id"], "name": r["name"], "rarity": r.get("rarity"),
             "universe": r.get("universe")} for r in rows]})

    # ===== BORDURES (placement) =====
    @app.route("/api/owner/borders", methods=["GET"])
    def api_owner_borders():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import borders_list
        return jsonify({"items": borders_list()})

    @app.route("/api/owner/borders/<border_key>", methods=["POST"])
    def api_owner_border_save(border_key):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import border_set_config
        data = request.json or {}
        kwargs = {}
        for k in ("offset_x", "offset_y", "scale_pct", "card_scale_pct", "enabled"):
            if k in data:
                try:
                    kwargs[k] = int(data[k])
                except (ValueError, TypeError):
                    pass
        if "name" in data and data["name"]:
            kwargs["name"] = str(data["name"]).strip()[:60]
        border_set_config(border_key, **kwargs)
        return jsonify({"ok": True})

    @app.route("/api/owner/borders/<border_key>/preview", methods=["POST"])
    def api_owner_border_preview(border_key):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import border_get
        from services.card_render import render_border_preview_file
        import time as _t
        b = border_get(border_key)
        if not b:
            return jsonify({"error": "bordure introuvable"}), 404
        data = request.json or {}
        ox = data.get("offset_x", b.get("offset_x", 0))
        oy = data.get("offset_y", b.get("offset_y", 0))
        sc = data.get("scale_pct", b.get("scale_pct", 100))
        csc = data.get("card_scale_pct", b.get("card_scale_pct", 100))
        placeholder = data.get("placeholder_card_id")
        try:
            placeholder = int(placeholder) if placeholder else None
        except (ValueError, TypeError):
            placeholder = None
        rel = render_border_preview_file(
            border_key, b["filename"],
            offset_x=int(ox), offset_y=int(oy), scale_pct=int(sc),
            card_scale_pct=int(csc), placeholder_card_id=placeholder)
        if not rel:
            return jsonify({"error": "génération échouée"}), 500
        return jsonify({"ok": True, "url": f"{rel}?t={int(_t.time())}"})
