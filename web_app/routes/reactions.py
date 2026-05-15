from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

def register_reaction_routes(app, deps):
    globals().update(deps)
    @app.route("/reactions")
    def reactions_panel():
        g_id = gid()
        conn = get_db()
        users_rows = conn.execute(
            "SELECT DISTINCT user_id, username FROM users WHERE guild_id = ? ORDER BY username COLLATE NOCASE",
            (g_id,)).fetchall()
        reactions_rows = conn.execute(
            "SELECT user_id, emoji FROM reactions WHERE guild_id = ?", (g_id,)).fetchall()
        reactions = {str(row["user_id"]): row["emoji"] for row in reactions_rows}
        conn.close()
        users = [(r["user_id"], r["username"]) for r in users_rows]
        return render_template("reactions.html", users=users, reactions=reactions)

    @app.route("/api/reactions/add", methods=["POST"])
    def add_reaction_route():
        g_id = gid()
        data = request.json or {}
        user_id = str(data.get("user_id") or "")
        emoji = data.get("emoji")
        if not user_id or not emoji:
            return jsonify({"error": "user_id et emoji requis"}), 400
        conn = get_db()
        conn.execute("""INSERT INTO reactions (guild_id, user_id, emoji) VALUES (?, ?, ?)
                        ON CONFLICT(guild_id, user_id) DO UPDATE SET emoji = excluded.emoji""",
                     (g_id, user_id, emoji))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    @app.route("/api/reactions/remove", methods=["POST"])
    def remove_reaction_route():
        g_id = gid()
        data = request.json or {}
        user_id = str(data.get("user_id") or "")
        if not user_id:
            return jsonify({"error": "user_id requis"}), 400
        conn = get_db()
        conn.execute("DELETE FROM reactions WHERE guild_id = ? AND user_id = ?", (g_id, user_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})


