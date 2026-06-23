"""Owner-only : test d'une feature Booster de cartes (9 cartes, epic+ garantie a la fin)."""
import os as _os
import random as _r

from flask import render_template, jsonify, request, send_file


def register_cards_booster_routes(app, deps):
    globals().update(deps)

    def _session_uid():
        from flask import session as _s
        d = _s.get("discord") or {}
        return str(d.get("user_id")) if d.get("user_id") else None

    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    _RENDERS = _os.path.join(_ROOT, "static", "card_renders")
    _BOOSTER_DIR = _os.path.join(_ROOT, "assets", "cardrelated", "booster")

    PACK_SIZE = 9
    # 8 premieres cartes : common/rare/epic (epic = max). Derniere : epic+ garantie.
    _FILLER_WEIGHTS = {"common": 62, "rare": 30, "epic": 8}
    _LAST_WEIGHTS = {"epic": 80, "legendary": 17, "mythic": 3}

    def _renders_roots():
        roots = [_RENDERS]
        try:
            from services.card_render import _ROOT as _CR
            roots.append(_os.path.join(_CR, "static", "card_renders"))
        except Exception:
            pass
        roots.append(_os.path.join(_os.getcwd(), "static", "card_renders"))
        return roots

    def _render_url(cid, image_url):
        if cid:
            for d in _renders_roots():
                for ext in (".webp", ".png"):
                    if _os.path.exists(_os.path.join(d, f"{cid}{ext}")):
                        return f"/static/card_renders/{cid}{ext}"
        if image_url and isinstance(image_url, str) and image_url.startswith("http"):
            return image_url
        return None

    def _pick(rarity):
        from database import card_pick_random_exact_rarity
        return card_pick_random_exact_rarity(rarity)

    def _card_payload(card):
        if not card:
            return None
        rar = card.get("rarity") or "common"
        return {
            "id": card["id"], "name": card.get("name") or "?",
            "rarity": rar, "element": card.get("element") or "",
            "universe": card.get("universe") or "", "subtitle": card.get("subtitle") or "",
            "img": _render_url(card["id"], card.get("image_url")),
            "foil": rar in ("legendary", "mythic", "secret"),
        }

    def _weighted(weights):
        return _r.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]

    @app.route("/cards/img/booster/<state>", methods=["GET"])
    def cards_img_booster(state):
        state = "".join(ch for ch in str(state) if ch.isalpha()).lower()
        fname = {"close": "boosterclose.png", "open": "boosteropen.png"}.get(state)
        if not fname:
            return "", 404
        # plusieurs racines candidates (robuste au cwd/__file__ de prod)
        roots = [_ROOT]
        try:
            from services.card_render import _ROOT as _CR_ROOT
            roots.append(_CR_ROOT)
        except Exception:
            pass
        roots.append(_os.getcwd())
        for root in roots:
            f = _os.path.join(root, "assets", "cardrelated", "booster", fname)
            if _os.path.exists(f):
                return send_file(f, mimetype="image/png", max_age=86400)
        return "", 404

    def _build_pack(force=None):
        """Genere un paquet de 9 cartes (8 common/rare/epic, derniere epic+).
        force in ('legendary','mythic') -> derniere carte forcee (test)."""
        cards = []
        for _ in range(PACK_SIZE - 1):
            rar = _weighted(_FILLER_WEIGHTS)
            c = _pick(rar) or _pick("common") or _pick("rare") or _pick("epic")
            p = _card_payload(c)
            if p:
                cards.append(p)
        if force in ("legendary", "mythic"):
            last_rar = force
        elif force == "min_legendary":
            # premier booster : legendaire minimum (legendaire ou mythic)
            last_rar = _weighted({"legendary": 85, "mythic": 15})
        else:
            last_rar = _weighted(_LAST_WEIGHTS)
        last = _pick(last_rar) or _pick("epic") or _pick("legendary") or _pick("mythic")
        lp = _card_payload(last)
        if lp:
            cards.append(lp)
        return cards

    @app.route("/api/public/booster/open", methods=["POST"])
    def api_public_booster_open():
        # Booster quotidien gratuit (1/jour) : octroie REELLEMENT les cartes.
        uid = _session_uid()
        if not uid:
            return jsonify({"error": "Connecte-toi pour ouvrir ton booster."}), 401
        from database import (daily_booster_claimed_today, daily_booster_claim, user_card_add,
                              daily_booster_ever_claimed)
        is_owner = _is_owner_session()   # owner : ouvertures illimitees (pas de claim quotidien)
        if not is_owner and daily_booster_claimed_today(uid):
            return jsonify({"error": "Booster quotidien déjà ouvert aujourd'hui."}), 400
        # premier booster d'un joueur -> legendaire garantie minimum a la 9e carte
        first = (not is_owner) and (not daily_booster_ever_claimed(uid))
        cards = _build_pack("min_legendary" if first else None)
        if not cards:
            return jsonify({"error": "Booster indisponible pour le moment."}), 500
        if not is_owner and not daily_booster_claim(uid):   # garde anti double-ouverture
            return jsonify({"error": "Booster quotidien déjà ouvert aujourd'hui."}), 400
        for c in cards:
            try:
                user_card_add(uid, c["id"])
            except Exception:
                pass
        return jsonify({"ok": True, "cards": cards, "size": len(cards)})

    @app.route("/api/public/booster/status", methods=["GET"])
    def api_public_booster_status():
        uid = _session_uid()
        from database import daily_booster_claimed_today
        return jsonify({"logged_in": bool(uid),
                        "claimed": daily_booster_claimed_today(uid) if uid else False})
