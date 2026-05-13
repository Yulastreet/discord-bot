from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

def register_auth_routes(app, deps):
    globals().update(deps)
    @app.route("/", methods=["GET", "POST"])
    def login():
        ip = _client_ip()
        password_fallback = bool(PASSWORD) and not OAUTH_ENABLED
        if request.method == "POST":
            # Le password n'est accepte qu'en fallback (OAuth desactive)
            if not password_fallback:
                return render_template("login.html",
                                       oauth_enabled=OAUTH_ENABLED,
                                       password_fallback=False,
                                       error="Login password désactivé. Utilise Discord."), 400
            ok, retry_in = _check_login_rate(ip)
            if not ok:
                return render_template("login.html",
                    oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback,
                    error=f"Trop de tentatives. Réessaie dans {retry_in // 60}m {retry_in % 60}s.",
                ), 429
            submitted = request.form.get("password") or ""
            if PASSWORD and secrets.compare_digest(submitted, PASSWORD):
                session.permanent       = True
                session["logged_in"]    = True
                session["login_ts"]     = time.time()
                session["login_ip"]     = ip
                # Mode password : tu es owner par defaut (single-user fallback)
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
                error="Mot de passe incorrect")
        if session.get("logged_in"):
            return redirect("/dashboard" if session.get("guild_id") else "/select-guild")
        return render_template("login.html", oauth_enabled=OAUTH_ENABLED, password_fallback=password_fallback)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/")


    # =====================================================================
    # PUBLIC LEGAL PAGES (sans auth, accessible a tous)
    # =====================================================================

    @app.route("/privacy")
    def privacy_page():
        return render_template("privacy.html")

    @app.route("/terms")
    def terms_page():
        return render_template("terms.html")


    # =====================================================================
    # OAuth DISCORD
    # =====================================================================

    @app.route("/oauth/login")
    def oauth_login():
        if not OAUTH_ENABLED:
            return "OAuth Discord non configuré.", 500
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
            return "OAuth non configuré.", 500
        err = request.args.get("error")
        if err:
            return render_template("login.html", error=f"Discord OAuth refusé : {err}"), 400
        code  = request.args.get("code")
        state = request.args.get("state")
        if not code or not state or state != session.get("oauth_state"):
            return render_template("login.html", error="État OAuth invalide. Réessaie."), 400

        # 1. Échange code contre access_token
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
            return render_template("login.html", error=f"Échange code -> token échoué : {e}"), 500

        access_token = token_data.get("access_token")
        if not access_token:
            return render_template("login.html", error="Pas d'access token reçu."), 500

        # 2. Fetch user
        try:
            u = _requests.get(f"{DISCORD_API}/users/@me",
                              headers={"Authorization": f"Bearer {access_token}"},
                              timeout=10).json()
            guilds_user = _requests.get(f"{DISCORD_API}/users/@me/guilds",
                                        headers={"Authorization": f"Bearer {access_token}"},
                                        timeout=10).json()
        except Exception as e:
            return render_template("login.html", error=f"Fetch user/guilds échoué : {e}"), 500

        if not isinstance(u, dict) or not u.get("id"):
            return render_template("login.html", error="Réponse Discord invalide."), 500

        user_id  = str(u["id"])
        is_owner = bool(DISCORD_OWNER_ID) and user_id == DISCORD_OWNER_ID

        # 3. Filtrer les guilds : intersection avec celles ou le bot est present + check perms admin/manage_guild
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
                    "guild_id":   gid,
                    "name":       gd.get("name"),
                    "perms":      perms,
                    "is_admin":   is_admin or bool(gd.get("owner")),
                    "is_manager": is_mgr,
                    "is_mod":     is_kick,
                })

        # On autorise tous les utilisateurs Discord a se connecter (acces page /premium,
        # gestion de leurs achats, etc.). Sans guild commune mod/admin, ils ne verront
        # juste pas le dashboard de moderation — geres par le middleware d'acces.
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
        }
        session.pop("oauth_state", None)
        _record_login(_client_ip(), True, username=session["discord"]["username"])
        # Owner / mod -> selection de guild ; user "regular" -> page premium directement.
        if is_owner or accessible:
            return redirect("/select-guild")
        return redirect("/premium")


    @app.route("/oauth/logout")
    def oauth_logout():
        session.clear()
        return redirect("/")


    # =====================================================================
    # GUILD PICKER
    # =====================================================================

    @app.route("/select-guild", methods=["GET", "POST"])
    def select_guild():
        if request.method == "POST":
            g_id = request.form.get("guild_id") or (request.json or {}).get("guild_id")
            if g_id and any(gd["guild_id"] == g_id for gd in g.guilds):
                session["guild_id"] = g_id
                return redirect("/dashboard")
        return render_template("select_guild.html", guilds=g.guilds)

    @app.route("/api/select-guild", methods=["POST"])
    def api_select_guild():
        data = request.json or {}
        g_id = data.get("guild_id")
        if not g_id:
            return jsonify({"error": "guild_id requis"}), 400
        if not any(gd["guild_id"] == g_id for gd in g.guilds):
            return jsonify({"error": "Serveur inconnu ou non autorisé"}), 403
        session["guild_id"] = g_id
        return jsonify({"success": True})

    @app.route("/api/guilds")
    def api_guilds():
        return jsonify({"guilds": g.guilds})


    # =====================================================================
    # DASHBOARD GENERAL (cross-guild)
    # =====================================================================
