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
            scout_data = _json.loads(sess["scout_data"] or "[]")
        except Exception:
            scout_data = []
        try:
            riot_ids = _json.loads(sess["riot_ids"] or "{}")
        except Exception:
            riot_ids = {}
        return render_template(
            "scout_session.html",
            session_data=sess,
            scout_data=scout_data,
            riot_ids=riot_ids,
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
