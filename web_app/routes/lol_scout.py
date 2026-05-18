"""LoL Scout sessions : sharable web links pour scouting Clash."""
from flask import render_template, request, jsonify, abort, redirect, url_for, session, Response, stream_with_context
import secrets
import threading
import queue as _queue
import json as _json
import time as _time


# In-memory pub/sub : slug -> list of queue.Queue
# Chaque subscriber SSE a sa propre Queue. publish() push a tous.
_STREAM_SUBS: dict = {}
_STREAM_LOCK = threading.Lock()


def _publish(slug: str, event_type: str, data: dict):
    with _STREAM_LOCK:
        subs = list(_STREAM_SUBS.get(slug, []))
    for q in subs:
        try:
            q.put_nowait((event_type, data))
        except Exception:
            pass


def register_lol_scout_routes(app, deps):
    globals().update(deps)
    from database import (
        lol_scout_session_get, lol_scout_session_stop,
        lol_scout_sessions_list,
        lol_scout_user_join,
        lol_scout_chat_add, lol_scout_chat_list,
        lol_scout_annot_add, lol_scout_annot_list,
    )
    import json as _json

    # Page publique : session de scout via slug.
    # Une fois la session stoppee, le lien n'est plus accessible (404).
    @app.route("/scout/<slug>")
    def lol_scout_page(slug):
        sess = lol_scout_session_get(slug)
        if not sess or sess["status"] != "active":
            abort(404)
        try:
            raw = _json.loads(sess["scout_data"] or "{}")
        except Exception:
            raw = {}
        # Backward compat : ancienne forme = liste de 5 enemies seulement
        if isinstance(raw, list):
            enemies = raw
            allies = []
        else:
            enemies = raw.get("enemies") or []
            allies = raw.get("allies") or []

        # Si allies manque ou vide, on genere 5 slots vides (pour que
        # les amis puissent renseigner leur Riot ID via la web UI).
        ROLE_EMOJI = {"TOP": "🛡️", "JUNGLE": "🌲", "MID": "⚡",
                       "ADC": "🏹", "SUPPORT": "🛡️"}
        if not allies:
            allies = [
                {"role": f"{ROLE_EMOJI[r]} {r}", "riot_id": "", "side": "ally"}
                for r in ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
            ]
        # Side tag par defaut sur enemies (pour les sessions tres anciennes)
        for e in enemies:
            if "side" not in e:
                e["side"] = "enemy"
        try:
            riot_ids = _json.loads(sess["riot_ids"] or "{}")
        except Exception:
            riot_ids = {}

        # Top bans : agrege top_wr de tous les enemies, sort par WR desc,
        # top 10. Inclut le pseudo du joueur pour contexte.
        top_bans = []
        for p in enemies:
            for ban in (p.get("top_wr") or []):
                top_bans.append({
                    "champ":  ban.get("champ"),
                    "slug":   ban.get("slug"),
                    "wr":     ban.get("wr") or 0,
                    "total":  ban.get("total") or 0,
                    "player": p.get("riot_id") or p.get("game_name") or "?",
                    "role":   (p.get("role") or "").split(" ")[-1],
                })
        top_bans.sort(key=lambda x: (-x["wr"], -x["total"]))
        top_bans = top_bans[:10]

        return render_template(
            "scout_session.html",
            session_data=sess,
            enemies=enemies,
            allies=allies,
            riot_ids=riot_ids,
            top_bans=top_bans,
            slug=slug,
        )

    @app.route("/api/scout/<slug>/join", methods=["POST"])
    def api_scout_join(slug):
        sess = lol_scout_session_get(slug)
        if not sess:
            return jsonify({"error": "session_not_found"}), 404
        if sess["status"] != "active":
            return jsonify({"error": "session_stopped"}), 410
        data = request.get_json(silent=True) or {}
        pseudo = (data.get("pseudo") or "").strip()[:24]
        if not pseudo:
            return jsonify({"error": "pseudo_required"}), 400
        info = lol_scout_user_join(slug, pseudo)
        return jsonify(info)

    @app.route("/api/scout/<slug>/chat", methods=["GET"])
    def api_scout_chat_get(slug):
        sess = lol_scout_session_get(slug)
        if not sess or sess["status"] != "active":
            return jsonify({"error": "session_not_found"}), 404
        try:
            since = int(request.args.get("since", 0))
        except Exception:
            since = 0
        msgs = lol_scout_chat_list(slug, since_id=since, limit=200)
        return jsonify({"messages": msgs})

    @app.route("/api/scout/<slug>/chat", methods=["POST"])
    def api_scout_chat_post(slug):
        sess = lol_scout_session_get(slug)
        if not sess:
            return jsonify({"error": "session_not_found"}), 404
        if sess["status"] != "active":
            return jsonify({"error": "session_stopped"}), 410
        data = request.get_json(silent=True) or {}
        pseudo = (data.get("pseudo") or "").strip()[:24]
        color  = (data.get("color") or "#888").strip()[:9]
        message = (data.get("message") or "").strip()
        if not pseudo or not message:
            return jsonify({"error": "pseudo + message required"}), 400
        chat_id = lol_scout_chat_add(slug, pseudo, color, message)
        _publish(slug, "chat", {
            "id": chat_id, "pseudo": pseudo, "color": color,
            "message": message,
        })
        return jsonify({"ok": True, "id": chat_id})

    @app.route("/api/scout/<slug>/annotations", methods=["GET"])
    def api_scout_annot_get(slug):
        sess = lol_scout_session_get(slug)
        if not sess or sess["status"] != "active":
            return jsonify({"error": "session_not_found"}), 404
        try:
            since = int(request.args.get("since", 0))
        except Exception:
            since = 0
        annots = lol_scout_annot_list(slug, since_id=since, limit=500)
        return jsonify({"annotations": annots})

    @app.route("/api/scout/<slug>/annotations", methods=["POST"])
    def api_scout_annot_post(slug):
        sess = lol_scout_session_get(slug)
        if not sess:
            return jsonify({"error": "session_not_found"}), 404
        if sess["status"] != "active":
            return jsonify({"error": "session_stopped"}), 410
        data = request.get_json(silent=True) or {}
        pseudo = (data.get("pseudo") or "").strip()[:24]
        color  = (data.get("color") or "#888").strip()[:9]
        kind   = (data.get("kind") or "stroke").strip()[:24]
        payload = data.get("data") or {}
        if not pseudo:
            return jsonify({"error": "pseudo_required"}), 400
        try:
            data_json = _json.dumps(payload)[:5000]
        except Exception:
            return jsonify({"error": "data_invalid"}), 400
        aid = lol_scout_annot_add(slug, pseudo, color, kind, data_json)
        _publish(slug, "annotation", {
            "id": aid, "pseudo": pseudo, "color": color,
            "kind": kind, "data": payload,
        })
        return jsonify({"ok": True, "id": aid})

    # ===== Edit Riot ID inline (refetch + SSE broadcast) =====
    # side : "enemy" (cote adverse) ou "ally" (notre 5-stack)
    @app.route("/api/scout/<slug>/player", methods=["POST"])
    def api_scout_player_update(slug):
        import asyncio
        import services.riot_api as _ra
        from commandes.lol import _scout_player_data
        from database import get_db
        sess = lol_scout_session_get(slug)
        if not sess or sess["status"] != "active":
            return jsonify({"error": "session_not_found"}), 404
        data = request.get_json(silent=True) or {}
        side = (data.get("side") or "enemy").strip().lower()
        if side not in ("enemy", "ally"):
            return jsonify({"error": "bad_side"}), 400
        role = (data.get("role") or "").strip().upper()
        raw_id = (data.get("riot_id") or "").strip()
        if role not in ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"):
            return jsonify({"error": "bad_role"}), 400
        if not raw_id:
            return jsonify({"error": "riot_id_required"}), 400
        platform = sess["platform"]

        # Re-scout via Riot API : on cree un loop dedie + on cleanup
        # la session aiohttp a la fin.
        _ra._SESSION = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            try:
                new_data = loop.run_until_complete(
                    _scout_player_data(platform, raw_id)
                )
            except Exception as e:
                print(f"[lol/scout-edit] err: {type(e).__name__}: {e}")
                return jsonify({"error": f"refetch_failed: {type(e).__name__}"}), 500
        finally:
            sess_obj = getattr(_ra, "_SESSION", None)
            if sess_obj and not sess_obj.closed:
                try:
                    loop.run_until_complete(sess_obj.close())
                except Exception:
                    pass
            _ra._SESSION = None
            try:
                loop.close()
            except Exception:
                pass
            asyncio.set_event_loop(None)

        role_emoji = {"TOP": "🛡️", "JUNGLE": "🌲", "MID": "⚡",
                       "ADC": "🏹", "SUPPORT": "🛡️"}
        new_entry = dict(new_data)
        new_entry["role"] = f"{role_emoji.get(role, '?')} {role}"
        new_entry["riot_id"] = raw_id
        new_entry["side"] = side

        # Update DB : scout_data{enemies, allies} + riot_ids
        try:
            scout_data = _json.loads(sess["scout_data"] or "{}")
        except Exception:
            scout_data = {}
        if isinstance(scout_data, list):
            scout_data = {"enemies": scout_data, "allies": []}
        enemies = scout_data.get("enemies") or []
        allies  = scout_data.get("allies")  or []
        try:
            riot_ids = _json.loads(sess["riot_ids"] or "{}")
        except Exception:
            riot_ids = {}

        target_list = enemies if side == "enemy" else allies
        replaced = False
        for i, p in enumerate(target_list):
            r = (p.get("role") or "").upper()
            if r.endswith(role):
                target_list[i] = new_entry
                replaced = True
                break
        if not replaced:
            target_list.append(new_entry)

        scout_data = {"enemies": enemies, "allies": allies}
        # On garde riot_ids des enemies dans le dict top-level pour
        # compat /lol scout list dans Discord. Les allies sont dans le
        # scout_data uniquement.
        if side == "enemy":
            riot_ids[role] = raw_id

        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE lol_scout_sessions SET riot_ids=?, scout_data=? WHERE slug=?",
                  (_json.dumps(riot_ids), _json.dumps(scout_data), slug))
        conn.commit()
        conn.close()

        _publish(slug, "player_update", {
            "side": side,
            "role": role,
            "riot_id": raw_id,
            "data": new_entry,
        })
        return jsonify({"ok": True, "data": new_entry})


    # ===== Emblems tier servis depuis le cache local (croppe, ~256x256) =====
    @app.route("/assets/lol-emblem/<tier>")
    def lol_emblem_serve(tier):
        import asyncio as _asyncio
        from pathlib import Path
        from flask import send_file
        import services.riot_api as _ra
        tier_lower = (tier or "").lower().replace(".png", "")
        valid = {"iron","bronze","silver","gold","platinum","emerald",
                  "diamond","master","grandmaster","challenger"}
        if tier_lower not in valid:
            abort(404)
        base_dir = Path(_ra._EMBLEM_CACHE_DIR)
        base_dir.mkdir(parents=True, exist_ok=True)
        path = base_dir / f"{tier_lower}.png"
        if not path.exists():
            # Generate one-shot
            _ra._SESSION = None
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_ra.tier_emblem_file_path(tier.upper()))
            finally:
                sess_obj = getattr(_ra, "_SESSION", None)
                if sess_obj and not sess_obj.closed:
                    try: loop.run_until_complete(sess_obj.close())
                    except Exception: pass
                _ra._SESSION = None
                loop.close()
                _asyncio.set_event_loop(None)
        if path.exists():
            return send_file(str(path), mimetype="image/png")
        abort(404)


    # ===== SSE realtime stream =====
    @app.route("/api/scout/<slug>/stream")
    def api_scout_stream(slug):
        sess = lol_scout_session_get(slug)
        if not sess or sess["status"] != "active":
            abort(404)
        q = _queue.Queue(maxsize=200)
        with _STREAM_LOCK:
            _STREAM_SUBS.setdefault(slug, []).append(q)

        @stream_with_context
        def gen():
            try:
                yield "retry: 5000\n\n"  # client retry 5s si deconnexion
                while True:
                    try:
                        event_type, data = q.get(timeout=20)
                        yield f"event: {event_type}\ndata: {_json.dumps(data)}\n\n"
                    except _queue.Empty:
                        yield ":ping\n\n"  # keep-alive
            finally:
                with _STREAM_LOCK:
                    subs = _STREAM_SUBS.get(slug)
                    if subs and q in subs:
                        subs.remove(q)
                    if subs is not None and len(subs) == 0:
                        _STREAM_SUBS.pop(slug, None)

        resp = Response(gen(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"  # disable nginx buffer
        return resp

    # Owner-only page : liste sessions + stop
    @app.route("/owner/lol-scout")
    def owner_lol_scout_page():
        if not _is_owner_session():
            abort(403)
        sessions = lol_scout_sessions_list(limit=100)
        # Enrich avec un peu de data
        for s in sessions:
            try:
                ids = _json.loads(s["riot_ids"] or "{}")
                s["riot_ids_dict"] = ids
            except Exception:
                s["riot_ids_dict"] = {}
        return render_template("owner_lol_scout.html",
                                active_nav="owner_lol_scout",
                                sessions=sessions)

    @app.route("/api/owner/lol-scout/<slug>/stop", methods=["POST"])
    def api_owner_scout_stop(slug):
        if not _is_owner_session():
            return jsonify({"error": "owner_only"}), 403
        n = lol_scout_session_stop(slug)
        return jsonify({"ok": True, "stopped": n})


def generate_scout_slug() -> str:
    """22 chars unguessable url-safe."""
    return secrets.token_urlsafe(16)[:22]
