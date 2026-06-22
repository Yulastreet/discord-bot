import os as _os

from flask import render_template, jsonify, request, send_file


def register_cards_boss_routes(app, deps):
    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    _RENDERS = _os.path.join(_ROOT, "static", "card_renders")
    _ELEM_DIR = _os.path.join(_ROOT, "assets", "cardrelated", "Elements")

    @app.route("/cards/img/element/<key>", methods=["GET"])
    def cards_img_element(key):
        key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
        f = _os.path.join(_ELEM_DIR, f"elem_{key}.png")
        if not _os.path.exists(f):
            return "", 404
        return send_file(f, mimetype="image/png", max_age=86400)

    def _render_url(cid, image_url):
        if cid:
            for ext in (".webp", ".png"):
                if _os.path.exists(_os.path.join(_RENDERS, f"{cid}{ext}")):
                    return f"/static/card_renders/{cid}{ext}"
        if image_url and isinstance(image_url, str) and image_url.startswith("http"):
            return image_url
        return None

    _PLAYERS_DIR = _os.path.join(_os.path.dirname(_RENDERS), "card_boss", "players")

    def _player_img(bid, uid, card_id):
        """Rendu compose du joueur (art + bordure + etoiles + skin alt), en cache.
        Filename inclut card_id : si le joueur change de carte, on regenere."""
        if not card_id:
            return None
        fname = f"{bid}_{uid}_{card_id}.png"
        full = _os.path.join(_PLAYERS_DIR, fname)
        rel = f"/static/card_boss/players/{fname}"
        if _os.path.exists(full):
            return rel
        try:
            from services.card_profile import _card_image_for
            img = _card_image_for(uid, int(card_id), allow_alt=True)
            if img is None:
                return _render_url(card_id, None)
            _os.makedirs(_PLAYERS_DIR, exist_ok=True)
            img.convert("RGBA").save(full, "PNG")
            return rel
        except Exception:
            return _render_url(card_id, None)

    # Apres la fin du combat, on garde la page vivante ce temps (pour afficher les
    # recompenses ~10s aux spectateurs en direct), puis le lien devient mort.
    _DEAD_GRACE = 25

    @app.route("/cards/boss/<int:bid>", methods=["GET"])
    def cards_boss_live(bid):
        import time as _t
        from database import card_boss_get, get_db
        boss = card_boss_get(bid)
        if not boss:
            return render_template("404.html"), 404
        # Combat termine depuis plus que le delai de grace -> lien mort
        if boss.get("status") in ("defeated", "wiped", "expired"):
            conn = get_db(); c = conn.cursor()
            r = c.execute("SELECT MAX(ts) AS t FROM card_boss_event "
                          "WHERE boss_id = ? AND etype = 'end'", (bid,)).fetchone()
            conn.close()
            end_ts = (r["t"] if r else None) or 0
            if end_ts and _t.time() - end_ts > _DEAD_GRACE:
                return render_template("404.html"), 404
        return render_template("boss_live.html", boss_id=bid, boss_name=boss.get("name") or "Boss")

    @app.route("/cards/boss/<int:bid>/state", methods=["GET"])
    def cards_boss_state(bid):
        import time as _t
        from database import (card_boss_get, boss_participants_list, boss_events_since,
                              BOSS_TIERS, CARD_ELEMENT_LABELS, element_weaknesses)
        boss = card_boss_get(bid)
        if not boss:
            return jsonify({"error": "not found"}), 404
        after = request.args.get("after", "0")
        try:
            after = int(after)
        except (ValueError, TypeError):
            after = 0
        from database import card_customization_get, card_get
        parts = boss_participants_list(bid)
        players = []
        for p in parts:
            _cd = card_get(p.get("card_id")) if p.get("card_id") else None
            if str(p["user_id"]).startswith("dummy_"):
                # garde les dummies (tests) mais sans avatar reel
                pass
            players.append({
                "uid": str(p["user_id"]),
                "name": p.get("name") or "?",
                "element": p.get("element") or "",
                "hp": max(0, int(p.get("hp") or 0)),
                "max_hp": int(p.get("max_hp") or p.get("hp") or 1),
                "atk": int(p.get("atk") or 0),
                "aptitude": p.get("aptitude") or "",
                "rarity": (_cd or {}).get("rarity") or "",
                "img": _player_img(bid, str(p["user_id"]), p.get("card_id")),
                "has_border": bool(p.get("card_id") and card_customization_get(str(p["user_id"]), p.get("card_id"))),
            })
        weak = element_weaknesses(boss.get("element"))
        return jsonify({
            "boss": {
                "id": bid,
                "name": boss.get("name") or "Boss",
                "element": boss.get("element") or "",
                "element_label": CARD_ELEMENT_LABELS.get(boss.get("element"), "?"),
                "weak": [CARD_ELEMENT_LABELS.get(w, w) for w in weak],
                "tier": boss.get("tier") or 1,
                "tier_label": BOSS_TIERS.get(boss.get("tier"), {}).get("label", ""),
                "hp": max(0, int(boss.get("hp") or 0)),
                "max_hp": int(boss.get("max_hp") or 1),
                "atk": int(boss.get("atk") or 0),
                "status": boss.get("status") or "",
                "start_at": boss.get("start_at"),
                "img": _render_url(boss.get("card_id"), boss.get("image_url")),
            },
            "players": players,
            "events": boss_events_since(bid, after),
            "now": _t.time(),
        })
