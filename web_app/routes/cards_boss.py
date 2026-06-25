import os as _os

from flask import render_template, jsonify, request, send_file, session, redirect


def register_cards_boss_routes(app, deps):
    def _session_uid():
        d = session.get("discord") or {}
        return str(d.get("user_id")) if d.get("user_id") else None
    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    _RENDERS = _os.path.join(_ROOT, "static", "card_renders")
    _ELEM_DIR = _os.path.join(_ROOT, "assets", "cardrelated", "Elements")

    _RAR_DIR = _os.path.join(_ROOT, "assets", "cardrelated", "badgerarete")
    _RAR_FILE = {"common": "commun", "rare": "rare", "epic": "epic",
                 "legendary": "legendaire", "mythic": "mythic"}

    @app.route("/cards/img/element/<key>", methods=["GET"])
    def cards_img_element(key):
        key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
        f = _os.path.join(_ELEM_DIR, f"elem_{key}.png")
        if not _os.path.exists(f):
            return "", 404
        return send_file(f, mimetype="image/png", max_age=86400)

    _PDIGIT_DIR = _os.path.join(_ROOT, "assets", "cardrelated", "Chiffre puissance")

    @app.route("/cards/img/pdigit/<name>", methods=["GET"])
    def cards_img_pdigit(name):
        name = "".join(ch for ch in str(name) if ch.isalnum()).lower()
        f = _os.path.join(_PDIGIT_DIR, f"{name}.png")
        if not _os.path.exists(f):
            return "", 404
        return send_file(f, mimetype="image/png", max_age=86400)

    _CR_DIR = _os.path.join(_ROOT, "assets", "cardrelated")

    @app.route("/cards/img/chest/<state>", methods=["GET"])
    def cards_img_chest(state):
        state = "".join(ch for ch in str(state) if ch.isalpha()).lower()
        fname = {"close": "chestclose.png", "open": "chestopen.png"}.get(state)
        if not fname:
            return "", 404
        f = _os.path.join(_CR_DIR, fname)
        if not _os.path.exists(f):
            return "", 404
        return send_file(f, mimetype="image/png", max_age=86400)

    @app.route("/cards/img/rarity/<key>", methods=["GET"])
    def cards_img_rarity(key):
        key = "".join(ch for ch in str(key) if ch.isalnum()).lower()
        fname = _RAR_FILE.get(key)
        if not fname:
            return "", 404
        f = _os.path.join(_RAR_DIR, f"{fname}.png")
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
        """Rendu compose du joueur (art + bordure + etoiles), en cache.
        Filename inclut card_id : si le joueur change de carte, on regenere.
        Le suffixe 'b' = version carte bordee (busting du cache des anciens rendus alt)."""
        if not card_id:
            return None
        fname = f"{bid}_{uid}_{card_id}b.png"
        full = _os.path.join(_PLAYERS_DIR, fname)
        rel = f"/static/card_boss/players/{fname}"
        if _os.path.exists(full):
            return rel
        try:
            from services.card_profile import _card_image_for
            # carte normale AVEC sa bordure (pas l'art alt transparent qui rendait
            # un "cadre de fond" sombre sur le live, ex: Ashe Summer)
            img = _card_image_for(uid, int(card_id), allow_alt=False)
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

    def _denied_page():
        # Connecte mais pas participant : message clair + bouton dashboard (pas d'auto-redirect)
        return ("""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Accès refusé</title>
<style>html,body{height:100%;margin:0}body{display:grid;place-items:center;
background:radial-gradient(circle at 50% 25%,#2a1438,#0a0610);color:#eadff5;
font-family:system-ui,sans-serif;text-align:center}
.box{padding:30px}.t{font-size:27px;font-weight:900;margin-bottom:12px}
.s{color:#b09cc8;font-size:15px;margin-bottom:22px}
.btn{display:inline-block;padding:11px 22px;border-radius:11px;background:var(--gold,#c9a24b);
color:#221700;font-weight:800;text-decoration:none;font-size:15px}</style></head>
<body><div class="box"><div class="t">⛔ Tu ne fais pas partie de ce combat</div>
<div class="s">Seuls les combattants ayant rejoint peuvent suivre le combat en direct.</div>
<a class="btn" href="/dashboard">Retour au dashboard</a></div></body></html>""", 403)

    @app.route("/cards/boss/<int:bid>", methods=["GET"])
    def cards_boss_live(bid):
        import time as _t
        from database import card_boss_get, get_db, boss_participant_get
        boss = card_boss_get(bid)
        if not boss:
            return render_template("404.html"), 404
        # Reserve aux participants connectes (sinon login puis retour ici)
        uid = _session_uid()
        if not uid:
            session["post_login_redirect"] = request.path
            return redirect("/oauth/login")
        is_owner = bool((session.get("discord") or {}).get("is_owner"))
        if not is_owner and not boss_participant_get(bid, uid):
            return _denied_page()
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
        from database import card_customization_get, card_get, combat_power
        parts = boss_participants_list(bid)
        players = []
        team_power = 0
        for p in parts:
            _cd = card_get(p.get("card_id")) if p.get("card_id") else None
            _pw = combat_power(int(p.get("max_hp") or p.get("hp") or 0), int(p.get("atk") or 0))
            team_power += _pw
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
                "power": _pw,
                "damage": int(p.get("damage") or 0),
                "heal": int(p.get("heal") or 0),
                "taken": int(p.get("taken") or 0),
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
                "weak_keys": list(weak),
                "tier": boss.get("tier") or 1,
                "tier_label": BOSS_TIERS.get(boss.get("tier"), {}).get("label", ""),
                "hp": max(0, int(boss.get("hp") or 0)),
                "max_hp": int(boss.get("max_hp") or 1),
                "atk": int(boss.get("atk") or 0),
                "status": boss.get("status") or "",
                "start_at": boss.get("start_at"),
                "power": combat_power(int(boss.get("max_hp") or 0), int(boss.get("atk") or 0)),
                "img": _render_url(boss.get("card_id"), boss.get("image_url")),
            },
            "players": players,
            "team_power": team_power,
            "events": boss_events_since(bid, after),
            "now": _t.time(),
        })

    # ── Edition depuis le dashboard (joueur connecte = participant) ──
    @app.route("/cards/boss/<int:bid>/me", methods=["GET"])
    def cards_boss_me(bid):
        from database import card_boss_get, boss_participant_get
        uid = _session_uid()
        boss = card_boss_get(bid)
        if not boss:
            return jsonify({"error": "not found"}), 404
        if not uid:
            return jsonify({"logged_in": False})
        p = boss_participant_get(bid, uid)
        return jsonify({
            "logged_in": True, "uid": uid,
            "is_participant": bool(p),
            "status": boss.get("status") or "",
            "card_id": (p or {}).get("card_id"),
            "aptitude": (p or {}).get("aptitude") or "",
        })

    @app.route("/cards/boss/<int:bid>/my-cards", methods=["GET"])
    def cards_boss_my_cards(bid):
        from database import (card_boss_get, boss_participant_get, user_card_list,
                              user_card_fusion_map, CARD_ELEMENT_LABELS, element_matchup)
        from services.card_boss import _sort_cards, _card_effectiveness
        uid = _session_uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        boss = card_boss_get(bid)
        if not boss:
            return jsonify({"error": "not found"}), 404
        if not boss_participant_get(bid, uid):
            return jsonify({"error": "pas dans l'equipe"}), 403
        element = request.args.get("element") or None
        if element in (None, "", "all"):
            element = None
        sort = request.args.get("sort") or None
        boss_el = boss.get("element")
        cards = user_card_list(uid)
        fmap = user_card_fusion_map(uid)
        grouped = {}
        for c in cards:
            if element and (c.get("element") or "") != element:
                continue
            cid = c["card_id"]
            if cid not in grouped:
                grouped[cid] = {**c, "count": 0, "stars": int(fmap.get(cid, 0))}
            grouped[cid]["count"] += 1
        rows = _sort_cards(list(grouped.values()), sort, boss_element=boss_el)
        out = []
        for c in rows:
            m = element_matchup(c.get("element") or "", boss_el or "")
            out.append({
                "card_id": c["card_id"], "name": c.get("name") or "?",
                "rarity": c.get("rarity") or "", "element": c.get("element") or "",
                "universe": c.get("universe") or "", "stars": int(c.get("stars", 0)),
                "count": int(c.get("count", 1)),
                "eff": round(_card_effectiveness(c, boss_el), 2),
                "adv": ("up" if m > 1 else ("down" if m < 1 else "")),
            })
        return jsonify({"cards": out, "boss_element": boss_el,
                        "boss_element_label": CARD_ELEMENT_LABELS.get(boss_el, "?")})

    @app.route("/cards/boss/<int:bid>/set-card", methods=["POST"])
    def cards_boss_set_card(bid):
        from database import (card_boss_get, boss_participant_get, boss_participant_update,
                              engaged_combat_stats, user_card_count_owned, card_get)
        from services.card_boss import _event_boss_dmg_mult
        uid = _session_uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        boss = card_boss_get(bid)
        if not boss or boss.get("status") != "recruiting":
            return jsonify({"error": "recrutement terminé"}), 400
        if not boss_participant_get(bid, uid):
            return jsonify({"error": "pas dans l'équipe"}), 403
        cid = (request.json or {}).get("card_id")
        card = card_get(int(cid)) if cid else None
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        if user_card_count_owned(uid, card["id"]) <= 0:
            return jsonify({"error": "carte non possédée"}), 403
        elem = card.get("element") or "eclat"
        stats = engaged_combat_stats(uid, card["id"])
        atk = int(stats["atk"] * _event_boss_dmg_mult(card, boss.get("guild_id")))
        boss_participant_update(bid, uid, element=elem, card_id=card["id"],
                                atk=atk, max_hp=stats["hp"], hp=stats["hp"])
        return jsonify({"ok": True})

    @app.route("/cards/boss/<int:bid>/set-aptitude", methods=["POST"])
    def cards_boss_set_aptitude(bid):
        from database import card_boss_get, boss_participant_get, boss_participant_update
        uid = _session_uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        boss = card_boss_get(bid)
        if not boss or boss.get("status") != "recruiting":
            return jsonify({"error": "recrutement terminé"}), 400
        if not boss_participant_get(bid, uid):
            return jsonify({"error": "pas dans l'équipe"}), 403
        val = (request.json or {}).get("aptitude") or "none"
        valid = {"berserker", "gardien", "soigneur", "duelliste", "executeur", "none"}
        if val not in valid:
            return jsonify({"error": "aptitude invalide"}), 400
        boss_participant_update(bid, uid, aptitude=("" if val == "none" else val))
        return jsonify({"ok": True})

    # ── Chat live du combat ──
    @app.route("/cards/boss/<int:bid>/chat", methods=["GET"])
    def cards_boss_chat_get(bid):
        from database import boss_chat_recent
        try:
            after = int(request.args.get("after", "0"))
        except (ValueError, TypeError):
            after = 0
        return jsonify({"messages": boss_chat_recent(bid, after)})

    @app.route("/cards/boss/<int:bid>/chat", methods=["POST"])
    def cards_boss_chat_post(bid):
        from database import card_boss_get, boss_participant_get, boss_chat_add
        uid = _session_uid()
        if not uid:
            return jsonify({"error": "login"}), 401
        boss = card_boss_get(bid)
        if not boss:
            return jsonify({"error": "not found"}), 404
        is_owner = bool((session.get("discord") or {}).get("is_owner"))
        if not is_owner and not boss_participant_get(bid, uid):
            return jsonify({"error": "pas dans l'équipe"}), 403
        text = str((request.json or {}).get("text") or "").strip()[:300]
        if not text:
            return jsonify({"error": "message vide"}), 400
        name = (session.get("discord") or {}).get("username") or "Joueur"
        cid = boss_chat_add(bid, uid, name, text)
        return jsonify({"ok": True, "id": cid})
