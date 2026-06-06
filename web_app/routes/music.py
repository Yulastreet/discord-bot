from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

def register_music_routes(app, deps):
    globals().update(deps)
    @app.route("/music")
    def music_page():
        return render_template("music.html")

    @app.route("/api/music/state")
    def api_music_state():
        g_id = gid()
        return jsonify({
            "state": music_state_get(g_id) or {},
            "queue": music_queue_list(g_id),
        })

    @app.route("/api/music/play", methods=["POST"])
    def api_music_play():
        g_id = gid()
        data = request.json or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "query requis"}), 400
        cmd_id = bot_command_enqueue(g_id, "music_play", {
            "query": query,
            "voice_channel_id": data.get("voice_channel_id"),
        })
        return jsonify({"success": True, "command_id": cmd_id})

    @app.route("/api/music/skip", methods=["POST"])
    def api_music_skip():
        cid = bot_command_enqueue(gid(), "music_skip", {})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/stop", methods=["POST"])
    def api_music_stop():
        cid = bot_command_enqueue(gid(), "music_stop", {})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/pause", methods=["POST"])
    def api_music_pause():
        cid = bot_command_enqueue(gid(), "music_pause", {})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/resume", methods=["POST"])
    def api_music_resume():
        cid = bot_command_enqueue(gid(), "music_resume", {})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/leave", methods=["POST"])
    def api_music_leave():
        cid = bot_command_enqueue(gid(), "music_leave", {})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/clear", methods=["POST"])
    def api_music_clear():
        cid = bot_command_enqueue(gid(), "music_clear", {})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/remove", methods=["POST"])
    def api_music_remove():
        data = request.json or {}
        track_id = data.get("track_id")
        if track_id is None:
            return jsonify({"error": "track_id requis"}), 400
        cid = bot_command_enqueue(gid(), "music_remove_track", {"track_id": track_id})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/stats")
    def api_music_stats():
        """Stats lecture : summary, top tracks, top requesters. ?days=30 par defaut."""
        from database import (music_stats_summary, music_stats_top_tracks,
                              music_stats_top_requesters)
        try:
            days = int(request.args.get("days", 30))
        except Exception:
            days = 30
        days = max(1, min(365, days))
        g_id = gid()
        return jsonify({
            "days":           days,
            "summary":        music_stats_summary(g_id, days) or {},
            "top_tracks":     music_stats_top_tracks(g_id, days, 10),
            "top_requesters": music_stats_top_requesters(g_id, days, 10),
        })

    @app.route("/api/music/volume", methods=["POST"])
    def api_music_volume():
        data = request.json or {}
        try:
            vol = max(0, min(200, int(data.get("volume", 100))))
        except (TypeError, ValueError):
            return jsonify({"error": "volume invalide"}), 400
        # Persist immediatement (utilise par la prochaine track + applique en live)
        from database import guild_setting_set
        g_id = gid()
        guild_setting_set(g_id, "music_volume", str(vol / 100.0))
        cid = bot_command_enqueue(g_id, "music_volume", {"volume": vol})
        return jsonify({"success": True, "command_id": cid, "volume": vol})

    @app.route("/api/music/jump", methods=["POST"])
    def api_music_jump():
        data = request.json or {}
        try:
            position = max(1, int(data.get("position", 1)))
        except (TypeError, ValueError):
            return jsonify({"error": "position invalide"}), 400
        cid = bot_command_enqueue(gid(), "music_jump", {"position": position})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/queue_reorder", methods=["POST"])
    def api_music_queue_reorder():
        """Reordonne la queue selon l'ordre des track_ids fournis."""
        from database import music_queue_list, get_db
        data = request.json or {}
        track_ids = data.get("track_ids") or []
        if not isinstance(track_ids, list) or not track_ids:
            return jsonify({"error": "track_ids requis"}), 400
        g_id = gid()
        # Verifie que tous les ids appartiennent a cette guild
        existing = {t["id"]: t for t in (music_queue_list(g_id) or [])}
        if not all(int(tid) in existing for tid in track_ids):
            return jsonify({"error": "track_id inconnu"}), 400
        # Reassigne position 1..N dans l'ordre fourni
        conn = get_db(); c = conn.cursor()
        for new_pos, tid in enumerate(track_ids, start=1):
            c.execute(
                "UPDATE music_queue SET position = ? WHERE guild_id = ? AND id = ?",
                (new_pos, str(g_id), int(tid)),
            )
        conn.commit(); conn.close()
        return jsonify({"success": True, "reordered": len(track_ids)})

    @app.route("/api/music/join", methods=["POST"])
    def api_music_join():
        data = request.json or {}
        ch_id = (data.get("voice_channel_id") or "").strip()
        if not ch_id:
            return jsonify({"error": "voice_channel_id requis"}), 400
        cid = bot_command_enqueue(gid(), "music_join", {"voice_channel_id": ch_id})
        return jsonify({"success": True, "command_id": cid})

    @app.route("/api/music/voice-channels")
    def api_music_voice_channels():
        """Liste les voice channels du guild courant pour le selecteur."""
        from database import list_channels
        try:
            chans = list_channels(gid(), type_filter="voice") or []
        except Exception:
            chans = []
        return jsonify({"voice_channels": chans})

    @app.route("/api/music/command/<int:cmd_id>")
    def api_music_command_status(cmd_id):
        row = bot_command_get(cmd_id)
        if not row:
            return jsonify({"error": "Commande introuvable"}), 404
        return jsonify(row)


