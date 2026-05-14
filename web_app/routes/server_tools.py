from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

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
            return jsonify({"error": "platform invalide"}), 400
        if not raw or not channel_id:
            return jsonify({"error": "lien et salon requis"}), 400

        parsed = social.parse_social_url(plat, raw)
        if not parsed:
            examples = {
                "twitch":  "https://twitch.tv/<pseudo>",
                "youtube": "https://youtube.com/@<handle> ou /channel/UC...",
                "reddit":  "https://reddit.com/r/<sub> ou /user/<u>",
            }
            return jsonify({"error": f"lien invalide. Exemple : {examples[plat]}"}), 400
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
        """Force re-detection : efface last_seen_id pour que le prochain poll
        notifie comme si l'alerte venait d'etre creee."""
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
        """Enqueue une commande bot pour poster un message role-reaction."""
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.get_json(silent=True) or {}
        channel_id  = data.get("channel_id")
        titre       = (data.get("titre") or "").strip() or "Choisis ton rôle"
        description = (data.get("description") or "").strip()
        mode        = data.get("mode") or "toggle"
        mappings    = data.get("mappings") or []
        if not channel_id or not mappings:
            return jsonify({"error": "channel_id et mappings requis"}), 400
        if mode not in ("toggle", "add_only", "unique"):
            return jsonify({"error": "mode invalide"}), 400
        for m in mappings:
            if not m.get("emoji_key") or not m.get("role_id"):
                return jsonify({"error": "mapping incomplet"}), 400

        cmd_id = bot_command_enqueue(g_id, "rolereaction_post", {
            "channel_id":  str(channel_id),
            "titre":       titre,
            "description": description,
            "mode":        mode,
            "mappings":    mappings,
            "by":          _current_user_id(),
        })
        return jsonify({"ok": True, "cmd_id": cmd_id})


    @app.route("/api/rolereactions/command/<int:cmd_id>", methods=["GET"])
    def api_rolereactions_command_status(cmd_id):
        """Permet au front de polling le statut d'une commande role-reaction
        pour afficher succes ou erreur explicite."""
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


    # ===== Pass : page utilisateur "Mon Pass" =====
