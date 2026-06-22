"""Owner-only : test d'une feature Booster de cartes (9 cartes, epic+ garantie a la fin)."""
import os as _os
import random as _r

from flask import render_template, jsonify, request, send_file


def register_cards_booster_routes(app, deps):
    globals().update(deps)
    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    _RENDERS = _os.path.join(_ROOT, "static", "card_renders")
    _BOOSTER_DIR = _os.path.join(_ROOT, "assets", "cardrelated", "booster")

    PACK_SIZE = 9
    # 8 premieres cartes : common/rare/epic (epic = max). Derniere : epic+ garantie.
    _FILLER_WEIGHTS = {"common": 62, "rare": 30, "epic": 8}
    _LAST_WEIGHTS = {"epic": 80, "legendary": 17, "mythic": 3}

    def _render_url(cid, image_url):
        if cid:
            for ext in (".webp", ".png"):
                if _os.path.exists(_os.path.join(_RENDERS, f"{cid}{ext}")):
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

    @app.route("/owner/cards/booster")
    def owner_cards_booster_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_booster.html", active_nav="owner_booster")

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

    @app.route("/api/owner/booster/open", methods=["POST"])
    def api_owner_booster_open():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        force = (request.json or {}).get("force") or ""   # '', 'legendary', 'mythic'
        cards = []
        # 8 premieres : common/rare/epic
        for _ in range(PACK_SIZE - 1):
            rar = _weighted(_FILLER_WEIGHTS)
            c = _pick(rar) or _pick("common") or _pick("rare") or _pick("epic")
            p = _card_payload(c)
            if p:
                cards.append(p)
        # derniere : epic garantie (ou forcee legendaire/mythic pour le test)
        if force in ("legendary", "mythic"):
            last_rar = force
        else:
            last_rar = _weighted(_LAST_WEIGHTS)
        last = _pick(last_rar) or _pick("epic") or _pick("legendary") or _pick("mythic")
        # si force mais aucune carte de cette rarete, on retombe sur epic+
        lp = _card_payload(last)
        if lp:
            cards.append(lp)
        return jsonify({"ok": True, "cards": cards, "size": len(cards)})
