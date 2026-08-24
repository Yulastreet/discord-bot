"""Daily card booster (wheel of luck): 9 cards per pack, with at least a guaranteed
epic on the last one. Also serves the pack images."""
import os as _os
import random as _r

from flask import jsonify, send_file

from services.i18n import t


def register_cards_booster_routes(app, deps):
    globals().update(deps)

    def _session_uid():
        from flask import session as _s
        d = _s.get("discord") or {}
        return str(d.get("user_id")) if d.get("user_id") else None

    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    _RENDERS = _os.path.join(_ROOT, "static", "card_renders")

    PACK_SIZE = 9
    # The first 8 cards range from common to epic (epic = cap). The last one is
    # guaranteed epic at minimum (epic/legendary/mythic).
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
        # Several candidate roots (robust against the prod cwd/__file__)
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
        """Build a 9-card pack (8 common/rare/epic, last one epic at minimum).
        force='min_legendary' guarantees a legendary/mythic on the last one (first booster)."""
        cards = []
        for _ in range(PACK_SIZE - 1):
            rar = _weighted(_FILLER_WEIGHTS)
            c = _pick(rar) or _pick("common") or _pick("rare") or _pick("epic")
            p = _card_payload(c)
            if p:
                cards.append(p)
        if force == "min_legendary":
            # A player's very first booster: at least a legendary (otherwise mythic)
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
        # Free daily booster (1/day): the cards are ACTUALLY granted.
        uid = _session_uid()
        if not uid:
            return jsonify({"error": t("api.cards_booster.login_required")}), 401
        from database import (daily_booster_claimed_today, daily_booster_claim, user_card_add,
                              daily_booster_ever_claimed)
        is_owner = _is_owner_session()   # owner: unlimited openings (no daily claim)
        if not is_owner and daily_booster_claimed_today(uid):
            return jsonify({"error": t("api.cards_booster.already_opened_today")}), 400
        # A player's first booster -> legendary guaranteed at minimum on the 9th card
        first = (not is_owner) and (not daily_booster_ever_claimed(uid))
        cards = _build_pack("min_legendary" if first else None)
        if not cards:
            return jsonify({"error": t("api.cards_booster.unavailable")}), 500
        if not is_owner and not daily_booster_claim(uid):   # guard against double opening
            return jsonify({"error": t("api.cards_booster.already_opened_today")}), 400
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
