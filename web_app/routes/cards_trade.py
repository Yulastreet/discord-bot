"""Card trading (dashboard): builder with 2 binders, member picker, card selection
and trade creation. Server posting / the view page / counter-offers land in the
following phases."""
from flask import render_template, jsonify, request, session

from services.i18n import t


def register_cards_trade_routes(app, deps):
    def _uid():
        d = session.get("discord") or {}
        return str(d.get("user_id")) if d.get("user_id") else None

    @app.route("/cards/trade", methods=["GET"])
    def cards_trade_page():
        uid = _uid()
        if not uid:
            return render_template("404.html"), 404
        d = session.get("discord") or {}
        return render_template("cards_trade.html", active_nav="cards_trade",
                               me_id=uid, me_name=d.get("username") or "Moi")

    @app.route("/cards/trade/<int:tid>", methods=["GET"])
    def cards_trade_view_page(tid):
        uid = _uid()
        from database import card_trade_get, card_trade_items, get_db
        trade = card_trade_get(tid)
        if not trade:
            return render_template("404.html"), 404
        offer = card_trade_items(tid, side="offer")
        request_items = card_trade_items(tid, side="request")

        import os as _os
        renders_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__)))), "static", "card_renders")

        def _img(cid, image_url):
            for ext in (".webp", ".png"):
                if _os.path.exists(_os.path.join(renders_dir, f"{cid}{ext}")):
                    return f"/static/card_renders/{cid}{ext}"
            return image_url or None

        for it in offer + request_items:
            it["img"] = _img(it["card_id"], it.get("image_url"))

        def _who(user_id):
            conn = get_db(); c = conn.cursor()
            r = c.execute(
                "SELECT username, avatar_url FROM guild_members WHERE user_id = ? "
                "ORDER BY (guild_id = ?) DESC LIMIT 1",
                (str(user_id), str(trade.get("guild_id") or ""))).fetchone()
            conn.close()
            return {"id": str(user_id),
                    "name": (r["username"] if r and r["username"] else t("api.cards_trade.player_fallback")),
                    "avatar": (r["avatar_url"] if r else "")}

        sender = _who(trade["sender_id"])
        receiver = _who(trade["receiver_id"])
        is_part = bool(uid and uid in (str(trade["sender_id"]), str(trade["receiver_id"])))
        return render_template("cards_trade_view.html", active_nav="cards_trade",
                               trade=trade, offer=offer, request_items=request_items,
                               sender=sender, receiver=receiver,
                               is_participant=is_part, me_id=uid, tid=tid)

    @app.route("/api/cards/trade/common-guilds", methods=["GET"])
    def api_cards_trade_common_guilds():
        uid = _uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT g.guild_id, g.name, g.icon_url FROM guilds g "
            "JOIN guild_members gm ON gm.guild_id = g.guild_id "
            "WHERE gm.user_id = ? AND COALESCE(g.active,1)=1 "
            "ORDER BY g.name COLLATE NOCASE", (uid,)).fetchall()
        conn.close()
        return jsonify({"guilds": [{"guild_id": r["guild_id"], "name": r["name"],
                                    "icon": r["icon_url"]} for r in rows]})

    @app.route("/api/cards/trade/members", methods=["GET"])
    def api_cards_trade_members():
        uid = _uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        gid = (request.args.get("guild_id") or "").strip()
        q = (request.args.get("q") or "").strip().lower()
        if not gid:
            return jsonify({"error": t("api.cards_trade.guild_id_required")}), 400
        from database import get_db
        conn = get_db(); c = conn.cursor()
        where = ("guild_id = ? AND COALESCE(is_bot,0)=0 AND user_id != ? "
                 "AND EXISTS (SELECT 1 FROM user_cards uc WHERE uc.user_id = guild_members.user_id)")
        params = [gid, uid]
        if q:
            where += " AND LOWER(username) LIKE ?"
            params.append(f"%{q}%")
        rows = c.execute(
            f"SELECT user_id, username, avatar_url FROM guild_members WHERE {where} "
            f"ORDER BY username COLLATE NOCASE LIMIT 60", params).fetchall()
        conn.close()
        return jsonify({"members": [{"user_id": r["user_id"], "name": r["username"] or t("api.cards_trade.player_fallback"),
                                     "avatar": r["avatar_url"]} for r in rows]})

    @app.route("/api/cards/trade/create", methods=["POST"])
    def api_cards_trade_create():
        uid = _uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        from database import (card_trade_create, user_card_count_owned)
        data = request.json or {}
        receiver = str(data.get("receiver_id") or "").strip()
        gid = str(data.get("guild_id") or "").strip() or None
        offer = [int(x) for x in (data.get("offer") or []) if str(x).isdigit()]
        req = [int(x) for x in (data.get("request") or []) if str(x).isdigit()]
        if not receiver or receiver == uid:
            return jsonify({"error": t("api.cards_trade.pick_another_player")}), 400
        if not offer and not req:
            return jsonify({"error": t("api.cards_trade.pick_at_least_one_card")}), 400
        # Safety: the sender can only offer cards they own, and can only request
        # cards the target actually owns.
        offer = [cid for cid in offer if user_card_count_owned(uid, cid) > 0]
        req = [cid for cid in req if user_card_count_owned(receiver, cid) > 0]
        if not offer and not req:
            return jsonify({"error": t("api.cards_trade.invalid_cards_ownership")}), 400
        tid = card_trade_create(uid, receiver, gid, None,
                                [(cid, 1) for cid in offer], [(cid, 1) for cid in req])
        return jsonify({"ok": True, "trade_id": tid,
                        "link": f"{request.host_url.rstrip('/')}/cards/trade/{tid}"})

    @app.route("/api/cards/trade/preset", methods=["GET"])
    def api_cards_trade_preset():
        """Cards of an existing trade -> builder pre-selection (counter-offer)."""
        uid = _uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        from database import card_trade_get, card_trade_items
        tid = int(request.args.get("trade_id") or 0)
        trade = card_trade_get(tid)
        if not trade:
            return jsonify({"error": t("api.cards_trade.trade_not_found")}), 404
        offer = [it["card_id"] for it in card_trade_items(tid, side="offer")]
        req = [it["card_id"] for it in card_trade_items(tid, side="request")]
        return jsonify({"offer": offer, "request": req,
                        "sender_id": str(trade["sender_id"]),
                        "receiver_id": str(trade["receiver_id"])})

    @app.route("/api/cards/trade/send", methods=["POST"])
    def api_cards_trade_send():
        """Post the trade embed in the guild's cards channel through the bot."""
        uid = _uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        from database import card_trade_get, bot_command_enqueue
        data = request.json or {}
        tid = int(data.get("trade_id") or 0)
        trade = card_trade_get(tid)
        if not trade:
            return jsonify({"error": t("api.cards_trade.trade_not_found")}), 404
        if uid not in (str(trade["sender_id"]), str(trade["receiver_id"])):
            return jsonify({"error": t("api.cards_trade.not_a_participant")}), 403
        gid = trade.get("guild_id")
        if not gid:
            return jsonify({"error": t("api.cards_trade.no_guild_linked")}), 400
        bot_command_enqueue(gid, "post_trade", {"trade_id": tid})
        return jsonify({"ok": True})
