"""Routes owner-only pour Cards Events (drops aleatoires)."""
from flask import render_template, request, jsonify


def register_cards_events_routes(app, deps):
    globals().update(deps)

    @app.route("/owner/cards/events")
    def owner_cards_events_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_card_events.html",
                                 active_nav="owner_card_events")


    @app.route("/api/owner/card-events/configs", methods=["GET"])
    def api_owner_card_events_configs():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT cec.*, g.name AS guild_name "
            "FROM card_event_config cec "
            "LEFT JOIN guilds g ON g.guild_id = cec.guild_id "
            "ORDER BY cec.updated_at DESC").fetchall()
        conn.close()
        return jsonify({"items": [dict(r) for r in rows]})


    @app.route("/api/owner/card-events/guilds", methods=["GET"])
    def api_owner_card_events_guilds():
        """Liste tous les serveurs du bot avec leurs salons textuels."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        bot_obj = deps.get("bot")
        if not bot_obj:
            return jsonify({"items": []})
        out = []
        for g in bot_obj.guilds:
            channels = []
            for ch in g.text_channels:
                try:
                    if ch.permissions_for(g.me).send_messages:
                        channels.append({"id": str(ch.id), "name": ch.name,
                                          "category": ch.category.name if ch.category else None})
                except Exception:
                    pass
            out.append({"id": str(g.id), "name": g.name,
                         "member_count": g.member_count or 0,
                         "channels": channels})
        out.sort(key=lambda x: x["name"].lower())
        return jsonify({"items": out})


    @app.route("/api/owner/card-events/config/<guild_id>", methods=["POST"])
    def api_owner_card_events_save(guild_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_event_config_set
        data = request.json or {}
        fields = {}
        if "channel_id" in data:
            fields["channel_id"] = str(data["channel_id"]) if data["channel_id"] else None
        if "enabled" in data:
            fields["enabled"] = 1 if data["enabled"] else 0
        if "min_interval_min" in data:
            try:
                fields["min_interval_min"] = max(1, int(data["min_interval_min"]))
            except (ValueError, TypeError):
                pass
        if "max_interval_min" in data:
            try:
                fields["max_interval_min"] = max(1, int(data["max_interval_min"]))
            except (ValueError, TypeError):
                pass
        if "min_rarity" in data:
            rar = (data["min_rarity"] or "").strip().lower()
            if rar in ("common", "rare", "epic", "legendary", "mythic"):
                fields["min_rarity"] = rar
        if "reset_next" in data and data["reset_next"]:
            fields["next_drop_at"] = None
        if not fields:
            return jsonify({"error": "rien a update"}), 400
        # Verify min <= max
        cur_min = fields.get("min_interval_min")
        cur_max = fields.get("max_interval_min")
        if cur_min and cur_max and cur_min > cur_max:
            return jsonify({"error": "min_interval_min > max_interval_min"}), 400
        card_event_config_set(guild_id, **fields)
        return jsonify({"ok": True})


    @app.route("/api/owner/card-events/trigger", methods=["POST"])
    def api_owner_card_events_trigger():
        """Trigger manuel d'un drop. Pour tests."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        import asyncio
        from services.card_events import trigger_event_drop
        bot_obj = deps.get("bot")
        if not bot_obj:
            return jsonify({"error": "bot non dispo"}), 500
        data = request.json or {}
        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        min_rarity = (data.get("min_rarity") or "rare").strip().lower()
        if not guild_id or not channel_id:
            return jsonify({"error": "guild_id + channel_id requis"}), 400
        if min_rarity not in ("common", "rare", "epic", "legendary", "mythic"):
            return jsonify({"error": "min_rarity invalide"}), 400
        # Schedule coroutine sur la loop du bot
        try:
            future = asyncio.run_coroutine_threadsafe(
                trigger_event_drop(bot_obj, int(guild_id), int(channel_id),
                                     min_rarity=min_rarity,
                                     triggered_by="manual"),
                bot_obj.loop)
            result = future.result(timeout=15)
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if not result:
            return jsonify({"error": "drop echoue (voir logs bot)"}), 500
        return jsonify({"ok": True, "event_id": result["event_id"],
                         "card_name": result["card"]["name"],
                         "message_id": str(result["message_id"])})


    @app.route("/api/owner/card-events/recent", methods=["GET"])
    def api_owner_card_events_recent():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_event_log_recent
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 200))
        except ValueError:
            limit = 50
        return jsonify({"items": card_event_log_recent(limit=limit)})
