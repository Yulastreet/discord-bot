from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

from services.i18n import t

def register_server_tool_routes(app, deps):
    globals().update(deps)
    @app.route("/tickets")
    def tickets_page():
        return render_template("tickets.html", active_nav="tickets")


    @app.route("/api/tickets/panels", methods=["GET"])
    def api_tickets_panels_list():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        return jsonify({"panels": ticket_panels_list(g_id)})


    @app.route("/api/tickets/panels/<int:pid>", methods=["DELETE"])
    def api_tickets_panel_delete(pid):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        panel = ticket_panel_get(pid)
        if not panel or str(panel["guild_id"]) != str(g_id):
            return jsonify({"error": "not_found"}), 404
        n = ticket_panel_delete(pid, guild_id=g_id)
        return jsonify({"ok": True, "deleted": n})


    @app.route("/api/tickets", methods=["GET"])
    def api_tickets_list():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        status = request.args.get("status") or None
        return jsonify({"tickets": tickets_list(g_id, status=status, limit=100)})


    @app.route("/api/tickets/<int:ticket_id>/close", methods=["POST"])
    def api_tickets_close(ticket_id):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        ticket_set_status(ticket_id, "closed", closed_by=_current_user_id())
        return jsonify({"ok": True})


    # ===== Social Alerts dashboard =====

    @app.route("/social-alerts")
    def social_alerts_page():
        return render_template("social_alerts.html", active_nav="social_alerts")


    @app.route("/api/social-alerts", methods=["GET"])
    def api_social_alerts_list():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        return jsonify({"alerts": social_alerts_list(guild_id=g_id)})


    @app.route("/api/social-alerts", methods=["POST"])
    def api_social_alerts_create():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.get_json(silent=True) or {}
        plat = (data.get("platform") or "").strip()
        raw = (data.get("target_id") or "").strip()
        channel_id = data.get("channel_id")
        message = (data.get("message_template") or "").strip() or None

        if plat not in ("twitch", "youtube", "reddit"):
            return jsonify({"error": t("api.server_tools.invalid_platform")}), 400
        if not raw or not channel_id:
            return jsonify({"error": t("api.server_tools.link_and_channel_required")}), 400

        parsed = social.parse_social_url(plat, raw)
        if not parsed:
            examples = {
                "twitch":  "https://twitch.tv/<pseudo>",
                "youtube": "https://youtube.com/@<handle> or /channel/UC...",
                "reddit":  "https://reddit.com/r/<sub> or /user/<u>",
            }
            return jsonify({"error": t("api.server_tools.invalid_link", example=examples[plat])}), 400
        target, label = parsed

        aid = social_alert_create(
            guild_id=g_id, platform=plat, target_id=target, target_label=label,
            channel_id=channel_id, message_template=message,
            created_by=_current_user_id(),
        )
        return jsonify({"ok": True, "id": aid, "target": target, "label": label})


    @app.route("/api/social-alerts/<int:alert_id>", methods=["DELETE"])
    def api_social_alerts_delete(alert_id):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        n = social_alert_delete(alert_id, guild_id=g_id)
        return jsonify({"ok": True, "deleted": n})


    @app.route("/api/social-alerts/<int:alert_id>/toggle", methods=["POST"])
    def api_social_alerts_toggle(alert_id):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled"))
        social_alert_set_enabled(alert_id, enabled, guild_id=g_id)
        return jsonify({"ok": True, "enabled": enabled})


    @app.route("/api/social-alerts/<int:alert_id>/reset", methods=["POST"])
    def api_social_alerts_reset(alert_id):
        """Force re-detection: clears last_seen_id so the next poll notifies
        as if the alert had just been created."""
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        n = social_alert_reset(alert_id, guild_id=g_id)
        return jsonify({"ok": True, "reset": n})


    # ===== Reaction Roles dashboard =====

    @app.route("/reactionroles")
    def reactionroles_page():
        return render_template("reactionroles.html", active_nav="reactionroles")


    @app.route("/api/rolereactions", methods=["GET"])
    def api_rolereactions_list():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        rows = reaction_role_list(g_id)
        # Group by message
        by_msg: dict[str, list] = {}
        for r in rows:
            by_msg.setdefault(r["message_id"], []).append(r)
        out = []
        for msg_id, items in by_msg.items():
            out.append({
                "message_id": msg_id,
                "channel_id": items[0]["channel_id"],
                "mode":       items[0]["mode"],
                "mappings":   items,
            })
        out.sort(key=lambda x: int(x["message_id"]), reverse=True)
        return jsonify({"messages": out})


    @app.route("/api/rolereactions/roles", methods=["GET"])
    def api_rolereactions_roles():
        g_id = gid()
        if not g_id:
            return jsonify({"roles": []})
        return jsonify({"roles": list_roles(g_id)})


    @app.route("/api/rolereactions/post", methods=["POST"])
    def api_rolereactions_post():
        """Enqueue a bot command to post a role-reaction message."""
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.get_json(silent=True) or {}
        channel_id  = data.get("channel_id")
        titre       = (data.get("titre") or "").strip() or t("api.server_tools.default_role_title")
        description = (data.get("description") or "").strip()
        mode        = data.get("mode") or "toggle"
        delivery    = data.get("delivery") or "reaction"
        style       = data.get("style") or "embed"
        mappings    = data.get("mappings") or []
        if not channel_id or not mappings:
            return jsonify({"error": t("api.server_tools.channel_and_mappings_required")}), 400
        if mode not in ("toggle", "add_only", "unique"):
            return jsonify({"error": t("api.server_tools.invalid_mode")}), 400
        if delivery not in ("reaction", "button"):
            return jsonify({"error": t("api.server_tools.invalid_delivery")}), 400
        if style not in ("embed", "text"):
            return jsonify({"error": t("api.server_tools.invalid_style")}), 400
        for m in mappings:
            if not m.get("role_id"):
                return jsonify({"error": t("api.server_tools.incomplete_mapping")}), 400
            # In reaction mode the emoji is mandatory; with buttons it is optional
            if delivery == "reaction" and not m.get("emoji_key"):
                return jsonify({"error": t("api.server_tools.emoji_required_for_reactions")}), 400

        color = (data.get("color") or "").strip() or None
        cmd_id = bot_command_enqueue(g_id, "rolereaction_post", {
            "channel_id":  str(channel_id),
            "titre":       titre,
            "description": description,
            "mode":        mode,
            "delivery":    delivery,
            "style":       style,
            "color":       color,
            "mappings":    mappings,
            "by":          _current_user_id(),
        })
        return jsonify({"ok": True, "cmd_id": cmd_id})


    @app.route("/api/rolereactions/command/<int:cmd_id>", methods=["GET"])
    def api_rolereactions_command_status(cmd_id):
        """Lets the front-end poll a role-reaction command status in order to
        show an explicit success or error."""
        row = bot_command_get(cmd_id)
        if not row:
            return jsonify({"error": "command_not_found"}), 404
        return jsonify({
            "id":        row.get("id"),
            "cmd":       row.get("cmd"),
            "status":    row.get("status"),
            "result":    row.get("result"),
            "created_at":   row.get("created_at"),
            "processed_at": row.get("processed_at"),
        })


    @app.route("/api/rolereactions/<message_id>", methods=["DELETE"])
    def api_rolereactions_delete(message_id):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        n = reaction_role_remove_message(g_id, message_id)
        return jsonify({"ok": True, "deleted": n})


    @app.route("/api/rolereactions/<message_id>/<emoji>", methods=["DELETE"])
    def api_rolereactions_delete_emoji(message_id, emoji):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        n = reaction_role_remove(g_id, message_id, emoji)
        return jsonify({"ok": True, "deleted": n})


    # ===== Giveaways dashboard =====
    from database import (giveaways_list, giveaway_get, giveaway_create,
                          giveaway_cancel, giveaway_entries_count)
    import datetime as _gw_dt

    @app.route("/giveaways")
    def giveaways_page():
        return render_template("giveaways.html", active_nav="giveaways")


    @app.route("/api/giveaways", methods=["GET"])
    def api_giveaways_list():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        rows = giveaways_list(g_id, limit=100)
        for r in rows:
            r["entries_count"] = giveaway_entries_count(r["id"])
        return jsonify({"giveaways": rows})


    @app.route("/api/giveaways", methods=["POST"])
    def api_giveaways_create():
        """Create a giveaway from the dashboard and delegate posting to the bot."""
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        prize    = (data.get("prize") or "").strip()
        winners  = int(data.get("winners_count") or 1)
        channel  = (data.get("channel_id") or "").strip()
        duration_sec = int(data.get("duration_sec") or 0)
        if not prize or not channel or duration_sec < 30 or duration_sec > 30 * 86400:
            return jsonify({"error": t("api.server_tools.invalid_giveaway_fields")}), 400
        if winners < 1 or winners > 50:
            return jsonify({"error": t("api.server_tools.invalid_winners_count")}), 400
        if len(prize) > 200:
            return jsonify({"error": t("api.server_tools.prize_too_long")}), 400
        ends_at = (_gw_dt.datetime.now(_gw_dt.timezone.utc)
                   + _gw_dt.timedelta(seconds=duration_sec))
        new_id = giveaway_create(g_id, channel, prize, winners,
                                 ends_at.isoformat(), _current_user_id())
        # Ask the bot to post the message
        bot_command_enqueue(g_id, "giveaway_post", {
            "giveaway_id": new_id,
        })
        return jsonify({"success": True, "giveaway_id": new_id})


    @app.route("/api/giveaways/<int:gid_>/cancel", methods=["POST"])
    def api_giveaways_cancel(gid_):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        gw = giveaway_get(gid_)
        if not gw or str(gw.get("guild_id")) != str(g_id):
            return jsonify({"error": t("api.server_tools.giveaway_not_found")}), 404
        if gw.get("ended"):
            return jsonify({"error": t("api.server_tools.giveaway_already_ended")}), 400
        giveaway_cancel(gid_)
        bot_command_enqueue(g_id, "giveaway_cancel_post", {
            "giveaway_id": gid_,
        })
        return jsonify({"success": True})


    @app.route("/api/giveaways/<int:gid_>/reroll", methods=["POST"])
    def api_giveaways_reroll(gid_):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        gw = giveaway_get(gid_)
        if not gw or str(gw.get("guild_id")) != str(g_id):
            return jsonify({"error": t("api.server_tools.giveaway_not_found")}), 404
        bot_command_enqueue(g_id, "giveaway_reroll", {
            "giveaway_id": gid_,
        })
        return jsonify({"success": True})


    # ===== Custom commands dashboard =====
    from database import (custom_cmds_list as _cc_list,
                          custom_cmd_get as _cc_get,
                          custom_cmd_upsert as _cc_upsert,
                          custom_cmd_delete as _cc_delete,
                          CUSTOM_CMD_NAME_RE as _cc_name_re)
    from services.feature_guard import COMMAND_FEATURE_MAP as _CMD_FEATURE_MAP

    # Reserved names: every existing bot slash command. Prevents conflicts
    # or a built-in command being overridden by a custom one.
    _RESERVED_CMD_NAMES = set(_CMD_FEATURE_MAP.keys()) | {
        "sync", "cmd",  # global commands without an entry in feature_map
    }

    def _user_has_tookbot_plus(uid):
        """True when the uid user has TookBot+ (manual grant OR SKU OR bot owner)."""
        if not uid:
            return False
        import os as _os3
        uid = str(uid)
        if has_premium_grant(uid, feature="tookbot_plus", inherit_all=False):
            return True
        sku = _os3.getenv("SKU_TOOKBOT_PLUS", "").strip() or None
        if sku and user_has_active_entitlement(uid, sku_id=sku):
            return True
        bot_owner = _os3.getenv("DISCORD_OWNER_ID", "").strip() or None
        if bot_owner and uid == bot_owner:
            return True
        return False


    def _has_tookbot_plus_for_current_guild():
        """True when the guild owner OR the logged-in user has TookBot+.

        More permissive than owner-only: a subscribed user can drive the custom
        commands of the guilds they have access to.
        """
        g_id = gid()
        if not g_id:
            return False

        # 1) Guild owner (in DB, populated through on_ready / on_guild_join)
        try:
            from database import get_guild as _gg
            guild_row = _gg(g_id) or {}
        except Exception:
            guild_row = {}
        owner_id = str(guild_row.get("owner_id") or "").strip()
        if owner_id and _user_has_tookbot_plus(owner_id):
            return True

        # 2) Logged-in user (session). Key 'user_id' (not 'id', see auth.py).
        du = g.discord_user if hasattr(g, "discord_user") else {}
        sess_uid = (du or {}).get("user_id") or (du or {}).get("id")
        if sess_uid and _user_has_tookbot_plus(sess_uid):
            return True

        return False

    @app.route("/custom-commands")
    def custom_commands_page():
        # ?preview=1 forces the paywall (handy for QA / non-premium checks)
        force_paywall = request.args.get("preview") in ("1", "true")
        return render_template(
            "custom_commands.html",
            active_nav="custom_commands",
            is_premium=(False if force_paywall else _has_tookbot_plus_for_current_guild()),
        )

    @app.route("/api/custom-commands", methods=["GET"])
    def api_custom_commands_list():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        return jsonify({"commands": _cc_list(g_id)})

    @app.route("/api/custom-commands", methods=["POST"])
    def api_custom_commands_save():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        if not _has_tookbot_plus_for_current_guild():
            return jsonify({"error": t("api.server_tools.plus_required_for_guild")}), 402
        data = request.json or {}
        name = (data.get("name") or "").strip().lower()
        if not _cc_name_re.match(name):
            return jsonify({"error": t("api.server_tools.invalid_command_name")}), 400
        if name in _RESERVED_CMD_NAMES:
            return jsonify({"error": t("api.server_tools.reserved_command_name", name=name)}), 400
        desc      = (data.get("description") or "").strip() or None
        use_embed = bool(data.get("use_embed"))
        enabled   = bool(data.get("enabled", True))
        resp_text = data.get("response_text") or ""
        resp_emb  = data.get("response_embed") or ""
        if use_embed:
            # Validate the embed JSON
            try:
                import json as _j
                obj = _j.loads(resp_emb or "{}")
                if not isinstance(obj, dict):
                    return jsonify({"error": t("api.server_tools.embed_must_be_object")}), 400
                resp_emb = _j.dumps(obj)
            except Exception as e:
                return jsonify({"error": t("api.server_tools.invalid_embed_json", error=type(e).__name__)}), 400
            if not resp_emb or resp_emb == "{}":
                return jsonify({"error": t("api.server_tools.empty_embed")}), 400
        else:
            if len(resp_text) < 1:
                return jsonify({"error": t("api.server_tools.response_text_required")}), 400
            if len(resp_text) > 2000:
                return jsonify({"error": t("api.server_tools.response_text_too_long")}), 400
        cid = _cc_upsert(
            g_id, name,
            description=desc,
            response_text=resp_text if not use_embed else None,
            response_embed=resp_emb if use_embed else None,
            use_embed=use_embed,
            enabled=enabled,
            created_by=_current_user_id(),
        )
        # Ask the bot to resync this guild's slash commands
        bot_command_enqueue(g_id, "custom_cmd_sync", {})
        return jsonify({"success": True, "id": cid})

    @app.route("/api/custom-commands/<name>", methods=["DELETE"])
    def api_custom_commands_delete(name):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        if not _has_tookbot_plus_for_current_guild():
            return jsonify({"error": t("api.server_tools.plus_required_for_guild")}), 402
        ok = _cc_delete(g_id, name.lower())
        if ok:
            bot_command_enqueue(g_id, "custom_cmd_sync", {})
        return jsonify({"success": ok})


    # ===== Counter-Strike 2 dashboard =====
    from database import cs_rank_config_get, cs_rank_config_upsert

    @app.route("/cs2")
    def cs2_page():
        return render_template("cs2.html", active_nav="cs2")


    @app.route("/api/cs2/config", methods=["GET"])
    def api_cs2_config_get():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        return jsonify(cs_rank_config_get(g_id))


    @app.route("/api/cs2/config", methods=["POST"])
    def api_cs2_config_set():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.get_json(silent=True) or {}
        roles = {k: data.get(k) for k in (
            "role_grey", "role_lightblue", "role_blue", "role_purple",
            "role_pink", "role_red", "role_gold",
        )}
        cs_rank_config_upsert(g_id, enabled=bool(data.get("enabled")), **roles)
        return jsonify({"ok": True})


    # ===== League of Legends dashboard =====
    from database import lol_rank_config_get, lol_rank_config_upsert
    import json as _json_lol

    _LOL_TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM",
                  "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]

    @app.route("/lol")
    def lol_page():
        return render_template("lol.html", active_nav="lol", tiers=_LOL_TIERS)

    @app.route("/api/lol/config", methods=["GET"])
    def api_lol_config_get():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        cfg = lol_rank_config_get(g_id)
        role_map = {}
        if cfg.get("role_map"):
            try:
                role_map = _json_lol.loads(cfg["role_map"]) or {}
            except Exception:
                role_map = {}
        return jsonify({
            "enabled":  bool(cfg.get("enabled")),
            "role_map": role_map,
            "tiers":    _LOL_TIERS,
        })

    @app.route("/api/lol/config", methods=["POST"])
    def api_lol_config_set():
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.get_json(silent=True) or {}
        enabled = 1 if data.get("enabled") else 0
        raw_map = data.get("role_map") or {}
        # Clean: keep only the valid tiers + role_id as a str
        clean = {}
        for tier in _LOL_TIERS:
            v = raw_map.get(tier)
            if v:
                clean[tier] = str(v)
            else:
                clean[tier] = None
        lol_rank_config_upsert(g_id, enabled=enabled, role_map=clean)
        return jsonify({"ok": True})


    # ===== Pass: "My Pass" user page =====
