"""Owner-only routes for Cards Events (random drops)."""
from flask import render_template, request, jsonify

from services.i18n import t


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
        """List every server of the bot (DB) along with its channels (cache)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import list_guilds, get_db, guild_setting_get
        import json as _json
        guilds = list_guilds(active_only=True)
        # Channel cache loaded from guild_channels_cache when available
        conn = get_db(); c = conn.cursor()
        try:
            c.execute("CREATE TABLE IF NOT EXISTS guild_channels_cache ("
                      "guild_id TEXT PRIMARY KEY, channels_json TEXT, "
                      "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            rows = c.execute("SELECT guild_id, channels_json FROM guild_channels_cache").fetchall()
            channels_by_guild = {}
            for r in rows:
                try:
                    channels_by_guild[r["guild_id"]] = _json.loads(r["channels_json"] or "[]")
                except Exception:
                    channels_by_guild[r["guild_id"]] = []
        finally:
            conn.close()
        out = []
        for g in guilds:
            gid = str(g.get("guild_id") or g.get("id"))
            out.append({
                "id": gid,
                "name": g.get("name") or "?",
                "member_count": g.get("member_count") or 0,
                "channels": channels_by_guild.get(gid, []),
                # Feature Cards Events activee sur ce serveur ?
                "events_enabled": guild_setting_get(gid, "card_events", "0") == "1",
            })
        out.sort(key=lambda x: x["name"].lower())
        return jsonify({"items": out})


    @app.route("/api/owner/card-events/refresh-channels/<guild_id>", methods=["POST"])
    def api_owner_card_events_refresh_channels(guild_id):
        """Push a bot command to refresh the channel cache of a guild."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import bot_command_enqueue
        bot_command_enqueue(guild_id, "list_guild_channels", {})
        return jsonify({"ok": True, "note": t("api.cards_events.channels_refreshing")})


    # ===== Config GLOBALE (s'applique a tous les serveurs feature ON) =====
    @app.route("/api/owner/card-events/global-config", methods=["GET"])
    def api_owner_card_events_global_get():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.card_events import global_event_config
        return jsonify(global_event_config())

    @app.route("/api/owner/card-events/global-config", methods=["POST"])
    def api_owner_card_events_global_set():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import set_setting
        data = request.json or {}
        try:
            mn = max(1, int(data.get("min_interval_min", 300)))
            mx = max(1, int(data.get("max_interval_min", 600)))
        except (ValueError, TypeError):
            return jsonify({"error": t("api.cards_events.invalid_intervals")}), 400
        if mn > mx:
            return jsonify({"error": "min > max"}), 400
        rar = (data.get("min_rarity") or "rare").strip().lower()
        if rar not in ("common", "rare", "epic", "legendary", "mythic"):
            return jsonify({"error": t("api.cards_events.invalid_rarity")}), 400
        set_setting("card_event_interval_min", mn)
        set_setting("card_event_interval_max", mx)
        set_setting("card_event_min_rarity", rar)
        set_setting("card_event_enabled", "1" if data.get("enabled", True) else "0")
        return jsonify({"ok": True})

    @app.route("/api/owner/card-events/enabled-guilds", methods=["GET"])
    def api_owner_card_events_enabled_guilds():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import list_guilds, guild_setting_get
        out = []
        for g in list_guilds(active_only=True):
            gid = str(g.get("guild_id") or g.get("id"))
            if guild_setting_get(gid, "card_events", "0") == "1":
                out.append({"id": gid, "name": g.get("name") or "?",
                            "member_count": g.get("member_count") or 0})
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
            return jsonify({"error": t("api.cards_events.nothing_to_update")}), 400
        # Verify min <= max
        cur_min = fields.get("min_interval_min")
        cur_max = fields.get("max_interval_min")
        if cur_min and cur_max and cur_min > cur_max:
            return jsonify({"error": "min_interval_min > max_interval_min"}), 400
        card_event_config_set(guild_id, **fields)
        return jsonify({"ok": True})


    @app.route("/api/owner/card-events/trigger", methods=["POST"])
    def api_owner_card_events_trigger():
        """Manual drop trigger through the bot_command queue (cross-process)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import bot_command_enqueue
        data = request.json or {}
        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        min_rarity = (data.get("min_rarity") or "rare").strip().lower()
        exact_rarity = bool(data.get("exact_rarity"))
        card_id = data.get("card_id") or None
        if not guild_id or not channel_id:
            return jsonify({"error": t("api.cards_events.guild_and_channel_required")}), 400
        # Specific card: just check that it exists (rarity is ignored)
        if card_id:
            from database import card_get
            try:
                card_id = int(card_id)
            except (ValueError, TypeError):
                return jsonify({"error": t("api.cards_events.invalid_card_id")}), 400
            if not card_get(card_id):
                return jsonify({"error": t("api.cards_events.card_not_found")}), 404
        else:
            # secret is allowed only as an EXACT rarity (never as a minimum floor)
            valid = ("common", "rare", "epic", "legendary", "mythic") + (("secret",) if exact_rarity else ())
            if min_rarity not in valid:
                return jsonify({"error": t("api.cards_events.invalid_rarity")}), 400
        bot_command_enqueue(guild_id, "card_event_drop", {
            "channel_id": str(channel_id),
            "min_rarity": min_rarity,
            "exact_rarity": exact_rarity,
            "card_id": card_id,
        })
        return jsonify({"ok": True, "note": t("api.cards_events.drop_dispatched")})


    @app.route("/api/owner/card-events/spawn-boss", methods=["POST"])
    def api_owner_spawn_boss():
        """Spawn a combat boss through the bot_command queue (cross-process)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import bot_command_enqueue
        data = request.json or {}
        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        try:
            tier = max(1, min(5, int(data.get("tier") or 1)))
        except (ValueError, TypeError):
            return jsonify({"error": t("api.cards_events.invalid_tier")}), 400
        if not guild_id or not channel_id:
            return jsonify({"error": t("api.cards_events.guild_and_channel_required")}), 400
        element = (data.get("element") or "").strip().lower() or None
        if element and element not in ("eclat", "abysse", "fracture", "vif", "neant"):
            return jsonify({"error": t("api.cards_events.invalid_element")}), 400
        rarity = (data.get("rarity") or "").strip().lower() or None
        if rarity and rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": t("api.cards_events.invalid_rarity")}), 400
        bot_command_enqueue(guild_id, "boss_spawn", {
            "channel_id": str(channel_id),
            "tier": tier,
            "element": element,
            "rarity": rarity,
        })
        suffix = t("api.cards_events.boss_suffix_element", element=element) if element else ""
        if rarity:
            suffix += t("api.cards_events.boss_suffix_rarity", rarity=rarity)
        return jsonify({"ok": True, "note": t("api.cards_events.boss_dispatched",
                                              tier=tier, suffix=suffix)})


    @app.route("/api/owner/card-events/simulate-roll", methods=["POST"])
    def api_owner_simulate_roll():
        """Fake test roll: posts the /roll layout + pings wishers, without giving anything."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import bot_command_enqueue, card_get
        data = request.json or {}
        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        card_id = data.get("card_id")
        if not guild_id or not channel_id or not card_id:
            return jsonify({"error": t("api.cards_events.guild_channel_card_required")}), 400
        try:
            card_id = int(card_id)
        except (ValueError, TypeError):
            return jsonify({"error": t("api.cards_events.invalid_card_id")}), 400
        card = card_get(card_id)
        if not card:
            return jsonify({"error": t("api.cards_events.card_not_found")}), 404
        bot_command_enqueue(guild_id, "simulate_roll", {
            "channel_id": str(channel_id),
            "card_id": card_id,
        })
        return jsonify({"ok": True, "note": t("api.cards_events.simulated_roll_dispatched",
                                              card=card["name"])})


    @app.route("/api/owner/card-events/fake-drop", methods=["POST"])
    def api_owner_fake_drop():
        """Fake drop (troll): normal drop, but no card is given on claim."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import bot_command_enqueue, card_get
        data = request.json or {}
        guild_id = data.get("guild_id")
        channel_id = data.get("channel_id")
        card_id = data.get("card_id")
        if not guild_id or not channel_id or not card_id:
            return jsonify({"error": t("api.cards_events.guild_channel_card_required")}), 400
        try:
            card_id = int(card_id)
        except (ValueError, TypeError):
            return jsonify({"error": t("api.cards_events.invalid_card_id")}), 400
        card = card_get(card_id)
        if not card:
            return jsonify({"error": t("api.cards_events.card_not_found")}), 404
        bot_command_enqueue(guild_id, "fake_drop", {
            "channel_id": str(channel_id),
            "card_id": card_id,
        })
        return jsonify({"ok": True, "note": t("api.cards_events.fake_drop_dispatched",
                                              card=card["name"])})


    @app.route("/api/owner/card-events/wheel/reset", methods=["POST"])
    def api_owner_wheel_reset():
        """Owner: reset today's lucky wheel for everyone."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import wheel_reset_all
        n = wheel_reset_all()
        return jsonify({"ok": True, "cleared": n})

    @app.route("/api/owner/card-events/wheel/reset-wins", methods=["POST"])
    def api_owner_wheel_reset_wins():
        """Owner: clear the wheel win log (the live feed)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import wheel_wins_reset
        n = wheel_wins_reset()
        return jsonify({"ok": True, "cleared": n})


    # ===== Gestion des rolls (reset / give) =====
    @app.route("/api/owner/card-events/rolls/status", methods=["GET"])
    def api_owner_rolls_status():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_setting, get_db
        grant = int(get_setting("roll_global_grant", "0") or 0)
        conn = get_db(); c = conn.cursor()
        active = c.execute("SELECT COUNT(*) AS n FROM roll_events "
                           "WHERE rolled_at > ?",
                           (__import__("time").time() - 3600,)).fetchone()["n"]
        conn.close()
        return jsonify({"global_grant": grant, "rolls_last_hour": int(active)})

    @app.route("/api/owner/card-events/rolls/reset", methods=["POST"])
    def api_owner_rolls_reset():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import roll_events_reset_all
        n = roll_events_reset_all()
        return jsonify({"ok": True, "cleared": n})

    @app.route("/api/owner/card-events/rolls/give", methods=["POST"])
    def api_owner_rolls_give():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import roll_grant_give_all
        data = request.json or {}
        try:
            n = int(data.get("n", 0))
        except (ValueError, TypeError):
            n = 0
        if n <= 0 or n > 1000:
            return jsonify({"error": "n entre 1 et 1000"}), 400
        new_grant = roll_grant_give_all(n)
        return jsonify({"ok": True, "global_grant": new_grant})

    @app.route("/api/owner/card-events/rolls/reset-grant", methods=["POST"])
    def api_owner_rolls_reset_grant():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import roll_grant_reset
        roll_grant_reset()
        return jsonify({"ok": True})

    # ===== Test sur UN utilisateur =====
    @app.route("/api/owner/card-events/rolls/user-status", methods=["GET"])
    def api_owner_rolls_user_status():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import roll_bonus_available, get_db
        uid = (request.args.get("user_id") or "").strip()
        if not uid:
            return jsonify({"error": t("api.cards_events.user_id_required")}), 400
        conn = get_db(); c = conn.cursor()
        recent = c.execute("SELECT COUNT(*) AS n FROM roll_events "
                           "WHERE user_id = ? AND rolled_at > ?",
                           (uid, __import__("time").time() - 3600)).fetchone()["n"]
        conn.close()
        return jsonify({"user_id": uid, "bonus_available": roll_bonus_available(uid),
                        "rolls_last_hour": int(recent)})

    @app.route("/api/owner/card-events/rolls/give-user", methods=["POST"])
    def api_owner_rolls_give_user():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import roll_give_user
        data = request.json or {}
        uid = str(data.get("user_id") or "").strip()
        try:
            n = int(data.get("n", 0))
        except (ValueError, TypeError):
            n = 0
        if not uid or n <= 0 or n > 1000:
            return jsonify({"error": t("api.cards_events.user_id_and_n_required")}), 400
        avail = roll_give_user(uid, n)
        return jsonify({"ok": True, "bonus_available": avail})

    # ===== Give golden / epic rolls (item d'inventaire) =====
    _GIVE_ITEMS = {"golden_roll", "epic_roll"}

    @app.route("/api/owner/card-events/items/give-user", methods=["POST"])
    def api_owner_items_give_user():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import user_item_add, user_item_get
        data = request.json or {}
        uid = str(data.get("user_id") or "").strip()
        item = str(data.get("item") or "").strip()
        try:
            n = int(data.get("n", 0))
        except (ValueError, TypeError):
            n = 0
        if not uid or item not in _GIVE_ITEMS or n <= 0 or n > 1000:
            return jsonify({"error": t("api.cards_events.user_id_item_n_required")}), 400
        user_item_add(uid, item, n)
        return jsonify({"ok": True, "total": user_item_get(uid, item)})

    @app.route("/api/owner/card-events/items/give-all", methods=["POST"])
    def api_owner_items_give_all():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import user_item_add, get_db
        data = request.json or {}
        item = str(data.get("item") or "").strip()
        try:
            n = int(data.get("n", 0))
        except (ValueError, TypeError):
            n = 0
        if item not in _GIVE_ITEMS or n <= 0 or n > 1000:
            return jsonify({"error": t("api.cards_events.item_and_n_required")}), 400
        conn = get_db(); c = conn.cursor()
        rows = c.execute("SELECT DISTINCT user_id FROM user_cards").fetchall()
        conn.close()
        uids = [str(r["user_id"]) for r in rows]
        for uid in uids:
            user_item_add(uid, item, n)
        return jsonify({"ok": True, "users": len(uids)})

    @app.route("/api/owner/card-events/rolls/reset-user", methods=["POST"])
    def api_owner_rolls_reset_user():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import roll_reset_user_cooldown, roll_reset_user_grant
        data = request.json or {}
        uid = str(data.get("user_id") or "").strip()
        if not uid:
            return jsonify({"error": t("api.cards_events.user_id_required")}), 400
        cleared = roll_reset_user_cooldown(uid)
        if data.get("also_grant"):
            roll_reset_user_grant(uid)
        return jsonify({"ok": True, "cleared": cleared})

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
