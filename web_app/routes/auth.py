from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

from services.i18n import t

def register_auth_routes(app, deps):
    globals().update(deps)
    @app.route("/", methods=["GET", "POST"])
    def login():
        ip = _client_ip()
        password_fallback = bool(PASSWORD) and not OAUTH_ENABLED
        if request.method == "POST":
            # The password is only accepted as a fallback (OAuth disabled)
            if not password_fallback:
                return render_template("login.html",
                                       oauth_enabled=OAUTH_ENABLED,
                                       password_fallback=False,
                                       error=t("api.auth.password_login_disabled")), 400
            ok, retry_in = _check_login_rate(ip)
            if not ok:
                return render_template("login.html",
                    oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback,
                    error=t("api.auth.too_many_attempts", minutes=retry_in // 60, seconds=retry_in % 60),
                ), 429
            submitted = request.form.get("password") or ""
            if PASSWORD and secrets.compare_digest(submitted, PASSWORD):
                session.permanent       = True
                session["logged_in"]    = True
                session["login_ts"]     = time.time()
                session["login_ip"]     = ip
                # Password mode: you are the owner by default (single-user fallback)
                session["discord"] = {
                    "user_id":              "password-user",
                    "username":             "Admin (password)",
                    "avatar":               None,
                    "is_owner":             True,
                    "accessible_guild_ids": [],
                    "guilds_meta":          [],
                }
                _record_login(ip, True, username="password")
                return redirect("/select-guild")
            _record_login(ip, False)
            return render_template("login.html",
                oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback,
                error=t("api.auth.wrong_password"))
        if session.get("logged_in"):
            return redirect("/dashboard" if session.get("guild_id") else "/select-guild")
        return render_template("login.html", oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/")


    @app.route("/privacy")
    def privacy_page():
        return render_template("privacy.html")

    @app.route("/terms")
    def terms_page():
        return render_template("terms.html")


    @app.route("/oauth/login")
    def oauth_login():
        if not OAUTH_ENABLED:
            return t("api.auth.oauth_not_configured"), 500
        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        params = {
            "client_id":     DISCORD_CLIENT_ID,
            "redirect_uri":  OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope":         "identify guilds",
            "state":         state,
            "prompt":        "consent",
        }
        return redirect(DISCORD_AUTH + "?" + urllib.parse.urlencode(params))


    @app.route("/oauth/callback")
    def oauth_callback():
        if not OAUTH_ENABLED:
            return t("api.auth.oauth_not_configured_short"), 500
        err = request.args.get("error")
        if err:
            return render_template("login.html", error=t("api.auth.oauth_denied", error=err)), 400
        code  = request.args.get("code")
        state = request.args.get("state")
        if not code or not state or state != session.get("oauth_state"):
            return render_template("login.html", error=t("api.auth.invalid_oauth_state")), 400

        # 1. Exchange the code for an access_token
        try:
            r = _requests.post(DISCORD_TOKEN, data={
                "client_id":     DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  OAUTH_REDIRECT_URI,
            }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
            r.raise_for_status()
            token_data = r.json()
        except Exception as e:
            return render_template("login.html", error=t("api.auth.token_exchange_failed", error=e)), 500

        access_token = token_data.get("access_token")
        if not access_token:
            return render_template("login.html", error=t("api.auth.no_access_token")), 500

        # 2. Fetch user
        try:
            u = _requests.get(f"{DISCORD_API}/users/@me",
                              headers={"Authorization": f"Bearer {access_token}"},
                              timeout=10).json()
            guilds_user = _requests.get(f"{DISCORD_API}/users/@me/guilds",
                                        headers={"Authorization": f"Bearer {access_token}"},
                                        timeout=10).json()
        except Exception as e:
            return render_template("login.html", error=t("api.auth.fetch_user_failed", error=e)), 500

        if not isinstance(u, dict) or not u.get("id"):
            return render_template("login.html", error=t("api.auth.invalid_discord_response")), 500

        user_id  = str(u["id"])
        is_owner = bool(DISCORD_OWNER_ID) and user_id == DISCORD_OWNER_ID

        # 3. Filter guilds: intersection with those where the bot is present + admin/manage_guild perms check
        bot_guild_ids = {gd["guild_id"] for gd in list_guilds(active_only=True)}
        accessible = []
        for gd in (guilds_user or []):
            gid = str(gd.get("id") or "")
            if gid not in bot_guild_ids:
                continue
            try:
                perms = int(gd.get("permissions", 0) or 0)
            except (TypeError, ValueError):
                perms = 0
            is_admin = bool(perms & PERM_ADMINISTRATOR)
            is_mgr   = bool(perms & PERM_MANAGE_GUILD)
            is_kick  = bool(perms & (PERM_KICK_MEMBERS | PERM_BAN_MEMBERS))
            if gd.get("owner") or is_admin or is_mgr or is_kick:
                accessible.append({
                    "guild_id":         gid,
                    "name":             gd.get("name"),
                    "perms":            perms,
                    "is_admin":         is_admin or bool(gd.get("owner")),
                    "is_manager":       is_mgr,
                    "is_mod":           is_kick,
                    "is_server_owner":  bool(gd.get("owner")),
                })

        # Guilds the user manages but WITHOUT the bot -> "Add TookBot" list.
        invitable = []
        for gd in (guilds_user or []):
            gid = str(gd.get("id") or "")
            if not gid or gid in bot_guild_ids:
                continue
            try:
                perms = int(gd.get("permissions", 0) or 0)
            except (TypeError, ValueError):
                perms = 0
            if gd.get("owner") or (perms & PERM_ADMINISTRATOR) or (perms & PERM_MANAGE_GUILD):
                icon = gd.get("icon")
                invitable.append({
                    "guild_id": gid,
                    "name": gd.get("name"),
                    "icon_url": (f"https://cdn.discordapp.com/icons/{gid}/{icon}.png?size=128"
                                 if icon else None),
                })
        invitable = invitable[:60]

        # Any Discord user is allowed to log in (access to the /premium page,
        # managing their purchases, etc.). Without a shared mod/admin guild they
        # simply won't see the moderation dashboard - handled by the access middleware.
        avatar_url = None
        if u.get("avatar"):
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{u['avatar']}.png?size=128"
        else:
            # default avatar
            idx = (int(user_id) >> 22) % 6 if user_id.isdigit() else 0
            avatar_url = f"https://cdn.discordapp.com/embed/avatars/{idx}.png"

        session.permanent = True
        session["logged_in"]  = True
        session["login_ts"]   = time.time()
        session["login_ip"]   = _client_ip()
        session["discord"]    = {
            "user_id":              user_id,
            "username":             u.get("global_name") or u.get("username") or "user",
            "avatar":               avatar_url,
            "is_owner":             is_owner,
            "accessible_guild_ids": [g["guild_id"] for g in accessible],
            "guilds_meta":          accessible,
            "invitable_guilds":     invitable,
        }
        session.pop("oauth_state", None)
        _record_login(_client_ip(), True, username=session["discord"]["username"])
        # Go straight back to the page requested before login (e.g. live combat)
        nxt = session.pop("post_login_redirect", None)
        if nxt and isinstance(nxt, str) and nxt.startswith("/"):
            return redirect(nxt)
        # Owner / mod -> guild selection; "regular" user -> premium page directly.
        if is_owner or accessible:
            return redirect("/select-guild")
        return redirect("/premium")


    @app.route("/oauth/logout")
    def oauth_logout():
        session.clear()
        return redirect("/")


    @app.route("/select-guild", methods=["GET", "POST"])
    def select_guild():
        if request.method == "POST":
            g_id = request.form.get("guild_id") or (request.json or {}).get("guild_id")
            if g_id and any(gd["guild_id"] == g_id for gd in g.guilds):
                session["guild_id"] = g_id
                return redirect("/dashboard")
        disc = session.get("discord") or {}
        return render_template("select_guild.html", guilds=g.guilds,
                               invitable=disc.get("invitable_guilds") or [],
                               client_id=DISCORD_CLIENT_ID,
                               discord_user=disc)

    @app.route("/api/select-guild", methods=["POST"])
    def api_select_guild():
        data = request.json or {}
        g_id = data.get("guild_id")
        if not g_id:
            return jsonify({"error": t("api.auth.guild_id_required")}), 400
        if not any(gd["guild_id"] == g_id for gd in g.guilds):
            return jsonify({"error": t("api.auth.unknown_or_forbidden_guild")}), 403
        session["guild_id"] = g_id
        return jsonify({"success": True})

    @app.route("/api/guilds")
    def api_guilds():
        return jsonify({"guilds": g.guilds})


