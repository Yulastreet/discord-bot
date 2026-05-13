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

    @app.route("/api/music/command/<int:cmd_id>")
    def api_music_command_status(cmd_id):
        row = bot_command_get(cmd_id)
        if not row:
            return jsonify({"error": "Commande introuvable"}), 404
        return jsonify(row)


    # =====================================================================
    # LOGS (per-guild)
    # =====================================================================
