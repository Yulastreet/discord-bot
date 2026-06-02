from flask import render_template, request, redirect, session, jsonify, g, url_for, abort, send_file

_PUBLIC_STATS_CACHE  = {"data": None, "expires": 0.0}
_PUBLIC_STATS_TTL_SEC = 3600
_PUBLIC_STATUS_CACHE  = {"data": None, "expires": 0.0}
_PUBLIC_STATUS_TTL_SEC = 30

def register_public_stats_routes(app, deps):
    globals().update(deps)
    @app.route("/api/public-stats", methods=["GET"])
    def api_public_stats():
        """Stats publiques affichees sur la landing tookbot.click.

        Pas d'auth, CORS ouvert pour permettre le fetch cross-origin
        (apex tookbot.click -> dashboard.tookbot.click). Cache server-side
        1h pour limiter la charge DB.
        """
        now = _time.time()
        if _PUBLIC_STATS_CACHE["data"] and _PUBLIC_STATS_CACHE["expires"] > now:
            data = _PUBLIC_STATS_CACHE["data"]
        else:
            db = get_db()
            try:
                guild_count = db.execute(
                    "SELECT COUNT(*) AS n FROM guilds WHERE active = 1"
                ).fetchone()["n"]
            except Exception:
                guild_count = db.execute("SELECT COUNT(*) AS n FROM guilds").fetchone()["n"]
            user_count = db.execute(
                "SELECT COUNT(DISTINCT user_id) AS n FROM users"
            ).fetchone()["n"]
            db.close()
            data = {
                "guilds":     int(guild_count or 0),
                "users":      int(user_count  or 0),
                "updated_at": int(now),
            }
            _PUBLIC_STATS_CACHE["data"]    = data
            _PUBLIC_STATS_CACHE["expires"] = now + _PUBLIC_STATS_TTL_SEC

        resp = jsonify(data)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Cache-Control"]                = "public, max-age=3600"
        return resp


    @app.route("/api/public-status", methods=["GET"])
    def api_public_status():
        """Statut public TookBot (page /status.html).

        Lit bot_state.json (ecrit par bot.py toutes les ~30s) + check rapide
        moteur musique. Cache 30s. CORS ouvert.
        """
        import os, json as _json2
        now = _time.time()
        if _PUBLIC_STATUS_CACHE["data"] and _PUBLIC_STATUS_CACHE["expires"] > now:
            data = _PUBLIC_STATUS_CACHE["data"]
        else:
            # Bot state via bot_state.json. Tente plusieurs chemins car selon
            # la maniere dont pm2 lance le process, cwd peut differer.
            candidate_paths = [
                "bot_state.json",  # cwd
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bot_state.json"),
                "/home/ubuntu/discord-bot/bot_state.json",
            ]
            bot_state = {}
            for p in candidate_paths:
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        bot_state = _json2.load(f) or {}
                    bot_state["_loaded_from"] = p
                    break
                except FileNotFoundError:
                    continue
                except Exception as e:
                    print(f"[public-status] read fail {p}: {type(e).__name__}: {e}")
                    continue
            if not bot_state:
                print(f"[public-status] bot_state.json NOT FOUND. Tried: {candidate_paths}")

            updated_at = float(bot_state.get("updated_at") or 0)
            bot_fresh  = (now - updated_at) < 120 if updated_at else False
            bot_online = bool(bot_fresh and bot_state.get("guild_count", 0) >= 0)

            # Moteur musique (light check)
            try:
                from services.status_utils import music_engine_diagnostics
                mus = music_engine_diagnostics(timeout=0.6)
            except Exception:
                mus = {}

            warp_ok    = bool((mus.get("warp")    or {}).get("connected"))
            bgutil_ok  = bool((mus.get("bgutil_pot") or {}).get("ok"))
            privoxy_ok = bool((mus.get("privoxy")  or {}).get("ok"))
            music_ok   = warp_ok and bgutil_ok and privoxy_ok

            # Overall status : up si bot+dashboard online (musique optionnel)
            overall = "operational" if bot_online else "down"
            if bot_online and not music_ok:
                overall = "degraded"

            data = {
                "overall":           overall,
                "components": {
                    "bot": {
                        "online":      bot_online,
                        "latency_ms":  bot_state.get("latency_ms"),
                        "guild_count": bot_state.get("guild_count"),
                        "voice_count": bot_state.get("voice_count"),
                        "last_update": int(updated_at) if updated_at else None,
                        "age_sec":     int(now - updated_at) if updated_at else None,
                    },
                    "dashboard": {
                        "online": True,  # repond donc OK
                    },
                    "music_engine": {
                        "operational":   music_ok,
                        "warp":          warp_ok,
                        "bgutil_pot":    bgutil_ok,
                        "privoxy":       privoxy_ok,
                        "yt_dlp_version": mus.get("yt_dlp_version"),
                    },
                },
                "checked_at": int(now),
            }
            _PUBLIC_STATUS_CACHE["data"]    = data
            _PUBLIC_STATUS_CACHE["expires"] = now + _PUBLIC_STATUS_TTL_SEC

            # Log uptime checks par component (best-effort, ne casse pas la reponse si echec)
            try:
                from database import service_uptime_log
                service_uptime_log("bot",          bot_online)
                service_uptime_log("dashboard",    True)
                service_uptime_log("music_engine", music_ok)
            except Exception as e:
                print(f"[uptime log] {e}")

        resp = jsonify(data)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Cache-Control"]                = "public, max-age=30"
        return resp


    @app.route("/api/public-status/history", methods=["GET"])
    def api_public_status_history():
        """Historique uptime par component pour les N dernieres heures (default 48).

        Reponse : {hours, components: {bot: [...], dashboard: [...], music_engine: [...]}}
        Chaque entry : {hour_offset, ok, oks, checks}. hour_offset 0 = heure courante,
        -1 = il y a 1h, etc. Les heures sans data sont incluses avec ok=null.
        """
        import datetime as _dt2
        try:
            hours = int(request.args.get("hours", 48))
        except Exception:
            hours = 48
        hours = max(1, min(168, hours))  # cap 1 semaine

        from database import service_uptime_history

        # Build dict hour_bucket -> {ok, oks, checks} par component
        now_hour = _dt2.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        out = {"hours": hours, "components": {}}
        for comp in ("bot", "dashboard", "music_engine"):
            raw = service_uptime_history(comp, hours)
            by_bucket = {row["hour_bucket"]: row for row in raw}
            series = []
            for h in range(hours, -1, -1):
                bucket_dt = now_hour - _dt2.timedelta(hours=h)
                key = bucket_dt.strftime("%Y-%m-%d %H:00")
                row = by_bucket.get(key)
                if row:
                    series.append({
                        "hour":   key,
                        "offset": -h,
                        "ok":     bool(row.get("last_ok")),
                        "oks":    int(row.get("oks") or 0),
                        "checks": int(row.get("checks") or 0),
                    })
                else:
                    series.append({
                        "hour":   key,
                        "offset": -h,
                        "ok":     None,
                        "oks":    0,
                        "checks": 0,
                    })
            # Uptime % sur la fenetre (que sur les heures avec data)
            with_data = [s for s in series if s["checks"] > 0]
            if with_data:
                total_checks = sum(s["checks"] for s in with_data)
                total_oks    = sum(s["oks"]    for s in with_data)
                uptime_pct   = round(100.0 * total_oks / total_checks, 2)
            else:
                uptime_pct = None
            out["components"][comp] = {
                "series":     series,
                "uptime_pct": uptime_pct,
            }

        resp = jsonify(out)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Cache-Control"]                = "public, max-age=30"
        return resp


    # ===== Tickets dashboard =====
