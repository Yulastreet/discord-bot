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

    @app.route("/owner/cards/cosmetics")
    def owner_cards_cosmetics_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_card_cosmetics.html",
                                 active_nav="owner_card_cosmetics")

    # ===== COSMETICS (bordures) : liste + give/remove =====
    @app.route("/api/owner/cosmetics", methods=["GET"])
    def api_owner_cosmetics():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import borders_list, get_db
        from services.card_render import render_border_preview_file
        import time as _t
        borders = borders_list()
        # Stats : combien de copies en circulation + combien equipees
        conn = get_db(); c = conn.cursor()
        for b in borders:
            k = b["border_key"]
            stock = c.execute("SELECT COALESCE(SUM(qty),0) AS n FROM user_borders WHERE border_key = ?",
                              (k,)).fetchone()["n"]
            equipped = c.execute("SELECT COUNT(*) AS n FROM card_customizations WHERE border_key = ?",
                                 (k,)).fetchone()["n"]
            b["stock_total"] = int(stock or 0)
            b["equipped_total"] = int(equipped or 0)
            # Regenere la preview avec la config actuelle (evite cache obsolete)
            try:
                render_border_preview_file(
                    k, b["filename"],
                    offset_x=b.get("offset_x", 0), offset_y=b.get("offset_y", 0),
                    scale_pct=b.get("scale_pct", 100),
                    card_scale_pct=b.get("card_scale_pct", 100))
            except Exception:
                pass
            b["preview_url"] = f"/static/card_customs/_preview_{k}.png?t={int(_t.time())}"
        conn.close()
        return jsonify({"items": borders})

    @app.route("/api/owner/cosmetics/user/<user_id>", methods=["GET"])
    def api_owner_cosmetics_user(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import user_borders_list, get_db
        inv = user_borders_list(user_id)
        # Bordures equipees (sur des cartes)
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT cc.card_id, cc.border_key, b.name AS border_name, ca.name AS card_name "
            "FROM card_customizations cc "
            "LEFT JOIN borders b ON b.border_key = cc.border_key "
            "LEFT JOIN cards ca ON ca.id = cc.card_id "
            "WHERE cc.user_id = ? AND cc.border_key IS NOT NULL",
            (str(user_id),)).fetchall()
        conn.close()
        return jsonify({"inventory": inv, "equipped": [dict(r) for r in rows]})

    @app.route("/api/owner/cosmetics/give", methods=["POST"])
    def api_owner_cosmetics_give():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import user_border_add, border_get
        data = request.json or {}
        user_id = str(data.get("user_id") or "").strip()
        border_key = str(data.get("border_key") or "").strip()
        try:
            qty = max(1, min(int(data.get("qty", 1)), 100))
        except (ValueError, TypeError):
            qty = 1
        if not user_id or not border_key:
            return jsonify({"error": "user_id + border_key requis"}), 400
        if not border_get(border_key):
            return jsonify({"error": "bordure introuvable"}), 404
        user_border_add(user_id, border_key, qty=qty)
        return jsonify({"ok": True, "given": qty})

    @app.route("/api/owner/cosmetics/remove", methods=["POST"])
    def api_owner_cosmetics_remove():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import user_border_remove
        data = request.json or {}
        user_id = str(data.get("user_id") or "").strip()
        border_key = str(data.get("border_key") or "").strip()
        if not user_id or not border_key:
            return jsonify({"error": "user_id + border_key requis"}), 400
        if data.get("all"):
            user_border_remove(user_id, border_key, qty=None)
        else:
            try:
                qty = max(1, int(data.get("qty", 1)))
            except (ValueError, TypeError):
                qty = 1
            user_border_remove(user_id, border_key, qty=qty)
        return jsonify({"ok": True})

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
        from database import card_get_by_name
        from services.card_shop import suggested_price
        data = request.json or {}
        fields = {}
        # Type (vide autorise pour vider le slot)
        it = (data.get("item_type") or "").strip().lower()
        fields["item_type"] = it if it in ("card", "border") else None
        # Reference item
        ref = str(data.get("item_ref") or "").strip()
        # Carte : resout l'id par nom tapé si fourni (fiable sans clic autocomplete)
        if fields["item_type"] == "card":
            q = (data.get("item_query") or "").strip()
            if q:
                card = card_get_by_name(q)
                ref = str(card["id"]) if card else ref
        fields["item_ref"] = ref or None
        # Si type/ref vide -> slot vidé : on desactive et reset
        if not fields["item_type"] or not fields["item_ref"]:
            fields["item_type"] = None
            fields["item_ref"] = None
        # Prix
        try:
            price = max(0, int(data.get("price") or 0))
        except (ValueError, TypeError):
            price = 0
        if price <= 0 and fields["item_type"] and fields["item_ref"]:
            sp = suggested_price(fields["item_type"], fields["item_ref"])
            if sp > 0:
                price = sp
        fields["price"] = price
        # Label (vide -> NULL)
        fields["label"] = (data.get("label") or "").strip()[:60] or None
        # Enabled (jamais actif si slot vide)
        enabled = 1 if data.get("enabled") else 0
        if not fields["item_type"] or not fields["item_ref"]:
            enabled = 0
        fields["enabled"] = enabled
        card_shop_set_slot(slot, **fields)
        return jsonify({"ok": True})

    @app.route("/api/owner/card-shop/shuffle", methods=["POST"])
    def api_owner_card_shop_shuffle():
        """Remplit aleatoirement les 6 slots. Garantit AU MOINS : 1 bordure +
        1 carte legendary OU mythic. Le reste = cartes aleatoires obtenables."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db, card_shop_set_slot, borders_list
        from services.card_shop import suggested_price
        import random as _r
        conn = get_db(); c = conn.cursor()
        # 1 bordure (priorite aux activees)
        bl = borders_list()
        enabled_b = [b for b in bl if b.get("enabled", 1)]
        pool_b = enabled_b or bl
        bkey = _r.choice(pool_b)["border_key"] if pool_b else None
        # 1 carte legendary/mythic obtenable
        lm = c.execute("SELECT id FROM cards WHERE rarity IN ('legendary','mythic') "
                       "AND COALESCE(not_obtainable,0)=0 ORDER BY RANDOM() LIMIT 1").fetchone()
        leg_id = lm["id"] if lm else None
        # cartes de remplissage (distinctes de la legendary/mythic deja prise)
        need = 6 - (1 if bkey else 0) - (1 if leg_id else 0)
        fillers = []
        if need > 0:
            fillers = c.execute(
                "SELECT id FROM cards WHERE COALESCE(not_obtainable,0)=0 AND id != ? "
                "ORDER BY RANDOM() LIMIT ?", (leg_id if leg_id else -1, need)).fetchall()
        conn.close()
        items = []
        if bkey:
            items.append(("border", str(bkey)))
        if leg_id:
            items.append(("card", str(leg_id)))
        for f in fillers:
            items.append(("card", str(f["id"])))
        _r.shuffle(items)  # melange l'ordre des slots
        for i in range(1, 7):
            if i - 1 < len(items):
                it, ref = items[i - 1]
                price = suggested_price(it, ref)
                card_shop_set_slot(i, item_type=it, item_ref=ref,
                                   price=price, label=None, enabled=1)
            else:
                card_shop_set_slot(i, item_type=None, item_ref=None,
                                   price=0, label=None, enabled=0)
        return jsonify({"ok": True, "count": len(items),
                        "has_border": bool(bkey), "has_legmyth": bool(leg_id)})

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
