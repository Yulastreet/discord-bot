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

        color = (data.get("color") or "").strip() or None
        cmd_id = bot_command_enqueue(g_id, "rolereaction_post", {
            "channel_id":  str(channel_id),
            "titre":       titre,
            "description": description,
            "mode":        mode,
            "color":       color,
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
        """Cree un giveaway via dashboard et delegue au bot pour poster le msg."""
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        data = request.json or {}
        prize    = (data.get("prize") or "").strip()
        winners  = int(data.get("winners_count") or 1)
        channel  = (data.get("channel_id") or "").strip()
        duration_sec = int(data.get("duration_sec") or 0)
        if not prize or not channel or duration_sec < 30 or duration_sec > 30 * 86400:
            return jsonify({"error": "champs invalides (prize, channel_id, duration_sec entre 30s et 30j)"}), 400
        if winners < 1 or winners > 50:
            return jsonify({"error": "gagnants doit etre entre 1 et 50"}), 400
        if len(prize) > 200:
            return jsonify({"error": "prix max 200 caracteres"}), 400
        ends_at = (_gw_dt.datetime.now(_gw_dt.timezone.utc)
                   + _gw_dt.timedelta(seconds=duration_sec))
        new_id = giveaway_create(g_id, channel, prize, winners,
                                 ends_at.isoformat(), _current_user_id())
        # Demande au bot de poster le message
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
            return jsonify({"error": "introuvable"}), 404
        if gw.get("ended"):
            return jsonify({"error": "deja termine"}), 400
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
            return jsonify({"error": "introuvable"}), 404
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

    @app.route("/custom-commands")
    def custom_commands_page():
        return render_template("custom_commands.html", active_nav="custom_commands")

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
        data = request.json or {}
        name = (data.get("name") or "").strip().lower()
        if not _cc_name_re.match(name):
            return jsonify({"error": "nom invalide (a-z 0-9 _ - uniquement, max 32 chars)"}), 400
        desc      = (data.get("description") or "").strip() or None
        use_embed = bool(data.get("use_embed"))
        enabled   = bool(data.get("enabled", True))
        resp_text = data.get("response_text") or ""
        resp_emb  = data.get("response_embed") or ""
        if use_embed:
            # Valide JSON embed
            try:
                import json as _j
                obj = _j.loads(resp_emb or "{}")
                if not isinstance(obj, dict):
                    return jsonify({"error": "embed JSON doit etre un objet"}), 400
                resp_emb = _j.dumps(obj)
            except Exception as e:
                return jsonify({"error": f"embed JSON invalide: {type(e).__name__}"}), 400
            if not resp_emb or resp_emb == "{}":
                return jsonify({"error": "embed vide"}), 400
        else:
            if len(resp_text) < 1:
                return jsonify({"error": "response_text obligatoire en mode texte"}), 400
            if len(resp_text) > 2000:
                return jsonify({"error": "response_text max 2000 caracteres"}), 400
        cid = _cc_upsert(
            g_id, name,
            description=desc,
            response_text=resp_text if not use_embed else None,
            response_embed=resp_emb if use_embed else None,
            use_embed=use_embed,
            enabled=enabled,
            created_by=_current_user_id(),
        )
        return jsonify({"success": True, "id": cid})

    @app.route("/api/custom-commands/<name>", methods=["DELETE"])
    def api_custom_commands_delete(name):
        g_id = gid()
        if not g_id:
            return jsonify({"error": "no_guild"}), 400
        ok = _cc_delete(g_id, name.lower())
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


    # ===== Pass : page utilisateur "Mon Pass" =====
