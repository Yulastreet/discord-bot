"""Routes owner-only pour gerer le catalogue de cartes."""
from flask import render_template, request, jsonify


# Page de partage public (balises Open Graph -> previsualisation Discord/Twitter).
# Le crawler lit les <meta>, les humains sont rediriges par le <script>.
_COLLECTION_OG_HTML = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Collection de {{ name }} · TookBot</title>
<meta property="og:type" content="website">
<meta property="og:site_name" content="TookBot">
<meta property="og:title" content="🃏 Collection de {{ name }}">
<meta property="og:description" content="{{ total }} cartes collectionnées sur TookBot. Viens voir le classeur !">
<meta property="og:url" content="{{ og_url }}">
{% if og_image %}<meta property="og:image" content="{{ og_image }}">{% endif %}
<meta name="twitter:card" content="{{ card_type }}">
<meta name="twitter:title" content="🃏 Collection de {{ name }}">
<meta name="twitter:description" content="{{ total }} cartes collectionnées sur TookBot.">
{% if og_image %}<meta name="twitter:image" content="{{ og_image }}">{% endif %}
<meta name="theme-color" content="#b9f23a">
</head><body style="background:#0c0b0e;color:#eee;font-family:sans-serif;text-align:center;padding:60px">
<p>Redirection vers la collection de {{ name }}…</p>
<p><a href="{{ target }}" style="color:#b9f23a">Cliquer ici si rien ne se passe</a></p>
<script>location.replace({{ target|tojson }});</script>
</body></html>"""


# Recompenses de la roue quotidienne. Plus la valeur est forte, plus le poids est
# faible (donc rare). Les chances affichees = weight / somme des weights.
_WHEEL_REWARDS = [
    {"type": "essence",         "value": 2,  "weight": 30, "label": "+2% essences",        "color": "#9aa0a6"},
    {"type": "essence",         "value": 5,  "weight": 20, "label": "+5% essences",        "color": "#4cb5f9"},
    {"type": "epic_roll",       "value": 1,  "weight": 18, "label": "Roll Épique garanti", "color": "#a86dff"},
    {"type": "essence",         "value": 10, "weight": 12, "label": "+10% essences",       "color": "#a86dff"},
    {"type": "epic_roll",       "value": 3,  "weight": 10, "label": "3 Rolls Épique",      "color": "#b06bf2"},
    {"type": "essence",         "value": 20, "weight": 5,  "label": "+20% essences",       "color": "#ffa726"},
    {"type": "golden_roll",     "value": 1,  "weight": 4,  "label": "Golden Roll",         "color": "#ffd23f"},
    {"type": "mythic_fragment", "value": 1,  "weight": 1,  "label": "Fragment Mythic",     "color": "#ff3d57"},
]


def register_cards_owner_routes(app, deps):
    globals().update(deps)

    # ===== PUBLIC =====
    @app.route("/cards")
    def public_cards_page():
        """Page publique : tout le monde peut voir le catalogue (read-only)."""
        import os as _os
        from flask import session as _ses
        dsc = _ses.get("discord") or {}
        uid = str(dsc.get("user_id") or "")
        owner_id = (_os.getenv("DISCORD_OWNER_ID") or "").strip()
        fast_edit_allowed = uid in {owner_id, "235079585509801984"} and bool(uid)
        return render_template("cards_public.html", active_nav="public_cards",
                                 fast_edit_allowed=fast_edit_allowed)


    @app.route("/api/public/cards", methods=["GET"])
    def api_public_cards_list():
        from database import card_list_all, card_count_filtered, card_count_total
        rarity = request.args.get("rarity") or None
        universe = request.args.get("universe") or None
        origin = request.args.get("origin") or None
        search = request.args.get("q") or None
        sort = (request.args.get("sort") or "name_asc").strip()
        try:
            per_page = max(1, min(int(request.args.get("per_page", 50)), 200))
        except ValueError:
            per_page = 50
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        offset = (page - 1) * per_page

        # Reuse helper avec extra filter universe (custom query si dispo)
        from database import get_db
        conn = get_db(); c = conn.cursor()
        # Public exclut tjrs not_obtainable
        where = ["COALESCE(not_obtainable, 0) = 0"]
        params = []
        if rarity:
            where.append("rarity = ?"); params.append(rarity)
        if universe:
            where.append("universe = ?"); params.append(universe)
        if origin:
            where.append("subtitle = ?"); params.append(origin)
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
            like = f"%{search.lower()}%"
            params += [like, like, like]
        sort_sql = {
            "name_asc":     "name ASC",
            "name_desc":    "name DESC",
            "rarity_desc":  "CASE rarity WHEN 'secret' THEN 0 WHEN 'mythic' THEN 1 "
                             "WHEN 'legendary' THEN 2 WHEN 'epic' THEN 3 WHEN 'rare' THEN 4 "
                             "WHEN 'common' THEN 5 ELSE 6 END ASC, name ASC",
            "rarity_asc":   "CASE rarity WHEN 'common' THEN 0 WHEN 'rare' THEN 1 "
                             "WHEN 'epic' THEN 2 WHEN 'legendary' THEN 3 WHEN 'mythic' THEN 4 "
                             "WHEN 'secret' THEN 5 ELSE 6 END ASC, name ASC",
            "universe_asc": "universe ASC, name ASC",
            "subtitle_asc": "subtitle ASC, name ASC",
            "newest":       "id DESC",
            "oldest":       "id ASC",
        }.get(sort, "name ASC")

        # count filtered
        count_sql = f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}"
        filtered = c.execute(count_sql, params).fetchone()["n"]
        # items (public expose aussi source_image_url pour recadrage proposition)
        items_params = params + [per_page, offset]
        rows = c.execute(
            f"SELECT id, name, universe, subtitle, rarity, image_url, source_image_url "
            f"FROM cards WHERE {' AND '.join(where)} "
            f"ORDER BY {sort_sql} LIMIT ? OFFSET ?", items_params).fetchall()
        # source_image_url public uniquement sur cette page (pour cropper),
        # autres usages publics restent image_url uniquement
        items = [dict(r) for r in rows]
        total = c.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
        conn.close()
        return jsonify({
            "items": items,
            "total": int(total),
            "filtered": int(filtered),
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (int(filtered) + per_page - 1) // per_page),
        })


    @app.route("/api/public/cards/<int:cid>", methods=["GET"])
    def api_public_cards_detail(cid):
        from database import card_get
        card = card_get(cid)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        return jsonify({"card": card})


    @app.route("/api/public/cards/universes", methods=["GET"])
    def api_public_cards_universes():
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT universe, COUNT(*) AS n FROM cards "
            "WHERE universe IS NOT NULL AND universe != '' "
            "GROUP BY universe ORDER BY n DESC").fetchall()
        conn.close()
        return jsonify({"items": [{"universe": r["universe"], "count": r["n"]} for r in rows]})


    @app.route("/api/public/cards/origins", methods=["GET"])
    def api_public_cards_origins():
        """Liste des origines (subtitle) distinctes avec compte. Filtre optionnel par univers."""
        from database import get_db
        uni = request.args.get("universe") or None
        conn = get_db(); c = conn.cursor()
        where = ["subtitle IS NOT NULL", "subtitle != ''"]
        params = []
        if uni:
            where.append("universe = ?"); params.append(uni)
        rows = c.execute(
            f"SELECT subtitle, COUNT(*) AS n FROM cards WHERE {' AND '.join(where)} "
            f"GROUP BY subtitle ORDER BY subtitle COLLATE NOCASE", params).fetchall()
        conn.close()
        return jsonify({"items": [{"origin": r["subtitle"], "count": r["n"]} for r in rows]})


    @app.route("/api/public/cards/contributors", methods=["GET"])
    def api_public_cards_contributors():
        """Top contributeurs : nb de suggestions approuvees par personne."""
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT suggester_id, "
            "  MAX(suggester_name) AS name, COUNT(*) AS n "
            "FROM card_suggestions WHERE status = 'approved' "
            "GROUP BY suggester_id ORDER BY n DESC LIMIT 30").fetchall()
        conn.close()
        return jsonify({"items": [
            {"name": (r["name"] or "Anonyme"), "count": int(r["n"])} for r in rows]})


    @app.route("/api/public/cards/my-collection", methods=["GET"])
    def api_public_cards_my_collection():
        """Card_ids possedes par l'utilisateur connecte (pour la vue collection)."""
        from flask import session as _ses
        from database import get_db
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        if not uid:
            return jsonify({"error": "non connecte"}), 401
        conn = get_db(); c = conn.cursor()
        rows = c.execute("SELECT DISTINCT card_id FROM user_cards WHERE user_id = ?",
                         (str(uid),)).fetchall()
        conn.close()
        return jsonify({"card_ids": [int(r["card_id"]) for r in rows]})


    # ===== OBTENTION TEMPS REEL =====
    @app.route("/cards/live")
    def public_cards_live_page():
        return render_template("cards_live.html", active_nav="cards_live")

    @app.route("/api/public/cards/recent-acquisitions", methods=["GET"])
    def api_public_cards_recent_acquisitions():
        """Dernieres cartes obtenues, tous serveurs confondus."""
        from database import get_db
        try:
            limit = max(1, min(int(request.args.get("limit", 60)), 120))
        except ValueError:
            limit = 60
        after = request.args.get("after")  # id du dernier vu (pour le poll)
        conn = get_db(); c = conn.cursor()
        params = []
        # Exclut les cartes ajoutees via owner cheat (from_cheat=1)
        where = "WHERE COALESCE(uc.from_cheat,0) = 0"
        if after and str(after).isdigit():
            where += " AND uc.id > ?"
            params.append(int(after))
        params.append(limit)
        rows = c.execute(
            f"SELECT uc.id, uc.user_id, uc.claimed_at, ca.name, ca.rarity "
            f"FROM user_cards uc JOIN cards ca ON ca.id = uc.card_id "
            f"{where} ORDER BY uc.id DESC LIMIT ?", params).fetchall()
        # Resout le pseudo + avatar via guild_members (1 ligne quelconque par user)
        out = []
        for r in rows:
            m = c.execute("SELECT username, avatar_url FROM guild_members "
                          "WHERE user_id = ? LIMIT 1", (str(r["user_id"]),)).fetchone()
            out.append({
                "id": r["id"],
                "user": (m["username"] if m and m["username"] else "Inconnu"),
                "avatar": (m["avatar_url"] if m else None),
                "name": r["name"],
                "rarity": r["rarity"],
                "at": r["claimed_at"],
            })
        conn.close()
        return jsonify({"items": out})

    # ===== CLASSEMENT DES GUILDES (public) =====
    @app.route("/cards/guilds")
    def public_cards_guilds_page():
        return render_template("cards_guilds.html", active_nav="public_guilds")

    @app.route("/api/public/guilds/top", methods=["GET"])
    def api_public_guilds_top():
        from database import (guild_top, get_guild_config, guild_member_ids,
                              compute_player_combat_stats, combat_power, get_db)
        cfg = get_guild_config()
        maxlv = int(cfg.get("max_level", 60))
        conn = get_db(); c = conn.cursor()
        out = []
        for g in guild_top(50):
            total_power = 0
            for uid in guild_member_ids(g["id"]):
                try:
                    st = compute_player_combat_stats(uid)
                    total_power += combat_power(st["hp"], st["atk"])
                except Exception:
                    pass
            owner_name = "Inconnu"
            owner_id = g.get("owner_id")
            if owner_id:
                om = c.execute("SELECT username FROM guild_members WHERE user_id = ? LIMIT 1",
                               (str(owner_id),)).fetchone()
                if om and om["username"]:
                    owner_name = om["username"]
            out.append({
                "id": g["id"], "name": g["name"], "tag": g.get("tag"),
                "level": g["level"], "xp": g["xp"], "max_level": maxlv,
                "members": g.get("members", 0), "bank": g.get("bank", 0),
                "power": total_power,
                "emblem": g.get("emblem"), "color": g.get("color"),
                "owner": owner_name,
            })
        conn.close()
        return jsonify({"items": out})

    @app.route("/assets/power-digit/<digit>")
    def assets_power_digit(digit):
        import os as _os
        from flask import send_from_directory, abort
        if digit not in [str(i) for i in range(10)]:
            abort(404)
        d = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__)))), "assets", "chiffres_puissance")
        return send_from_directory(d, f"{digit}.png", max_age=86400)

    @app.route("/api/public/guilds/<int:gid>/members", methods=["GET"])
    def api_public_guild_members(gid):
        from database import (guild_get, guild_members, get_db,
                              compute_player_combat_stats, combat_power, user_card_count)
        g = guild_get(gid)
        if not g:
            return jsonify({"error": "guilde introuvable"}), 404
        conn = get_db(); c = conn.cursor()
        out = []
        for m in guild_members(gid):
            uid = m["user_id"]
            mm = c.execute("SELECT username, avatar_url FROM guild_members "
                           "WHERE user_id = ? LIMIT 1", (str(uid),)).fetchone()
            try:
                st = compute_player_combat_stats(uid)
                pw = combat_power(st["hp"], st["atk"])
            except Exception:
                pw = 0
            try:
                cards = user_card_count(uid)
            except Exception:
                cards = 0
            out.append({
                "user_id": str(uid),
                "user": (mm["username"] if mm and mm["username"] else "Inconnu"),
                "avatar": (mm["avatar_url"] if mm else None),
                "role": m.get("role", "member"),
                "power": pw, "cards": cards,
                "xp": m.get("xp_contributed", 0),
            })
        conn.close()
        out.sort(key=lambda x: -x["power"])
        return jsonify({"items": out})

    # ===== COLLECTION D'UN MEMBRE (classeur public) =====
    @app.route("/cards/collection")
    @app.route("/cards/collection/<user_id>")
    def public_collection_page(user_id=None):
        from flask import session as _ses
        if not user_id or user_id in ("me", "moi"):
            user_id = (_ses.get("discord") or {}).get("user_id")
        return render_template("cards_collection.html", active_nav="collection",
                               target_user_id=str(user_id or ""))

    def _build_collection_preview(user_id, renders_dir):
        """Mosaique 1200x630 des cartes du joueur (pour la previsualisation Discord).
        Cache : regenere si manquant ou > 1h. Retourne le chemin relatif /static ou None.
        TOUT est dans le try : si Pillow manque ou erreur -> None (fallback avatar)."""
        import os as _os, time as _t
        rel = f"/static/collection_preview/{user_id}.png"
        try:
            from PIL import Image
            from database import user_card_list
            out_dir = _os.path.join(_os.path.dirname(renders_dir), "collection_preview")
            _os.makedirs(out_dir, exist_ok=True)
            out_abs = _os.path.join(out_dir, f"{user_id}.png")
            if _os.path.exists(out_abs) and (_t.time() - _os.path.getmtime(out_abs) < 3600):
                return rel
            # cartes uniques, deja triees par rarete (mythic d'abord)
            seen = []
            for c in user_card_list(user_id):
                if c["card_id"] not in seen:
                    seen.append(c["card_id"])
                if len(seen) >= 12:
                    break
            if not seen:
                return None
            W, H = 1200, 630
            cols, rows = 6, 2
            gap = 12
            cw = (W - gap * (cols + 1)) // cols
            ch = int(cw * 1.5)
            grid_h = rows * ch + gap * (rows + 1)
            top = (H - grid_h) // 2
            canvas = Image.new("RGB", (W, H), (12, 11, 14))
            for i, cid in enumerate(seen[: cols * rows]):
                img = None
                for ext in (".webp", ".png"):
                    p = _os.path.join(renders_dir, f"{cid}{ext}")
                    if _os.path.exists(p):
                        try:
                            img = Image.open(p).convert("RGB")
                        except Exception:
                            img = None
                        break
                if img is None:
                    continue
                # cover-crop vers cw x ch
                sr, dr = img.width / img.height, cw / ch
                if sr > dr:
                    nw = int(img.height * dr); img = img.crop(((img.width - nw) // 2, 0, (img.width + nw) // 2, img.height))
                else:
                    nh = int(img.width / dr); img = img.crop((0, (img.height - nh) // 2, img.width, (img.height + nh) // 2))
                img = img.resize((cw, ch), Image.LANCZOS)
                r, col = divmod(i, cols)
                x = gap + col * (cw + gap)
                y = top + gap + r * (ch + gap)
                canvas.paste(img, (x, y))
            canvas.save(out_abs, "PNG", optimize=True)
            return rel
        except Exception as e:
            print(f"[collection preview] {e}")
            return None

    @app.route("/cards/og-image.png")
    def collection_og_image():
        """Image FIXE de previsualisation (identique pour tous). Servie par Flask
        sous /cards/ (deja proxy nginx) car /assets/ n'est pas servi publiquement."""
        import os as _os
        from flask import send_file, abort
        p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__)))), "assets", "cardrelated", "imageintegration.png")
        if not _os.path.exists(p):
            abort(404)
        return send_file(p, mimetype="image/png", max_age=86400)

    @app.route("/cards/s/<user_id>")
    def collection_share_preview(user_id):
        """Lien de partage public : sert les balises Open Graph (previsualisation
        Discord) puis redirige les humains vers le classeur dashboard."""
        import os as _os
        from flask import request as _rq, render_template_string, redirect as _redir
        target = f"/cards/collection/{user_id}"
        try:
            from database import get_db, user_card_count
            renders_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
                _os.path.dirname(_os.path.abspath(__file__)))), "static", "card_renders")
            conn = get_db(); c = conn.cursor()
            m = c.execute("SELECT username, avatar_url FROM guild_members WHERE user_id = ? LIMIT 1",
                          (str(user_id),)).fetchone()
            conn.close()
            name = (m["username"] if m and m["username"] else "Joueur")
            avatar = (m["avatar_url"] if m else None)
            try:
                total = user_card_count(user_id)
            except Exception:
                total = 0
            # https force (derriere nginx, Flask peut voir http://). Discord exige https.
            base = _rq.host_url.rstrip("/").replace("http://", "https://")
            # Image FIXE identique pour tous, servie depuis /static (nginx direct,
            # fiable - une regle nginx .png peut court-circuiter /cards/*).
            og_image = f"{base}/static/og_share.png"
            return render_template_string(_COLLECTION_OG_HTML, name=name, total=total,
                                          og_image=og_image, og_url=f"{base}/cards/s/{user_id}",
                                          target=target, card_type="summary_large_image")
        except Exception as e:
            print(f"[collection share] {e}")
            return _redir(target)   # jamais de 500 : on redirige direct vers le classeur

    @app.route("/api/public/collection/<user_id>", methods=["GET"])
    def api_public_collection(user_id):
        import os as _os
        from database import (user_card_list, user_card_fusion_map, get_db,
                              event_skin_owned_set)
        renders_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__)))), "static", "card_renders")
        # skins alt debloques par CE joueur -> affiche l'alt a la place
        skin_cards = event_skin_owned_set(user_id)

        def _render_url(cid, image_url, alt=False):
            if alt:
                for ext in (".webp", ".png"):
                    if _os.path.exists(_os.path.join(renders_dir, f"{cid}_alt{ext}")):
                        return f"/static/card_renders/{cid}_alt{ext}"
            for ext in (".webp", ".png"):
                if _os.path.exists(_os.path.join(renders_dir, f"{cid}{ext}")):
                    return f"/static/card_renders/{cid}{ext}"
            return image_url or None

        cards = user_card_list(user_id)
        fmap = user_card_fusion_map(user_id)
        grouped = {}
        for c in cards:
            cid = c["card_id"]
            if cid not in grouped:
                has_alt = cid in skin_cards
                grouped[cid] = {
                    "id": cid, "name": c["name"],
                    "rarity": c.get("rarity"),
                    "origine": c.get("subtitle") or "",
                    "univers": c.get("universe") or "",
                    "element": c.get("element") or "",
                    "stars": int(fmap.get(cid, 0)),
                    "count": 0,
                    "alt": has_alt,
                    "img": _render_url(cid, c.get("image_url"), alt=has_alt),
                }
            grouped[cid]["count"] += 1
        items = list(grouped.values())

        # infos du membre (pseudo + avatar) depuis guild_members
        conn = get_db(); cc = conn.cursor()
        m = cc.execute("SELECT username, avatar_url FROM guild_members "
                       "WHERE user_id = ? LIMIT 1", (str(user_id),)).fetchone()
        conn.close()
        total = sum(it["count"] for it in items)
        return jsonify({
            "user": {
                "id": str(user_id),
                "name": (m["username"] if m and m["username"] else "Joueur"),
                "avatar": (m["avatar_url"] if m else None),
            },
            "total": total, "unique": len(items),
            "cards": items,
        })

    # ===== MA GUILDE (page membre, visibilite par role) =====
    @app.route("/cards/my-guild")
    def public_my_guild_page():
        return render_template("cards_my_guild.html", active_nav="my_guild")

    @app.route("/api/public/guilds/mine", methods=["GET"])
    def api_public_my_guild():
        from flask import session as _ses
        from database import (guild_of_user, guild_member_role, guild_members, get_db,
                              compute_player_combat_stats, combat_power, user_card_count,
                              guild_quests_weekly_get, guild_quests_daily_get,
                              guild_application_list, get_guild_config, guild_xp_log_list)
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        if not uid:
            return jsonify({"in_guild": False, "auth": False})
        g = guild_of_user(uid)
        if not g:
            return jsonify({"in_guild": False, "auth": True})
        gid = g["id"]
        role = guild_member_role(gid, uid)
        can_manage = role in ("master", "officer")
        is_master = role == "master"
        cfg = get_guild_config()
        conn = get_db(); c = conn.cursor()
        members = []; total_power = 0
        for m in guild_members(gid):
            muid = m["user_id"]
            mm = c.execute("SELECT username, avatar_url FROM guild_members "
                           "WHERE user_id = ? LIMIT 1", (str(muid),)).fetchone()
            try:
                st = compute_player_combat_stats(muid)
                pw = combat_power(st["hp"], st["atk"])
            except Exception:
                pw = 0
            try:
                cards = user_card_count(muid)
            except Exception:
                cards = 0
            total_power += pw
            row = {
                "user_id": str(muid),
                "user": (mm["username"] if mm and mm["username"] else "Inconnu"),
                "avatar": (mm["avatar_url"] if mm else None),
                "role": m.get("role", "member"),
                "power": pw, "cards": cards,
                "xp_contributed": m.get("xp_contributed", 0),
            }
            if can_manage:
                row["joined_at"] = m.get("joined_at")
            members.append(row)
        members.sort(key=lambda x: -x["power"])

        # quetes hebdo : contributions visibles uniquement aux gestionnaires (pseudos resolus)
        weekly = guild_quests_weekly_get(gid)
        if not can_manage:
            for q in weekly:
                q.pop("contrib", None)
        else:
            for q in weekly:
                for cc in q.get("contrib", []):
                    cm = c.execute("SELECT username FROM guild_members WHERE user_id = ? LIMIT 1",
                                   (str(cc["user_id"]),)).fetchone()
                    cc["user"] = (cm["username"] if cm and cm["username"] else str(cc["user_id"]))
        daily = guild_quests_daily_get(uid, gid)

        apps = []
        if can_manage:
            for a in guild_application_list(gid):
                am = c.execute("SELECT username, avatar_url FROM guild_members "
                               "WHERE user_id = ? LIMIT 1", (str(a["user_id"]),)).fetchone()
                apps.append({
                    "user_id": str(a["user_id"]),
                    "user": (am["username"] if am and am["username"] else str(a["user_id"])),
                    "avatar": (am["avatar_url"] if am else None),
                    "created_at": a.get("created_at"),
                })

        # historique XP : qui / source / montant (pseudos resolus)
        xp_log = []
        for e in guild_xp_log_list(gid, limit=40):
            lm = c.execute("SELECT username FROM guild_members WHERE user_id = ? LIMIT 1",
                           (str(e["user_id"]),)).fetchone()
            xp_log.append({
                "user": (lm["username"] if lm and lm["username"] else "Inconnu"),
                "amount": e["amount"], "source": e.get("source") or "action",
                "created_at": e.get("created_at"),
            })
        conn.close()

        maxlv = int(cfg.get("max_level", 60))
        # progression dans le niveau (cumul base*growth^(n-2))
        base = float(cfg.get("level_base", 600)); growth = float(cfg.get("level_growth", 1.1))
        def _cumul(lv):
            return sum(base * (growth ** (n - 2)) for n in range(2, lv + 1)) if lv >= 2 else 0
        cur_c = _cumul(g["level"]); nxt_c = _cumul(g["level"] + 1)
        xp_into = max(0, g["xp"] - cur_c); xp_span = max(1, nxt_c - cur_c)
        out = {
            "in_guild": True, "auth": True, "role": role,
            "can_manage": can_manage, "is_master": is_master,
            "guild": {
                "id": gid, "name": g["name"], "tag": g.get("tag"),
                "level": g["level"], "xp": g["xp"], "max_level": maxlv,
                "xp_into": int(xp_into), "xp_span": int(xp_span),
                "bank": g.get("bank", 0), "color": g.get("color"), "emblem": g.get("emblem"),
                "power": total_power,
                "min_power": g.get("min_power") or 0,
                "min_cards": g.get("min_level") or 0,
                "open_join": bool(g.get("open_join")),
            },
            "members": members,
            "weekly": weekly,
            "daily": daily,
            "applications": apps,
            "xp_log": xp_log,
        }
        return jsonify(out)

    @app.route("/api/public/guilds/mine/application/<auid>", methods=["POST", "DELETE"])
    def api_my_guild_application(auid):
        from flask import session as _ses
        from database import (guild_of_user, guild_member_role, guild_application_remove,
                              guild_add_member, guild_member_count, guild_of_user as _gou,
                              guild_meets_requirements, get_guild_config)
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        if not uid:
            return jsonify({"error": "non connecté"}), 401
        g = guild_of_user(uid)
        if not g:
            return jsonify({"error": "pas de guilde"}), 400
        gid = g["id"]
        if guild_member_role(gid, uid) not in ("master", "officer"):
            return jsonify({"error": "réservé Maître/Officier"}), 403
        if request.method == "DELETE":
            guild_application_remove(gid, auid)
            return jsonify({"ok": True})
        # accept
        cfg = get_guild_config()
        if _gou(auid):
            guild_application_remove(gid, auid)
            return jsonify({"error": "déjà dans une guilde"}), 400
        if guild_member_count(gid) >= int(cfg.get("max_members", 30)):
            return jsonify({"error": "guilde pleine"}), 400
        ok, reason = guild_meets_requirements(gid, auid)
        if not ok:
            return jsonify({"error": f"prérequis non remplis : {reason}"}), 400
        guild_add_member(gid, auid, "member")
        guild_application_remove(gid, auid)
        return jsonify({"ok": True})

    # ===== ROUE DE LA CHANCE QUOTIDIENNE =====
    @app.route("/cards/wheel")
    def public_cards_wheel_page():
        return render_template("cards_wheel.html", active_nav="cards_wheel")

    @app.route("/api/public/wheel/status", methods=["GET"])
    def api_public_wheel_status():
        from flask import session as _ses
        from database import (wheel_claim_today, essence_bonus_get, daily_roll_claimed_today,
                              daily_booster_claimed_today)
        import datetime as _dt
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        claimed = wheel_claim_today(uid) if uid else None
        # secondes jusqu'au prochain reset = minuit heure FRANCAISE (Europe/Paris)
        try:
            from zoneinfo import ZoneInfo
            _tz = ZoneInfo("Europe/Paris")
        except Exception:
            _tz = None
        now = _dt.datetime.now(_tz)
        nxt = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        reset_in = int((nxt - now).total_seconds())
        return jsonify({
            "logged_in": bool(uid),
            "claimed": claimed is not None,
            "claim": claimed,
            "bonus_today": essence_bonus_get(uid) if uid else 0,
            "rewards": _WHEEL_REWARDS,
            "reset_in": reset_in,
            "daily_roll_claimed": daily_roll_claimed_today(uid) if uid else False,
            # owner : booster illimite -> jamais "deja ouvert"
            "daily_booster_claimed": (False if _is_owner_session()
                                      else (daily_booster_claimed_today(uid) if uid else False)),
        })

    @app.route("/api/public/daily-roll/claim", methods=["POST"])
    def api_public_daily_roll_claim():
        from flask import session as _ses
        from database import daily_roll_grant
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        if not uid:
            return jsonify({"error": "Connecte-toi pour récupérer ton roll."}), 401
        if not daily_roll_grant(uid):
            return jsonify({"error": "Roll quotidien déjà récupéré aujourd'hui."}), 400
        return jsonify({"ok": True})

    @app.route("/api/public/wheel/recent-wins", methods=["GET"])
    def api_public_wheel_recent_wins():
        """Derniers gains de la roue, tous joueurs confondus (journal wheel_wins)."""
        from database import wheel_wins_recent, get_db
        try:
            limit = max(1, min(int(request.args.get("limit", 40)), 80))
        except ValueError:
            limit = 40
        rows = wheel_wins_recent(limit)
        conn = get_db(); c = conn.cursor()
        out = []
        for r in rows:
            m = c.execute("SELECT username FROM guild_members WHERE user_id = ? LIMIT 1",
                          (str(r["user_id"]),)).fetchone()
            out.append({
                "user": (m["username"] if m and m["username"] else "Inconnu"),
                "type": r["reward_type"],
                "value": r["reward_value"],
                "at": r["won_at"],
            })
        conn.close()
        return jsonify({"items": out})

    @app.route("/api/public/wheel/spin", methods=["POST"])
    def api_public_wheel_spin():
        from flask import session as _ses
        from database import (wheel_claim_today, wheel_record, essence_bonus_set,
                              roll_give_user, wheel_win_log)
        import random as _rnd
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        if not uid:
            return jsonify({"error": "Connecte-toi pour jouer."}), 401
        if wheel_claim_today(uid):
            return jsonify({"error": "Tu as deja tourne la roue aujourd'hui."}), 400
        # Tirage pondere
        total = sum(r["weight"] for r in _WHEEL_REWARDS)
        pick = _rnd.uniform(0, total)
        acc = 0
        won = _WHEEL_REWARDS[-1]
        for r in _WHEEL_REWARDS:
            acc += r["weight"]
            if pick <= acc:
                won = r
                break
        # Enregistre (anti double-spin) AVANT d'octroyer
        if not wheel_record(uid, won["type"], won["value"]):
            return jsonify({"error": "Tu as deja tourne la roue aujourd'hui."}), 400
        wtype = won["type"]
        if wtype == "essence":
            essence_bonus_set(uid, won["value"])
        elif wtype in ("epic_roll", "golden_roll", "mythic_fragment"):
            # items d'inventaire (a utiliser via /cardinventory)
            from database import user_item_add
            user_item_add(uid, wtype, int(won["value"]))
        else:
            roll_give_user(uid, won["value"])
        wheel_win_log(uid, won["type"], won["value"])  # journal du feed en direct
        # XP de guilde (spin de la roue)
        try:
            from database import get_guild_config, guild_member_action_xp
            _xpw = int(get_guild_config().get("xp", {}).get("wheel", 0))
            if _xpw:
                guild_member_action_xp(uid, _xpw, source="roue")
        except Exception as e:
            print(f"[wheel guild xp] {e}")
        # Sequence de defilement facon caisse CS (index gagnant connu du client)
        win_index = _WHEEL_REWARDS.index(won)
        reel = [_rnd.choices(range(len(_WHEEL_REWARDS)),
                             weights=[r["weight"] for r in _WHEEL_REWARDS])[0]
                for _ in range(60)]
        reel[55] = win_index  # case sous le marqueur a l'arret
        return jsonify({"ok": True, "won": won, "reel": reel, "win_pos": 55})

    @app.route("/cards/img/roll/<name>", methods=["GET"])
    def cards_img_roll(name):
        import os as _o
        from flask import send_file as _sf
        name = "".join(ch for ch in str(name) if ch.isalpha()).lower()
        fname = {"roll": "roll.png", "epicroll": "epicroll.png",
                 "goldenroll": "goldenroll.png"}.get(name)
        if not fname:
            return "", 404
        roots = []
        try:
            from services.card_render import _ROOT as _CR
            roots.append(_CR)
        except Exception:
            pass
        roots.append(_o.getcwd())
        roots.append(_o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))))
        for root in roots:
            f = _o.path.join(root, "assets", "cardrelated", "Rolls", fname)
            if _o.path.exists(f):
                return _sf(f, mimetype="image/png", max_age=86400)
        return "", 404

    @app.route("/api/public/cards/<int:cid>/suggest-edit", methods=["POST"])
    def api_public_cards_suggest_edit(cid):
        """User logge propose modif d'une carte existante. Owner valide.
        Accepte JSON (no image) ou multipart (avec 'cropped' file PNG)."""
        from flask import session as _ses
        from database import card_suggestion_add, card_get, get_db
        import os as _os
        from services.cards_overlay import _OUTPUT_DIR as _RENDERS_DIR
        from PIL import Image as _Img
        dsc = _ses.get("discord") or {}
        uid = dsc.get("user_id")
        uname = dsc.get("username") or dsc.get("global_name")
        if not uid:
            return jsonify({"error": "login Discord requis pour proposer une modif"}), 401
        card = card_get(cid)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404

        # Multipart si fichier present, sinon JSON
        is_multipart = "cropped" in request.files
        if is_multipart:
            new_name = (request.form.get("name") or "").strip()[:100]
            new_universe = (request.form.get("universe") or "").strip()[:60]
            new_subtitle = (request.form.get("subtitle") or "").strip()[:80]
            new_rarity = (request.form.get("rarity") or "").strip().lower()
        else:
            data = request.json or {}
            new_name = (data.get("name") or "").strip()[:100]
            new_universe = (data.get("universe") or "").strip()[:60]
            new_subtitle = (data.get("subtitle") or "").strip()[:80]
            new_image_url_json = (data.get("image_url") or "").strip()
            new_rarity = (data.get("rarity") or "").strip().lower()
        if new_rarity and new_rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            new_rarity = None
        if not new_name:
            return jsonify({"error": "nom requis"}), 400

        # Resolve image proposee
        final_image_url = None
        sugg_dir = _os.path.join(_os.path.dirname(_RENDERS_DIR), "card_suggestions")
        _os.makedirs(sugg_dir, exist_ok=True)
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        # Original (non croppe) que le cropper a utilise : preserve pour re-crop futur
        original_image_url = None
        if is_multipart:
            f = request.files["cropped"]
            try:
                cropped = _Img.open(f.stream).convert("RGBA")
            except Exception as e:
                return jsonify({"error": f"PNG invalide : {type(e).__name__}"}), 400
            cropped = cropped.resize((450, 675), _Img.LANCZOS)
            # Save vers _proposed_<uid>_<ts>.png pour unicite
            import time as _t
            fname = f"_proposed_{uid}_{int(_t.time())}.png"
            cropped.convert("RGB").save(_os.path.join(sugg_dir, fname),
                                          "PNG", optimize=True)
            rel = f"/static/card_suggestions/{fname}"
            final_image_url = (public_base + rel) if public_base else rel
            # l'URL d'origine que le cropper a chargee (envoyee par le client)
            original_image_url = (request.form.get("original_url") or "").strip() or None
        else:
            final_image_url = new_image_url_json or None
            # une URL collee EST l'original (non croppe)
            original_image_url = final_image_url

        # Si rien de change : reject
        rarity_changed = new_rarity and new_rarity != (card.get("rarity") or "")
        if (new_name == card["name"]
                and new_universe == (card.get("universe") or "")
                and new_subtitle == (card.get("subtitle") or "")
                and not final_image_url
                and not rarity_changed):
            return jsonify({"error": "aucun changement detecte"}), 400

        sname = uname or f"User#{uid}"
        try:
            sid = card_suggestion_add(
                suggester_id=uid, suggester_name=sname,
                guild_id=None, channel_id=None,
                name=new_name, universe=new_universe or None,
                subtitle=new_subtitle or None,
                image_url=final_image_url or card.get("image_url"),
                source_type="attachment" if is_multipart else "url",
                suggestion_type="edit",
                target_card_id=cid,
                proposed_rarity=new_rarity or None,
                original_image_url=original_image_url,
            )
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "suggestion_id": sid})


    @app.route("/api/public/cards/stats", methods=["GET"])
    def api_public_cards_stats():
        from database import get_db, CARD_RARITY_WEIGHTS
        rarity = request.args.get("rarity") or None
        universe = request.args.get("universe") or None
        search = request.args.get("q") or None

        where = ["COALESCE(not_obtainable, 0) = 0"]; params = []
        if rarity:
            where.append("rarity = ?"); params.append(rarity)
        if universe:
            where.append("universe = ?"); params.append(universe)
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
            like = f"%{search.lower()}%"
            params += [like, like, like]

        conn = get_db(); c = conn.cursor()
        total = c.execute("SELECT COUNT(*) AS n FROM cards "
                          "WHERE COALESCE(not_obtainable, 0) = 0").fetchone()["n"]
        filtered = c.execute(
            f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}",
            params).fetchone()["n"]
        by_rarity_rows = c.execute(
            f"SELECT rarity, COUNT(*) AS n FROM cards WHERE {' AND '.join(where)} "
            f"GROUP BY rarity", params).fetchall()
        by_rarity = {r["rarity"]: r["n"] for r in by_rarity_rows}
        conn.close()

        # Poids globaux par rarete (constants /roll)
        total_weight = sum(CARD_RARITY_WEIGHTS.values())
        weights = {k: v for k, v in CARD_RARITY_WEIGHTS.items()}
        drop_rates_static = {k: round(v * 100 / total_weight, 2)
                              for k, v in weights.items()}

        # Calcul EFFECTIF en tenant compte du fallback /roll :
        # Quand rarete piochee mais count(rarete) == 0 dans filtre,
        # fallback random uniforme dans pool filtre. Redistribue les poids.
        # P(rarete=X)_effectif = (w_X/W si c_X>0 sinon 0)
        #                       + sum_{R, c_R=0} (w_R/W) * (c_X / T)
        all_rarities = list(weights.keys())
        # counts per rarity dans filtre (0 si absent)
        cnt = {r: int(by_rarity.get(r, 0)) for r in all_rarities}
        T = sum(cnt.values()) or 1
        fallback_weight = sum(weights[r] for r in all_rarities if cnt[r] == 0)
        drop_rates_effective = {}
        for X in all_rarities:
            direct = (weights[X] / total_weight) if cnt[X] > 0 else 0
            redistrib = (fallback_weight / total_weight) * (cnt[X] / T) if T > 0 else 0
            drop_rates_effective[X] = round((direct + redistrib) * 100, 2)

        # Probabilite par carte specifique (basee sur effectif)
        per_card_chance = {}
        for X in all_rarities:
            if cnt[X] > 0:
                per_card_chance[X] = round(drop_rates_effective[X] / cnt[X], 4)
            else:
                per_card_chance[X] = 0

        return jsonify({
            "total": int(total),
            "filtered": int(filtered),
            "by_rarity": by_rarity,
            "drop_rates": drop_rates_static,             # weights bruts /roll
            "drop_rates_effective": drop_rates_effective, # apres redistribution
            "per_card_chance": per_card_chance,
            "cooldown_seconds": 3600,
            "filtered_active": bool(rarity or universe or search),
        })


    # ===== OWNER =====
    @app.route("/owner/cards")
    def owner_cards_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_cards.html", active_nav="owner_cards")


    @app.route("/owner/cards-settings")
    def owner_cards_settings_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_cards_settings.html", active_nav="cards_settings")


    @app.route("/owner/cards-event")
    def owner_cards_event_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_cards_event.html", active_nav="cards_event")


    @app.route("/api/owner/cards-settings", methods=["GET", "POST"])
    def api_owner_cards_settings():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_setting, set_setting
        import os as _os
        if request.method == "POST":
            data = request.json or {}
            try:
                days = int(data.get("roll_min_guild_age_days"))
                solo = int(data.get("roll_max_solo_guilds"))
            except (ValueError, TypeError):
                return jsonify({"error": "valeur invalide (entiers attendus)"}), 400
            days = max(0, min(days, 3650))
            solo = max(0, min(solo, 1000))
            set_setting("roll_min_guild_age_days", days)
            set_setting("roll_max_solo_guilds", solo)
            return jsonify({"ok": True, "roll_min_guild_age_days": days,
                            "roll_max_solo_guilds": solo})
        env_override = _os.getenv("ROLL_MIN_GUILD_AGE_DAYS")
        return jsonify({
            "roll_min_guild_age_days": int(get_setting("roll_min_guild_age_days", "7")),
            "roll_max_solo_guilds": int(get_setting("roll_max_solo_guilds", "2")),
            "env_override": env_override,
        })


    @app.route("/api/owner/global-event", methods=["GET", "POST"])
    def api_owner_global_event():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (GLOBAL_EVENTS, global_event_get, global_event_set,
                              global_event_card_counts)
        from database import set_setting, get_setting
        if request.method == "POST":
            data = request.json or {}
            key = (data.get("key") or "").strip()
            if key and key not in GLOBAL_EVENTS:
                return jsonify({"error": "event inconnu"}), 400
            try:
                boost = max(1.0, float(data.get("drop_boost", 2.0)))
                rar_boost = max(1.0, float(data.get("rarity_boost", 1.0)))
            except (ValueError, TypeError):
                return jsonify({"error": "boost invalide"}), 400
            global_event_set(key, drop_boost=boost, rarity_boost=rar_boost)
            # serveurs de test (CSV d'IDs) : si fourni, l'event n'est actif que la-bas
            if "test_guilds" in data:
                tg = (data.get("test_guilds") or "").strip()
                set_setting("global_event_test_guilds", tg)
            return jsonify({"ok": True, **global_event_get(),
                            "test_guilds": get_setting("global_event_test_guilds", "") or ""})
        cur = global_event_get()
        counts = global_event_card_counts()
        # Totaux de cartes OBTENABLES par rarete (pour le % global /roll) + par
        # (univers, rarete) pour les univers qui ont des cartes event (pour /roll univers).
        from database import get_db as _gdb
        conn = _gdb(); cc = conn.cursor()
        rrows = cc.execute("SELECT rarity, COUNT(*) AS n FROM cards "
                           "WHERE COALESCE(not_obtainable,0) = 0 GROUP BY rarity").fetchall()
        rarity_totals = {r["rarity"]: int(r["n"]) for r in rrows}
        urows = cc.execute(
            "SELECT universe, rarity, COUNT(*) AS n FROM cards "
            "WHERE COALESCE(not_obtainable,0) = 0 AND universe IS NOT NULL AND universe != '' "
            "AND universe IN (SELECT DISTINCT universe FROM cards "
            "  WHERE event_key IS NOT NULL AND event_key != '' AND universe IS NOT NULL) "
            "GROUP BY universe, rarity").fetchall()
        conn.close()
        uni_rarity_totals = {}
        for r in urows:
            uni_rarity_totals.setdefault(r["universe"], {})[r["rarity"]] = int(r["n"])
        return jsonify({
            "current": cur,
            "test_guilds": get_setting("global_event_test_guilds", "") or "",
            "rarity_totals": rarity_totals,
            "uni_rarity_totals": uni_rarity_totals,
            "catalog": [{"key": k, "name": v["name"], "emoji": v["emoji"],
                         "cards": counts.get(k, 0)} for k, v in GLOBAL_EVENTS.items()],
        })

    @app.route("/api/owner/global-event/cards/<event_key>", methods=["GET"])
    def api_owner_global_event_cards(event_key):
        """Liste les cartes taguees a un event (pour le gestionnaire dashboard)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db, GLOBAL_EVENTS
        if event_key not in GLOBAL_EVENTS:
            return jsonify({"items": []})
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT id, name, rarity, universe, subtitle, image_url, alt_image_url, "
            "  COALESCE(not_obtainable,0) AS not_obtainable FROM cards "
            "WHERE event_key = ? ORDER BY name COLLATE NOCASE", (event_key,)).fetchall()
        conn.close()
        # not_obtainable=1 -> brouillon (pas encore deploye) ; 0 -> deployee.
        return jsonify({"items": [dict(r) for r in rows]})

    @app.route("/api/owner/global-event/card/<int:cid>/alt", methods=["POST"])
    def api_owner_global_event_alt(cid):
        """Upload le skin ALT d'une carte event (multipart 'image')."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        import os as _os
        from database import card_get, card_alt_set
        if not card_get(cid):
            return jsonify({"error": "carte introuvable"}), 404
        f = request.files.get("image")
        if not f or not f.filename:
            return jsonify({"error": "fichier image requis"}), 400
        try:
            from PIL import Image as _Img
            from services.cards_overlay import _OUTPUT_DIR, _CARD_W, _CARD_H
            # Skin alt : ART BRUT, recadre 2:3, FOND TRANSPARENT conserve (RGBA, pas
            # de fond noir, pas de cadre). Etoiles ajoutees a l'affichage.
            img = _Img.open(f.stream).convert("RGBA")
            sr, dr = img.width / img.height, _CARD_W / _CARD_H
            if sr > dr:
                nw = int(img.height * dr); img = img.crop(((img.width - nw) // 2, 0, (img.width + nw) // 2, img.height))
            else:
                nh = int(img.width / dr); img = img.crop((0, (img.height - nh) // 2, img.width, (img.height + nh) // 2))
            resized = img.resize((_CARD_W, _CARD_H), _Img.LANCZOS)
            _os.makedirs(_OUTPUT_DIR, exist_ok=True)
            resized.save(_os.path.join(_OUTPUT_DIR, f"{cid}_alt.png"), "PNG", optimize=True)
            rel = f"/static/card_renders/{cid}_alt.png"
            public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
            card_alt_set(cid, (public_base + rel) if public_base else rel)
            return jsonify({"ok": True, "alt_image_url": rel})
        except Exception as e:
            return jsonify({"error": f"image invalide : {type(e).__name__}: {e}"}), 400

    @app.route("/api/owner/global-event/card/<int:cid>/image", methods=["POST"])
    def api_owner_global_event_main_image(cid):
        """Remplace l'image PRINCIPALE d'une carte event (multipart 'image')."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        import os as _os
        from database import card_get, get_db
        if not card_get(cid):
            return jsonify({"error": "carte introuvable"}), 404
        f = request.files.get("image")
        if not f or not f.filename:
            return jsonify({"error": "fichier image requis"}), 400
        try:
            from PIL import Image as _Img
            from services.cards_overlay import _OUTPUT_DIR, _CARD_W, _CARD_H, _get_overlay
            conn = get_db(); c = conn.cursor()
            row = c.execute("SELECT rarity FROM cards WHERE id = ?", (cid,)).fetchone()
            conn.close()
            rarity = (row["rarity"] if row else "common") or "common"
            img = _Img.open(f.stream).convert("RGBA")
            sr, dr = img.width / img.height, _CARD_W / _CARD_H
            if sr > dr:
                nw = int(img.height * dr); img = img.crop(((img.width - nw) // 2, 0, (img.width + nw) // 2, img.height))
            else:
                nh = int(img.width / dr); img = img.crop((0, (img.height - nh) // 2, img.width, (img.height + nh) // 2))
            resized = img.resize((_CARD_W, _CARD_H), _Img.LANCZOS)
            canvas = _Img.new("RGBA", (_CARD_W, _CARD_H), (26, 26, 26, 255))
            canvas.paste(resized, (0, 0), resized)
            overlay = _get_overlay(rarity)
            if overlay is not None:
                canvas = _Img.alpha_composite(canvas, overlay)
            _os.makedirs(_OUTPUT_DIR, exist_ok=True)
            canvas.convert("RGB").save(_os.path.join(_OUTPUT_DIR, f"{cid}.png"), "PNG", optimize=True)
            try:
                _wp = _os.path.join(_OUTPUT_DIR, f"{cid}.webp")
                if _os.path.exists(_wp):
                    _os.remove(_wp)
            except Exception:
                pass
            rel = f"/static/card_renders/{cid}.png"
            public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
            conn = get_db(); c = conn.cursor()
            c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                      ((public_base + rel) if public_base else rel, cid))
            conn.commit(); conn.close()
            return jsonify({"ok": True, "image_url": rel})
        except Exception as e:
            return jsonify({"error": f"image invalide : {type(e).__name__}: {e}"}), 400

    @app.route("/api/owner/global-event/card", methods=["POST"])
    def api_owner_global_event_create_card():
        """Cree une carte d'event en BROUILLON (event_key + not_obtainable=1).
        Image : soit par URL (JSON), soit par UPLOAD (multipart champ 'image').
        Elle n'est ni au catalogue ni rollable tant qu'elle n'est pas deployee."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_add, get_db, GLOBAL_EVENTS
        import os as _os
        # JSON (url) OU multipart (fichier) : source de champs unifiee.
        is_multipart = bool(request.files)
        src = request.form if is_multipart else (request.json or {})
        ek = (src.get("event_key") or "").strip()
        if ek not in GLOBAL_EVENTS:
            return jsonify({"error": "event inconnu"}), 400
        name = (src.get("name") or "").strip()
        if not name:
            return jsonify({"error": "nom requis"}), 400
        rarity = (src.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "rareté invalide"}), 400
        cid = card_add(
            name=name,
            universe=(src.get("universe") or "").strip() or None,
            subtitle=(src.get("subtitle") or "").strip() or None,
            rarity=rarity,
            image_url=(src.get("image_url") or "").strip() or None,
            description=(src.get("description") or "").strip() or None,
        )
        # Upload : compose le render (cover-crop 2:3 + overlay rarete) -> static.
        f = request.files.get("image") if is_multipart else None
        if f and f.filename:
            try:
                from PIL import Image as _Img
                from services.cards_overlay import _OUTPUT_DIR, _CARD_W, _CARD_H, _get_overlay
                img = _Img.open(f.stream).convert("RGBA")
                sr, dr = img.width / img.height, _CARD_W / _CARD_H
                if sr > dr:
                    nw = int(img.height * dr); img = img.crop(((img.width - nw) // 2, 0, (img.width + nw) // 2, img.height))
                else:
                    nh = int(img.width / dr); img = img.crop((0, (img.height - nh) // 2, img.width, (img.height + nh) // 2))
                resized = img.resize((_CARD_W, _CARD_H), _Img.LANCZOS)
                canvas = _Img.new("RGBA", (_CARD_W, _CARD_H), (26, 26, 26, 255))
                canvas.paste(resized, (0, 0), resized)
                overlay = _get_overlay(rarity)
                if overlay is not None:
                    canvas = _Img.alpha_composite(canvas, overlay)
                _os.makedirs(_OUTPUT_DIR, exist_ok=True)
                canvas.convert("RGB").save(_os.path.join(_OUTPUT_DIR, f"{cid}.png"), "PNG", optimize=True)
                rel = f"/static/card_renders/{cid}.png"
                public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                final = (public_base + rel) if public_base else rel
                conn0 = get_db(); c0 = conn0.cursor()
                c0.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final, cid))
                conn0.commit(); conn0.close()
            except Exception as e:
                return jsonify({"error": f"image invalide : {type(e).__name__}: {e}"}), 400
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET event_key = ?, not_obtainable = 1 WHERE id = ?", (ek, cid))
        conn.commit(); conn.close()
        return jsonify({"ok": True, "id": cid})

    @app.route("/api/owner/global-event/deploy", methods=["POST"])
    def api_owner_global_event_deploy():
        """Deploie les cartes en brouillon d'un event : not_obtainable 1 -> 0.
        Elles deviennent rollables (et boostees si l'event est actif)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db, GLOBAL_EVENTS
        data = request.json or {}
        ek = (data.get("event_key") or "").strip()
        if ek not in GLOBAL_EVENTS:
            return jsonify({"error": "event inconnu"}), 400
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET not_obtainable = 0 "
                  "WHERE event_key = ? AND COALESCE(not_obtainable,0) = 1", (ek,))
        n = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deployed": int(n)})

    @app.route("/api/owner/global-event/tag", methods=["POST"])
    def api_owner_global_event_tag():
        """Ajoute/retire une carte d'un event. {card_id, event_key} (vide=retirer)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db, GLOBAL_EVENTS, card_get
        data = request.json or {}
        try:
            cid = int(data.get("card_id"))
        except (ValueError, TypeError):
            return jsonify({"error": "card_id invalide"}), 400
        ek = (data.get("event_key") or "").strip()
        if ek and ek not in GLOBAL_EVENTS:
            return jsonify({"error": "event inconnu"}), 400
        if not card_get(cid):
            return jsonify({"error": "carte introuvable"}), 404
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET event_key = ? WHERE id = ?",
                  (ek or None, cid))
        conn.commit(); conn.close()
        return jsonify({"ok": True})


    @app.route("/owner/cards-cheat")
    def owner_cards_cheat_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute("SELECT DISTINCT universe FROM cards "
                         "WHERE universe IS NOT NULL AND universe != '' "
                         "ORDER BY universe").fetchall()
        conn.close()
        return render_template("owner_cards_cheat.html", active_nav="cards_cheat",
                               universes=[r["universe"] for r in rows])


    @app.route("/api/owner/cards-cheat/roll", methods=["POST"])
    def api_owner_cards_cheat_roll():
        """Owner : roll N cartes (categorie optionnelle) direct dans SON inventaire,
        sans apparaitre dans le feed Obtention temps reel (flag from_cheat)."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from flask import session as _ses
        from database import card_roll_random, user_card_add_cheat
        data = request.json or {}
        # user_id cible (sinon : l'owner lui-meme)
        uid = (str(data.get("user_id") or "").strip()
               or (_ses.get("discord") or {}).get("user_id"))
        if not uid or not str(uid).isdigit():
            return jsonify({"error": "user_id invalide"}), 400
        try:
            count = max(1, min(int(data.get("count") or 1), 1000))
        except (ValueError, TypeError):
            return jsonify({"error": "count invalide (1-1000)"}), 400
        universe = (data.get("universe") or "").strip() or None
        rarity = (data.get("rarity") or "").strip().lower() or None
        if rarity and rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "rareté invalide"}), 400

        def _pick():
            # rareté forcee : carte random de cette rareté (+ univers si fourni)
            if rarity:
                from database import get_db as _gdb
                conn = _gdb(); cc = conn.cursor()
                where = "rarity = ? AND COALESCE(not_obtainable,0)=0"
                params = [rarity]
                if universe:
                    where += " AND universe = ?"; params.append(universe)
                row = cc.execute(f"SELECT * FROM cards WHERE {where} ORDER BY RANDOM() LIMIT 1",
                                 params).fetchone()
                conn.close()
                return dict(row) if row else None
            return card_roll_random(universe)

        added = 0
        by_rarity = {}
        for _ in range(count):
            card = _pick()
            if not card:
                break
            user_card_add_cheat(uid, card["id"])
            added += 1
            r = card.get("rarity", "common")
            by_rarity[r] = by_rarity.get(r, 0) + 1
        if added == 0:
            return jsonify({"error": "aucune carte (categorie vide ?)"}), 400
        # compte dans le total de rolls effectues (affiche en bas du profil)
        try:
            from database import roll_total_inc
            roll_total_inc(uid, added)
        except Exception:
            pass
        return jsonify({"ok": True, "added": added, "by_rarity": by_rarity})


    @app.route("/api/owner/cards-cheat/force-next", methods=["POST"])
    def api_owner_cards_cheat_force():
        """Owner : force la carte exacte de SON prochain /roll."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from flask import session as _ses
        from database import forced_roll_set, card_get
        data = request.json or {}
        # user_id cible (sinon : l'owner lui-meme)
        target = (str(data.get("user_id") or "").strip()
                  or (_ses.get("discord") or {}).get("user_id"))
        if not target or not str(target).isdigit():
            return jsonify({"error": "user_id invalide"}), 400
        try:
            cid = int(data.get("card_id"))
        except (ValueError, TypeError):
            return jsonify({"error": "card_id invalide"}), 400
        card = card_get(cid)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        forced_roll_set(target, cid)
        return jsonify({"ok": True, "name": card["name"], "user_id": str(target)})


    # ===== GUILDES : config owner =====
    @app.route("/owner/guilds")
    def owner_guilds_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_guild_config
        return render_template("owner_guilds_config.html", active_nav="owner_guilds",
                               config=get_guild_config())

    @app.route("/api/owner/guilds-config", methods=["GET", "POST"])
    def api_owner_guilds_config():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_guild_config, set_guild_config
        if request.method == "GET":
            return jsonify(get_guild_config())
        data = request.json or {}

        def _i(v, d=0):
            try:
                return max(0, int(v))
            except (ValueError, TypeError):
                return d

        def _f(v, d=1.0):
            try:
                return max(0.0, float(v))
            except (ValueError, TypeError):
                return d

        cur = get_guild_config()
        xp_in = data.get("xp") or {}
        boss_in = xp_in.get("boss") or {}
        cfg = {
            "create_cost": _i(data.get("create_cost"), cur["create_cost"]),
            "max_members": max(1, _i(data.get("max_members"), cur["max_members"])),
            "hop_cooldown_h": _i(data.get("hop_cooldown_h"), cur["hop_cooldown_h"]),
            "daily_xp_cap": _i(data.get("daily_xp_cap"), cur["daily_xp_cap"]),
            "xp": {
                "roll": _i(xp_in.get("roll"), cur["xp"]["roll"]),
                "fusion": _i(xp_in.get("fusion"), cur["xp"]["fusion"]),
                "wheel": _i(xp_in.get("wheel"), cur["xp"]["wheel"]),
                "essence_per_100": _i(xp_in.get("essence_per_100"), cur["xp"]["essence_per_100"]),
                "boss": {str(t): _i(boss_in.get(str(t)), cur["xp"]["boss"].get(str(t), 0))
                         for t in range(1, 6)},
            },
            "level_base": max(1, _i(data.get("level_base"), cur["level_base"])),
            "level_growth": _f(data.get("level_growth"), cur["level_growth"]),
            "max_level": max(2, _i(data.get("max_level"), cur["max_level"])),
            "rewards": [],
        }
        for p in (data.get("rewards") or []):
            cfg["rewards"].append({
                "level": max(1, _i(p.get("level"), 1)),
                "essence_pct": _i(p.get("essence_pct")),
                "xp_pct": _i(p.get("xp_pct")),
                "roll_cd_min": _i(p.get("roll_cd_min")),
                "charges": _i(p.get("charges")),
                "wishlist": _i(p.get("wishlist")),
                "boss_pct": _i(p.get("boss_pct")),
                "bank": bool(p.get("bank")),
                "raids": bool(p.get("raids")),
                "shop": bool(p.get("shop")),
            })
        cfg["rewards"].sort(key=lambda x: x["level"])
        if not cfg["rewards"]:
            cfg["rewards"] = cur["rewards"]
        # Boutique
        shop = []
        for it in (data.get("shop") or []):
            typ = (it.get("type") or "guild_xp").strip()
            if typ not in ("guild_xp", "rolls_all", "essence_all"):
                typ = "guild_xp"
            name = (it.get("name") or "").strip()[:40]
            if not name:
                continue
            shop.append({
                "key": (it.get("key") or name.lower().replace(" ", "_"))[:24],
                "name": name,
                "cost": _i(it.get("cost"), 0),
                "type": typ,
                "value": _i(it.get("value"), 0),
                "desc": (it.get("desc") or "").strip()[:80],
            })
        cfg["shop"] = shop if shop else cur.get("shop", [])
        set_guild_config(cfg)
        return jsonify({"ok": True})

    # ===== GUILD SETTINGS (edition manuelle de n'importe quelle guilde) =====
    @app.route("/owner/guild-settings")
    def owner_guild_settings_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_guild_settings.html", active_nav="owner_guild_settings")

    @app.route("/api/owner/guilds/list", methods=["GET"])
    def api_owner_guilds_list():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import guild_list_all
        q = (request.args.get("q") or "").strip() or None
        return jsonify({"items": guild_list_all(q)})

    @app.route("/api/owner/guilds/<int:gid>", methods=["GET"])
    def api_owner_guild_get(gid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (guild_get, guild_members, get_db,
                              compute_player_combat_stats, combat_power, user_card_count,
                              guild_quests_weekly_get, guild_application_list)
        g = guild_get(gid)
        if not g:
            return jsonify({"error": "guilde introuvable"}), 404
        conn = get_db(); c = conn.cursor()
        members = []
        for m in guild_members(gid):
            uid = m["user_id"]
            mm = c.execute("SELECT username, avatar_url FROM guild_members "
                           "WHERE user_id = ? LIMIT 1", (str(uid),)).fetchone()
            try:
                st = compute_player_combat_stats(uid)
                pw = combat_power(st["hp"], st["atk"])
            except Exception:
                pw = 0
            try:
                cards = user_card_count(uid)
            except Exception:
                cards = 0
            members.append({
                "user_id": str(uid),
                "user": (mm["username"] if mm and mm["username"] else "Inconnu"),
                "avatar": (mm["avatar_url"] if mm else None),
                "role": m.get("role", "member"),
                "xp_contributed": m.get("xp_contributed", 0),
                "joined_at": m.get("joined_at"),
                "power": pw, "cards": cards,
            })
        # quetes hebdo + contributions (pseudos resolus)
        weekly = guild_quests_weekly_get(gid)
        for q in weekly:
            for cc in q.get("contrib", []):
                cm = c.execute("SELECT username FROM guild_members WHERE user_id = ? LIMIT 1",
                               (str(cc["user_id"]),)).fetchone()
                cc["user"] = (cm["username"] if cm and cm["username"] else str(cc["user_id"]))
        # candidatures
        apps = []
        for a in guild_application_list(gid):
            am = c.execute("SELECT username, avatar_url FROM guild_members WHERE user_id = ? LIMIT 1",
                           (str(a["user_id"]),)).fetchone()
            apps.append({
                "user_id": str(a["user_id"]),
                "user": (am["username"] if am and am["username"] else str(a["user_id"])),
                "avatar": (am["avatar_url"] if am else None),
                "created_at": a.get("created_at"),
            })
        conn.close()
        return jsonify({"guild": g, "members": members, "weekly": weekly, "applications": apps})

    @app.route("/api/owner/guilds/<int:gid>", methods=["POST"])
    def api_owner_guild_update(gid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import guild_get, guild_admin_update
        if not guild_get(gid):
            return jsonify({"error": "guilde introuvable"}), 404
        data = request.json or {}
        fields = {}
        if "name" in data:
            nm = (data.get("name") or "").strip()
            if len(nm) < 3:
                return jsonify({"error": "nom trop court (3 min)"}), 400
            fields["name"] = nm[:32]
        if "tag" in data:
            fields["tag"] = ((data.get("tag") or "").strip()[:8]) or None
        for k in ("level", "xp", "bank"):
            if k in data:
                try:
                    fields[k] = max(0, int(data.get(k)))
                except (ValueError, TypeError):
                    pass
        if "color" in data:
            fields["color"] = (data.get("color") or "").strip() or None
        if "emblem" in data:
            fields["emblem"] = (data.get("emblem") or "").strip() or None
        if "owner_id" in data:
            fields["owner_id"] = (data.get("owner_id") or "").strip()
        for k in ("min_power", "min_level"):
            if k in data:
                try:
                    fields[k] = max(0, int(data.get(k)))
                except (ValueError, TypeError):
                    pass
        if "open_join" in data:
            fields["open_join"] = 1 if data.get("open_join") else 0
        if data.get("reset_rename"):
            fields["renamed_at"] = None
        guild_admin_update(gid, fields)
        return jsonify({"ok": True, "guild": guild_get(gid)})

    @app.route("/api/owner/guilds/<int:gid>/application/<auid>", methods=["POST", "DELETE"])
    def api_owner_guild_application(gid, auid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (guild_get, guild_application_remove, guild_add_member,
                              guild_of_user, guild_member_count, get_guild_config)
        if not guild_get(gid):
            return jsonify({"error": "guilde introuvable"}), 404
        if request.method == "DELETE":
            guild_application_remove(gid, auid)
            return jsonify({"ok": True})
        # accept (owner force, ignore prerequis)
        if guild_of_user(auid):
            guild_application_remove(gid, auid)
            return jsonify({"error": "déjà dans une guilde"}), 400
        if guild_member_count(gid) >= int(get_guild_config().get("max_members", 30)):
            return jsonify({"error": "guilde pleine"}), 400
        guild_add_member(gid, auid, "member")
        guild_application_remove(gid, auid)
        return jsonify({"ok": True})

    @app.route("/api/owner/guilds/<int:gid>/member/<user_id>", methods=["POST", "DELETE"])
    def api_owner_guild_member(gid, user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import guild_get, guild_set_role, guild_remove_member, guild_member_role
        if not guild_get(gid):
            return jsonify({"error": "guilde introuvable"}), 404
        if request.method == "DELETE":
            guild_remove_member(gid, user_id)
            return jsonify({"ok": True})
        data = request.json or {}
        role = (data.get("role") or "").strip()
        if role not in ("master", "officer", "member"):
            return jsonify({"error": "role invalide"}), 400
        if not guild_member_role(gid, user_id):
            return jsonify({"error": "pas membre de cette guilde"}), 400
        guild_set_role(gid, user_id, role)
        return jsonify({"ok": True})


    @app.route("/owner/cards/suggestions")
    def owner_cards_suggestions_page():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        return render_template("owner_card_suggestions.html",
                                 active_nav="owner_card_suggestions")


    @app.route("/api/owner/card-suggestions", methods=["GET"])
    def api_owner_card_suggestions_list():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_list
        status = request.args.get("status") or None
        items = card_suggestion_list(status=status, limit=500)
        return jsonify({"items": items})


    @app.route("/api/owner/card-suggestions/<int:sid>/target-card", methods=["GET"])
    def api_owner_card_suggestion_target(sid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_get, card_get
        sugg = card_suggestion_get(sid)
        if not sugg or not sugg.get("target_card_id"):
            return jsonify({"error": "pas de target"}), 404
        card = card_get(sugg["target_card_id"])
        if not card:
            return jsonify({"error": "carte cible introuvable"}), 404
        return jsonify({"card": card})


    @app.route("/api/owner/card-suggestions/leaderboard", methods=["GET"])
    def api_owner_card_sugg_leaderboard():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 100))
        except ValueError:
            limit = 20
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT suggester_id, suggester_name, COUNT(*) AS n "
            "FROM card_suggestions WHERE status = 'approved' "
            "GROUP BY suggester_id ORDER BY n DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return jsonify({"items": [{"user_id": r["suggester_id"],
                                      "name": r["suggester_name"],
                                      "approved_count": r["n"]} for r in rows]})


    @app.route("/api/owner/card-suggestions/counts", methods=["GET"])
    def api_owner_card_sugg_counts():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM card_suggestions GROUP BY status").fetchall()
        conn.close()
        counts = {r["status"]: r["n"] for r in rows}
        return jsonify({
            "pending":  int(counts.get("pending", 0)),
            "approved": int(counts.get("approved", 0)),
            "rejected": int(counts.get("rejected", 0)),
            "countered": int(counts.get("countered", 0)),
            "cancelled": int(counts.get("cancelled", 0)),
            "total":    int(sum(counts.values())),
        })


    @app.route("/api/owner/card-suggestions/pending-count", methods=["GET"])
    def api_owner_card_suggestions_pending_count():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_count_pending
        return jsonify({"count": card_suggestion_count_pending()})


    def _notify_suggestion_resolved(sugg, status, reason=None, dm=True):
        """Cote bot : reagit (✅/❌) sous le message de la suggestion dans le salon
        support, et DM le demandeur en cas de refus (sauf si dm=False -> ex bulk)."""
        try:
            import os as _os
            from database import bot_command_enqueue
            sg = int((_os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip() or 0)
            if not sg:
                return
            bot_command_enqueue(sg, "suggestion_resolved", {
                "channel_id": "1513592894265757716",
                "message_id": sugg.get("forward_message_id"),
                "status": status,
                "suggester_id": sugg.get("suggester_id"),
                "reason": reason,
                "dm": dm,
            })
        except Exception as e:
            print(f"[suggestion notify] {e}")

    def _notify_bulk_reject_dm(suggester_id, count, reason=None):
        """Un seul DM groupe quand plusieurs cartes d'un meme demandeur sont refusees."""
        try:
            import os as _os
            from database import bot_command_enqueue
            sg = int((_os.getenv("SUPPORT_GUILD_ID") or "1502322150822908115").strip() or 0)
            if not sg:
                return
            bot_command_enqueue(sg, "suggestion_bulk_dm", {
                "suggester_id": suggester_id, "count": count, "reason": reason,
            })
        except Exception as e:
            print(f"[suggestion bulk dm] {e}")

    def _approve_apply_image(tcid, new_image_url, original, target, final_rarity, image_changed):
        """Approbation modif image : héberge l'ORIGINAL en local (source_image_url,
        re-crop perenne), bake le render depuis le crop proposé, supprime le crop
        de suggestion consommé. Retourne True si re-bake OK."""
        from services.cards_overlay import composite_card, localize_source
        from database import get_db
        import os as _os
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        # 1. Localise l'original (pour pouvoir re-cropper meme dans 3 ans)
        src_local = None
        if image_changed:
            # La NOUVELLE image proposee EST le nouvel original a heberger (sauf si un
            # original distinct a ete fourni). Avant : retombait sur l'ancienne source.
            orig = original or new_image_url or target.get("source_image_url") or target.get("image_url")
            rel = localize_source(tcid, orig) if orig else None
            if rel:
                src_local = (public_base + rel) if public_base else rel
        # 2. Render = bake du crop proposé (framing) ; sinon re-bake source pour rarete
        src_for_bake = new_image_url if image_changed else (target.get("source_image_url") or target.get("image_url"))
        rebaked = False
        if src_for_bake and "/card_renders/" not in src_for_bake:
            try:
                url = composite_card(src_for_bake, final_rarity, tcid)
                if url:
                    final = (public_base + url) if public_base else url
                    conn = get_db(); c = conn.cursor()
                    if src_local:
                        c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                                   (final, src_local, tcid))
                    else:
                        c.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final, tcid))
                    conn.commit(); conn.close()
                    rebaked = True
            except Exception as e:
                print(f"[approve apply image] err {tcid}: {e}")
        # 3. Supprime le crop de suggestion consommé (evite l'orphelin sur disque)
        if new_image_url and "/card_suggestions/" in new_image_url:
            try:
                fname = new_image_url.split("/card_suggestions/", 1)[1].split("?")[0]
                cropfp = _os.path.join(_os.path.dirname(_RENDERS_DIR), "card_suggestions", fname)
                if _os.path.exists(cropfp):
                    _os.remove(cropfp)
            except Exception:
                pass
        return rebaked

    @app.route("/api/owner/card-suggestions/<int:sid>/approve", methods=["POST"])
    def api_owner_card_suggestion_approve(sid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (card_suggestion_get, card_suggestion_review,
                                card_add, get_db)
        from flask import session as _ses
        data = request.json or {}
        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        reviewer_id = _ses.get("user_id") or "owner"

        sugg_type = sugg.get("suggestion_type") or "new"
        if sugg_type == "edit" and sugg.get("target_card_id"):
            tcid = int(sugg["target_card_id"])
            # Recup card actuelle pour rarete + comparison
            from database import card_get
            target = card_get(tcid)
            if not target:
                return jsonify({"error": "carte cible introuvable"}), 404
            new_image_url = sugg.get("image_url") or ""
            original = sugg.get("original_image_url")

            conn = get_db(); c = conn.cursor()
            fields = []; params = []
            for k in ("name", "universe", "subtitle"):
                v = sugg.get(k)
                if v is not None and v != "":
                    fields.append(f"{k} = ?"); params.append(v)
            # Rarete proposee
            proposed_rar = sugg.get("proposed_rarity")
            if proposed_rar and proposed_rar in ("common","rare","epic","legendary","mythic","secret"):
                fields.append("rarity = ?"); params.append(proposed_rar)
            image_changed = (new_image_url and new_image_url != (target.get("image_url") or ""))
            if fields:
                params.append(tcid)
                c.execute(f"UPDATE cards SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit(); conn.close()

            # Image : héberge l'ORIGINAL en local (re-crop perenne) + render = crop.
            final_rarity = proposed_rar if proposed_rar else target.get("rarity", "common")
            rarity_changed = proposed_rar and proposed_rar != target.get("rarity")
            rebaked = False
            if image_changed or rarity_changed:
                rebaked = _approve_apply_image(tcid, new_image_url, original, target,
                                                final_rarity, image_changed)

            card_suggestion_review(sid, "approved", reviewer_id, created_card_id=tcid)
            _notify_suggestion_resolved(sugg, "approved")
            return jsonify({"ok": True, "card_id": tcid, "type": "edit",
                             "rebaked": rebaked,
                             "rarity_changed": rarity_changed,
                             "new_rarity": final_rarity if rarity_changed else None})

        # type 'new' : create nouvelle carte
        # Priorité : rarity body > proposed_rarity suggestion > common
        rarity = (data.get("rarity") or sugg.get("proposed_rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        try:
            cid = card_add(
                name=sugg["name"],
                universe=sugg.get("universe"),
                subtitle=sugg.get("subtitle"),
                rarity=rarity,
                image_url=sugg.get("image_url"),
                description=f"Suggestion communautaire de {sugg.get('suggester_name', '?')}.",
            )
        except Exception as e:
            return jsonify({"error": f"erreur create : {type(e).__name__}: {e}"}), 500
        # Bake overlay auto pour single approve aussi
        from services.cards_overlay import composite_card
        import os as _os
        src = sugg.get("image_url") or ""
        rebaked = False
        if src and "/card_renders/" not in src and "/card_suggestions/" not in src:
            try:
                url = composite_card(src, rarity, cid)
                if url:
                    public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                    final = (public_base + url) if public_base else url
                    conn = get_db(); c = conn.cursor()
                    c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                               (final, src, cid))
                    conn.commit(); conn.close()
                    rebaked = True
            except Exception as e:
                print(f"[approve bake] err {cid}: {e}")
        card_suggestion_review(sid, "approved", reviewer_id, created_card_id=cid)
        _notify_suggestion_resolved(sugg, "approved")
        return jsonify({"ok": True, "card_id": cid, "type": "new", "rebaked": rebaked})


    def _approve_one_suggestion(sid, rarity_override=None, reviewer_id="owner"):
        """Helper : approve une suggestion + bake overlay auto. Retourne dict."""
        from database import (card_suggestion_get, card_suggestion_review,
                                card_add, card_get, get_db)
        import os as _os
        from services.cards_overlay import composite_card
        sugg = card_suggestion_get(sid)
        if not sugg:
            return {"ok": False, "error": "introuvable"}
        if sugg["status"] != "pending":
            return {"ok": False, "error": f"deja {sugg['status']}"}

        sugg_type = sugg.get("suggestion_type") or "new"
        if sugg_type == "edit" and sugg.get("target_card_id"):
            tcid = int(sugg["target_card_id"])
            target = card_get(tcid)
            if not target:
                return {"ok": False, "error": "carte cible introuvable"}
            new_image_url = sugg.get("image_url") or ""
            original = sugg.get("original_image_url")
            conn = get_db(); c = conn.cursor()
            fields = []; params = []
            for k in ("name", "universe", "subtitle"):
                v = sugg.get(k)
                if v is not None and v != "":
                    fields.append(f"{k} = ?"); params.append(v)
            proposed_rar = sugg.get("proposed_rarity")
            if proposed_rar and proposed_rar in ("common","rare","epic","legendary","mythic","secret"):
                fields.append("rarity = ?"); params.append(proposed_rar)
            image_changed = (new_image_url and new_image_url != (target.get("image_url") or ""))
            if fields:
                params.append(tcid)
                c.execute(f"UPDATE cards SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit(); conn.close()
            final_rarity = proposed_rar if proposed_rar else target.get("rarity", "common")
            rarity_changed = proposed_rar and proposed_rar != target.get("rarity")
            # Image : héberge l'original (re-crop perenne) + render du crop + cleanup
            if image_changed or rarity_changed:
                _approve_apply_image(tcid, new_image_url, original, target,
                                     final_rarity, image_changed)
            card_suggestion_review(sid, "approved", reviewer_id, created_card_id=tcid)
            _notify_suggestion_resolved(sugg, "approved")
            return {"ok": True, "card_id": tcid, "type": "edit"}

        # type 'new' : priorite override > proposed_rarity > common
        rarity = (rarity_override or sugg.get("proposed_rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        try:
            cid = card_add(
                name=sugg["name"],
                universe=sugg.get("universe"),
                subtitle=sugg.get("subtitle"),
                rarity=rarity,
                image_url=sugg.get("image_url"),
                description=f"Suggestion communautaire de {sugg.get('suggester_name', '?')}.",
            )
        except Exception as e:
            return {"ok": False, "error": f"create card : {type(e).__name__}: {e}"}
        # Bake overlay auto
        src = sugg.get("image_url") or ""
        if src and "/card_renders/" not in src and "/card_suggestions/" not in src:
            try:
                url = composite_card(src, rarity, cid)
                if url:
                    public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                    final = (public_base + url) if public_base else url
                    conn = get_db(); c = conn.cursor()
                    c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                               (final, src, cid))
                    conn.commit(); conn.close()
            except Exception as e:
                print(f"[bulk approve bake] err {cid}: {e}")
        card_suggestion_review(sid, "approved", reviewer_id, created_card_id=cid)
        _notify_suggestion_resolved(sugg, "approved")
        return {"ok": True, "card_id": cid, "type": "new"}


    @app.route("/api/owner/card-suggestions/bulk-approve", methods=["POST"])
    def api_owner_card_sugg_bulk_approve():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from flask import session as _ses
        data = request.json or {}
        sids = data.get("sids") or []
        if not isinstance(sids, list) or not sids:
            return jsonify({"error": "sids vide"}), 400
        try:
            sids_int = [int(x) for x in sids][:500]
        except (ValueError, TypeError):
            return jsonify({"error": "sids invalides"}), 400
        default_rarity = (data.get("default_rarity") or "common").strip()
        # rarities optionnel : dict {sid: rarity} pour override par suggestion
        rarities = data.get("rarities") or {}
        if not isinstance(rarities, dict):
            rarities = {}
        reviewer = _ses.get("user_id") or "owner"
        stats = {"approved": 0, "failed": 0, "details": []}
        for sid in sids_int:
            per_rar = (rarities.get(str(sid)) or rarities.get(sid) or default_rarity)
            res = _approve_one_suggestion(sid, rarity_override=per_rar, reviewer_id=reviewer)
            if res.get("ok"): stats["approved"] += 1
            else: stats["failed"] += 1
            stats["details"].append({"sid": sid, **res})
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/card-suggestions/bulk-reject", methods=["POST"])
    def api_owner_card_sugg_bulk_reject():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_get, card_suggestion_review
        from flask import session as _ses
        data = request.json or {}
        sids = data.get("sids") or []
        if not isinstance(sids, list) or not sids:
            return jsonify({"error": "sids vide"}), 400
        try:
            sids_int = [int(x) for x in sids][:500]
        except (ValueError, TypeError):
            return jsonify({"error": "sids invalides"}), 400
        reason = (data.get("reason") or "").strip()[:200] or None
        reviewer = _ses.get("user_id") or "owner"
        rejected = 0; skipped = 0
        by_suggester = {}   # suggester_id -> nb refusees (pour DM groupe)
        for sid in sids_int:
            sugg = card_suggestion_get(sid)
            if not sugg or sugg["status"] != "pending":
                skipped += 1; continue
            card_suggestion_review(sid, "rejected", reviewer, reason=reason)
            # reaction de refus sous chaque message, mais pas de DM par carte (anti-spam)
            _notify_suggestion_resolved(sugg, "rejected", reason, dm=False)
            sgid = sugg.get("suggester_id")
            if sgid:
                by_suggester[sgid] = by_suggester.get(sgid, 0) + 1
            rejected += 1
        # un seul DM par demandeur (groupe si plusieurs cartes)
        for sgid, n in by_suggester.items():
            _notify_bulk_reject_dm(sgid, n, reason)
        return jsonify({"ok": True, "rejected": rejected, "skipped": skipped})


    @app.route("/api/owner/card-suggestions/<int:sid>/approve-cropped", methods=["POST"])
    def api_owner_card_suggestion_approve_cropped(sid):
        """Recoit image cropee (multipart) + cree card avec cette image."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (card_suggestion_get, card_suggestion_review,
                                card_add, get_db)
        from flask import session as _ses
        import os as _os, io as _io
        from PIL import Image as _Img
        from services.cards_overlay import _OUTPUT_DIR as _RENDERS_DIR

        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        rarity = (request.form.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        if "cropped" not in request.files:
            return jsonify({"error": "champ 'cropped' manquant"}), 400
        f = request.files["cropped"]
        try:
            cropped = _Img.open(f.stream).convert("RGBA")
        except Exception as e:
            return jsonify({"error": f"PNG invalide : {type(e).__name__}"}), 400

        tmp_dir = _os.path.join(_os.path.dirname(_RENDERS_DIR), "card_suggestions")
        _os.makedirs(tmp_dir, exist_ok=True)
        cropped = cropped.resize((450, 675), _Img.LANCZOS)

        try:
            cid = card_add(
                name=sugg["name"],
                universe=sugg.get("universe"),
                subtitle=sugg.get("subtitle"),
                rarity=rarity, image_url=None,
                description=f"Suggestion communautaire de {sugg.get('suggester_name', '?')}.",
            )
        except Exception as e:
            return jsonify({"error": f"create card : {type(e).__name__}: {e}"}), 500

        out_path = _os.path.join(tmp_dir, f"{cid}.png")
        cropped.convert("RGB").save(out_path, "PNG", optimize=True)

        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        rel = f"/static/card_suggestions/{cid}.png"
        final_url = (public_base + rel) if public_base else rel
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                  (final_url, sugg.get("image_url"), cid))
        conn.commit(); conn.close()

        reviewer_id = _ses.get("user_id") or "owner"
        card_suggestion_review(sid, "approved", reviewer_id, created_card_id=cid)
        return jsonify({"ok": True, "card_id": cid, "image_url": final_url})


    @app.route("/api/owner/card-suggestions/<int:sid>/reject", methods=["POST"])
    def api_owner_card_suggestion_reject(sid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_suggestion_get, card_suggestion_review
        from flask import session as _ses
        data = request.json or {}
        sugg = card_suggestion_get(sid)
        if not sugg:
            return jsonify({"error": "suggestion introuvable"}), 404
        if sugg["status"] != "pending":
            return jsonify({"error": f"deja {sugg['status']}"}), 400
        reason = (data.get("reason") or "").strip()[:200] or None
        reviewer_id = _ses.get("user_id") or "owner"
        card_suggestion_review(sid, "rejected", reviewer_id, reason=reason)
        _notify_suggestion_resolved(sugg, "rejected", reason)
        return jsonify({"ok": True})


    @app.route("/api/owner/cards", methods=["GET"])
    def api_owner_cards_list():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        rarity = request.args.get("rarity") or None
        universe = request.args.get("universe") or None
        origin = request.args.get("origin") or None
        search = request.args.get("q") or None
        sort = (request.args.get("sort") or "name_asc").strip()
        try:
            per_page = max(1, min(int(request.args.get("per_page", 50)), 500))
        except ValueError:
            per_page = 50
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        offset = (page - 1) * per_page

        conn = get_db(); c = conn.cursor()
        where = ["1=1"]
        params = []
        if rarity:
            where.append("rarity = ?"); params.append(rarity)
        if universe:
            where.append("universe = ?"); params.append(universe)
        if origin:
            where.append("subtitle = ?"); params.append(origin)
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(universe) LIKE ? OR LOWER(subtitle) LIKE ?)")
            like = f"%{search.lower()}%"
            params += [like, like, like]
        sort_sql = {
            "name_asc":     "name ASC",
            "name_desc":    "name DESC",
            "rarity_desc":  "CASE rarity WHEN 'secret' THEN 0 WHEN 'mythic' THEN 1 "
                             "WHEN 'legendary' THEN 2 WHEN 'epic' THEN 3 WHEN 'rare' THEN 4 "
                             "WHEN 'common' THEN 5 ELSE 6 END ASC, name ASC",
            "rarity_asc":   "CASE rarity WHEN 'common' THEN 0 WHEN 'rare' THEN 1 "
                             "WHEN 'epic' THEN 2 WHEN 'legendary' THEN 3 WHEN 'mythic' THEN 4 "
                             "WHEN 'secret' THEN 5 ELSE 6 END ASC, name ASC",
            "universe_asc": "universe ASC, name ASC",
            "subtitle_asc": "subtitle ASC, name ASC",
            "newest":       "id DESC",
            "oldest":       "id ASC",
        }.get(sort, "name ASC")

        filtered = c.execute(
            f"SELECT COUNT(*) AS n FROM cards WHERE {' AND '.join(where)}",
            params).fetchone()["n"]
        rows = c.execute(
            f"SELECT *, "
            f"  (SELECT COUNT(*) FROM user_cards WHERE card_id = cards.id) AS owned_count, "
            f"  (SELECT COUNT(DISTINCT user_id) FROM user_cards WHERE card_id = cards.id) AS owners_count "
            f"FROM cards WHERE {' AND '.join(where)} "
            f"ORDER BY {sort_sql} LIMIT ? OFFSET ?",
            params + [per_page, offset]).fetchall()
        items = [dict(r) for r in rows]
        total = c.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
        conn.close()
        return jsonify({
            "items": items,
            "total": int(total),
            "filtered": int(filtered),
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (int(filtered) + per_page - 1) // per_page),
        })


    @app.route("/api/owner/cards", methods=["POST"])
    def api_owner_cards_create():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_add
        data = request.json or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name requis"}), 400
        rarity = (data.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "rarity invalide"}), 400
        cid = card_add(
            name=name,
            universe=(data.get("universe") or "").strip() or None,
            subtitle=(data.get("subtitle") or "").strip() or None,
            rarity=rarity,
            image_url=(data.get("image_url") or "").strip() or None,
            description=(data.get("description") or "").strip() or None,
            flavor_subtitle=(data.get("flavor_subtitle") or "").strip() or None,
        )
        return jsonify({"ok": True, "id": cid})


    @app.route("/api/owner/cards/<int:cid>", methods=["PATCH"])
    def api_owner_cards_update(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db, GLOBAL_EVENTS
        data = request.json or {}
        allowed = {"name", "universe", "subtitle", "rarity", "image_url", "description", "flavor_subtitle", "not_obtainable", "event_key"}
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return jsonify({"error": "rien a update"}), 400
        if "rarity" in fields and fields["rarity"] not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "rarity invalide"}), 400
        if "not_obtainable" in fields:
            fields["not_obtainable"] = 1 if fields["not_obtainable"] else 0
        if "event_key" in fields:
            ek = (fields["event_key"] or "").strip()
            fields["event_key"] = ek if ek in GLOBAL_EVENTS else None
        conn = get_db(); c = conn.cursor()
        sets = ", ".join(f"{k} = ?" for k in fields.keys())
        c.execute(f"UPDATE cards SET {sets} WHERE id = ?",
                  (*fields.values(), int(cid)))
        ok = c.rowcount > 0
        conn.commit(); conn.close()
        return jsonify({"ok": ok})


    @app.route("/api/owner/cards/<int:cid>", methods=["DELETE"])
    def api_owner_cards_delete(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_delete
        ok = card_delete(cid)
        return jsonify({"ok": ok})


    @app.route("/api/owner/cards/refresh-images", methods=["POST"])
    def api_owner_cards_refresh_images():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_image_fetcher import refresh_all_cards_images
        data = request.json or {}
        force = bool(data.get("force"))
        try:
            stats = refresh_all_cards_images(force_overwrite=force)
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-jikan", methods=["POST"])
    def api_owner_cards_bulk_jikan():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_jikan_bulk import bulk_import_jikan
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 40)), 100))
        except (ValueError, TypeError):
            pages = 40
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_jikan(pages=pages, skip_existing=skip_existing)
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-anilist", methods=["POST"])
    def api_owner_cards_bulk_anilist():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_anilist_bulk import bulk_import_anilist
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 20)), 50))
        except (ValueError, TypeError):
            pages = 20
        skip_existing = bool(data.get("skip_existing", True))
        wipe_first = bool(data.get("wipe_first", False))
        try:
            stats = bulk_import_anilist(pages=pages, skip_existing=skip_existing,
                                          wipe_first=wipe_first)
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/<int:cid>/give", methods=["POST"])
    def api_owner_cards_give(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_get, user_card_add
        data = request.json or {}
        user_id = (data.get("user_id") or "").strip()
        if not user_id or not user_id.isdigit():
            return jsonify({"error": "user_id invalide (ID Discord numerique)"}), 400
        try:
            qty = max(1, min(int(data.get("qty", 1)), 100))
        except (ValueError, TypeError):
            qty = 1
        card = card_get(cid)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        for _ in range(qty):
            user_card_add(user_id, cid)
        return jsonify({"ok": True, "given": qty, "card_name": card.get("name"),
                         "user_id": user_id})


    @app.route("/api/owner/cards/bulk-import-game", methods=["POST"])
    def api_owner_cards_bulk_one_game():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_games import bulk_import_game, GAMES
        data = request.json or {}
        game_key = (data.get("game_key") or "").strip()
        if game_key not in GAMES:
            return jsonify({"error": f"jeu inconnu : {game_key}"}), 400
        try:
            limit = max(10, min(int(data.get("limit", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_game(game_key, limit=limit, skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-games-multi", methods=["POST"])
    def api_owner_cards_bulk_multi_games():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_games import bulk_import_multiple_games, GAMES
        data = request.json or {}
        game_keys = data.get("game_keys") or []
        if not isinstance(game_keys, list) or not game_keys:
            return jsonify({"error": "game_keys liste vide"}), 400
        invalid = [g for g in game_keys if g not in GAMES]
        if invalid:
            return jsonify({"error": f"jeux inconnus : {invalid}"}), 400
        try:
            limit = max(10, min(int(data.get("limit_per_game", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_multiple_games(game_keys, limit_per_game=limit,
                                                  skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/import/start", methods=["POST"])
    def api_owner_cards_import_start():
        """Start an import job en background. Retourne job_id."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.import_jobs import run_async
        data = request.json or {}
        source = (data.get("source") or "").strip().lower()
        params = data.get("params") or {}
        if not isinstance(params, dict):
            return jsonify({"error": "params doit etre dict"}), 400

        if source == "anilist":
            from services.cards_anilist_bulk import bulk_import_anilist
            pages = max(1, min(int(params.get("pages", 20)), 100))
            start_page = max(1, int(params.get("start_page", 1)))
            job_id = run_async(f"Anilist pages {start_page}-{start_page+pages-1}",
                                 bulk_import_anilist,
                                 pages=pages, start_page=start_page,
                                 skip_existing=True)
        elif source == "jikan":
            from services.cards_jikan_bulk import bulk_import_jikan
            pages = max(1, min(int(params.get("pages", 40)), 100))
            job_id = run_async(f"Jikan {pages} pages",
                                 bulk_import_jikan,
                                 pages=pages, skip_existing=True)
        elif source == "superhero":
            from services.cards_superhero_bulk import bulk_import_superhero
            publishers = params.get("publishers")
            if publishers is not None and not isinstance(publishers, list):
                return jsonify({"error": "publishers liste ou null"}), 400
            label = ("SuperHero " + (",".join(publishers) if publishers else "all"))
            job_id = run_async(label, bulk_import_superhero,
                                 publishers=publishers, skip_existing=True)
        elif source == "fandom_game":
            from services.cards_fandom_games import bulk_import_game, GAMES
            game_key = (params.get("game_key") or "").strip()
            if game_key not in GAMES:
                return jsonify({"error": f"jeu inconnu : {game_key}"}), 400
            limit_v = max(10, min(int(params.get("limit", 1000)), 2000))
            job_id = run_async(f"Fandom game {game_key}",
                                 bulk_import_game, game_key,
                                 limit=limit_v, skip_existing=True)
        elif source == "pokemon":
            from services.cards_pokemon_bulk import bulk_import_pokemon
            start_id = max(1, int(params.get("start_id", 1)))
            end_id = max(start_id, min(int(params.get("end_id", 1025)), 1025))
            job_id = run_async(f"Pokémon #{start_id}-{end_id}",
                                 bulk_import_pokemon,
                                 start_id=start_id, end_id=end_id,
                                 skip_existing=True)
        elif source == "hakush":
            from services.cards_hakush_bulk import bulk_import_hakush, GAMES_HAKUSH
            gkey = (params.get("game_key") or "").strip()
            if gkey not in GAMES_HAKUSH:
                return jsonify({"error": f"hakush jeu inconnu : {gkey}"}), 400
            job_id = run_async(f"hakush {gkey}", bulk_import_hakush,
                                 gkey, skip_existing=True)
        elif source == "nookipedia":
            from services.cards_nookipedia_bulk import bulk_import_nookipedia
            job_id = run_async("Animal Crossing villagers",
                                 bulk_import_nookipedia,
                                 skip_existing=True)
        elif source == "fandom_show":
            from services.cards_fandom_films import bulk_import_show, SHOWS
            show_key = (params.get("show_key") or "").strip()
            if show_key not in SHOWS:
                return jsonify({"error": f"show inconnu : {show_key}"}), 400
            limit_v = max(10, min(int(params.get("limit", 1000)), 2000))
            job_id = run_async(f"Fandom show {show_key}",
                                 bulk_import_show, show_key,
                                 limit=limit_v, skip_existing=True)
        else:
            return jsonify({"error": f"source inconnue : {source}"}), 400

        return jsonify({"ok": True, "job_id": job_id})


    @app.route("/api/owner/cards/import/progress/<job_id>", methods=["GET"])
    def api_owner_cards_import_progress(job_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.import_jobs import get_job
        j = get_job(job_id)
        if not j:
            return jsonify({"error": "job introuvable"}), 404
        return jsonify(j)


    @app.route("/api/owner/cards/bulk-import-superhero", methods=["POST"])
    def api_owner_cards_bulk_superhero():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_superhero_bulk import bulk_import_superhero
        data = request.json or {}
        publishers = data.get("publishers")
        if publishers is not None and not isinstance(publishers, list):
            return jsonify({"error": "publishers doit etre liste ou null"}), 400
        skip_existing = bool(data.get("skip_existing", True))
        try:
            limit_v = int(data.get("limit", 0))
            limit_v = limit_v if limit_v > 0 else None
        except (ValueError, TypeError):
            limit_v = None
        try:
            stats = bulk_import_superhero(publishers=publishers,
                                            skip_existing=skip_existing,
                                            limit=limit_v)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-marvel", methods=["POST"])
    def api_owner_cards_bulk_marvel():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_marvel_bulk import bulk_import_marvel
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 15)), 30))
        except (ValueError, TypeError):
            pages = 15
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_marvel(pages=pages, skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-show", methods=["POST"])
    def api_owner_cards_bulk_one_show():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_films import bulk_import_show, SHOWS
        data = request.json or {}
        show_key = (data.get("show_key") or "").strip()
        if show_key not in SHOWS:
            return jsonify({"error": f"show inconnu : {show_key}"}), 400
        try:
            limit = max(10, min(int(data.get("limit", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_show(show_key, limit=limit, skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-shows-multi", methods=["POST"])
    def api_owner_cards_bulk_multi_shows():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_films import bulk_import_multiple_shows, SHOWS
        data = request.json or {}
        show_keys = data.get("show_keys") or []
        if not isinstance(show_keys, list) or not show_keys:
            return jsonify({"error": "show_keys liste vide"}), 400
        invalid = [s for s in show_keys if s not in SHOWS]
        if invalid:
            return jsonify({"error": f"shows inconnus : {invalid}"}), 400
        try:
            limit = max(10, min(int(data.get("limit_per_show", 500)), 2000))
        except (ValueError, TypeError):
            limit = 500
        skip_existing = bool(data.get("skip_existing", True))
        try:
            stats = bulk_import_multiple_shows(show_keys, limit_per_show=limit,
                                                  skip_existing=skip_existing)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/games-available", methods=["GET"])
    def api_owner_cards_games_available():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_fandom_games import GAMES
        return jsonify({"items": [{"key": k, "name": v["name"]} for k, v in GAMES.items()]})


    @app.route("/api/owner/cards/bulk-import-giantbomb", methods=["POST"])
    def api_owner_cards_bulk_gb():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_giantbomb_bulk import bulk_import_giantbomb
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 50)), 200))
        except (ValueError, TypeError):
            pages = 50
        try:
            page_size = max(1, min(int(data.get("page_size", 100)), 100))
        except (ValueError, TypeError):
            page_size = 100
        try:
            start_offset = max(0, int(data.get("start_offset", 0)))
        except (ValueError, TypeError):
            start_offset = 0
        try:
            sleep_between = max(1.0, float(data.get("sleep_between", 17.0)))
        except (ValueError, TypeError):
            sleep_between = 17.0
        skip_existing = bool(data.get("skip_existing", True))
        wipe_first = bool(data.get("wipe_first", False))
        try:
            stats = bulk_import_giantbomb(pages=pages, page_size=page_size,
                                            sleep_between=sleep_between,
                                            skip_existing=skip_existing,
                                            wipe_first=wipe_first,
                                            start_offset=start_offset)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-import-igdb", methods=["POST"])
    def api_owner_cards_bulk_igdb():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_igdb_bulk import bulk_import_igdb
        data = request.json or {}
        try:
            pages = max(1, min(int(data.get("pages", 4)), 20))
        except (ValueError, TypeError):
            pages = 4
        try:
            page_size = max(1, min(int(data.get("page_size", 500)), 500))
        except (ValueError, TypeError):
            page_size = 500
        skip_existing = bool(data.get("skip_existing", True))
        wipe_first = bool(data.get("wipe_first", False))
        try:
            stats = bulk_import_igdb(pages=pages, page_size=page_size,
                                       skip_existing=skip_existing,
                                       wipe_first=wipe_first)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        if isinstance(stats, dict) and stats.get("error"):
            return jsonify({"error": stats["error"]}), 400
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/<int:cid>/recrop", methods=["POST"])
    def api_owner_card_recrop(cid):
        """Recoit l'image deja cropee (multipart) + applique overlay rarete."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        import os as _os, io as _io
        from PIL import Image as _Img
        from services.cards_overlay import _OUTPUT_DIR, _CARD_W, _CARD_H, _get_overlay

        # Body : multipart avec champ 'cropped' (PNG blob de Cropper canvas)
        if "cropped" not in request.files:
            return jsonify({"error": "champ 'cropped' manquant (multipart file)"}), 400
        f = request.files["cropped"]
        try:
            cropped = _Img.open(f.stream).convert("RGBA")
        except Exception as e:
            return jsonify({"error": f"PNG invalide : {type(e).__name__}: {e}"}), 400

        conn = get_db(); c = conn.cursor()
        row = c.execute("SELECT id, name, rarity, source_image_url, image_url "
                         "FROM cards WHERE id = ?", (int(cid),)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "carte introuvable"}), 404
        rarity = row["rarity"] or "common"
        conn.close()

        # Resize au format card standard (cropper a deja envoye 2:3, juste resize precis)
        resized = cropped.resize((_CARD_W, _CARD_H), _Img.LANCZOS)
        canvas = _Img.new("RGBA", (_CARD_W, _CARD_H), (26, 26, 26, 255))
        canvas.paste(resized, (0, 0), resized)
        overlay = _get_overlay(rarity)
        if overlay is not None:
            canvas = _Img.alpha_composite(canvas, overlay)

        _os.makedirs(_OUTPUT_DIR, exist_ok=True)
        out_path = _os.path.join(_OUTPUT_DIR, f"{cid}.png")
        canvas.convert("RGB").save(out_path, "PNG", optimize=True)
        # Purge le cache .webp perime (telechargement distant) qui masquerait ce render
        try:
            _wp = _os.path.join(_OUTPUT_DIR, f"{cid}.webp")
            if _os.path.exists(_wp):
                _os.remove(_wp)
        except Exception:
            pass

        rel = f"/static/card_renders/{cid}.png"
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        final = (public_base + rel) if public_base else rel
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final, cid))
        conn.commit(); conn.close()
        return jsonify({"ok": True, "image_url": final})


    @app.route("/api/owner/cards/<int:cid>/rebake", methods=["POST"])
    def api_owner_card_rebake(cid):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        from services.cards_overlay import composite_card
        import os as _os
        data = request.json or {}
        overlay_rarity = (data.get("overlay_rarity") or "").strip().lower()
        if overlay_rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "overlay_rarity invalide"}), 400
        conn = get_db(); c = conn.cursor()
        row = c.execute("SELECT id, source_image_url, image_url FROM cards WHERE id = ?",
                         (int(cid),)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "carte introuvable"}), 404
        src = row["source_image_url"] or row["image_url"] or ""
        if not src or "/card_renders/" in src or "/card_suggestions/" in src:
            conn.close()
            return jsonify({"error": "pas de source image pour rebake (URL deja locale)"}), 400
        # Save source si pas deja
        if not row["source_image_url"]:
            c.execute("UPDATE cards SET source_image_url = ? WHERE id = ?",
                       (src, cid))
            conn.commit()
        conn.close()
        # Composite avec overlay choisi
        url = composite_card(src, overlay_rarity, int(cid))
        if not url:
            return jsonify({"error": "echec composite"}), 500
        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        final = (public_base + url) if public_base else url
        conn = get_db(); c = conn.cursor()
        c.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final, cid))
        conn.commit(); conn.close()
        return jsonify({"ok": True, "image_url": final})


    @app.route("/api/owner/cards/backfill-source", methods=["POST"])
    def api_owner_cards_backfill_source():
        """Pour les cartes qui ont image_url local mais source_image_url NULL,
        impossible de re-cropper. Cherche dans logs ou propose au moins de
        nullifier l'image_url local pour forcer re-fetch via le wizard.

        Strategie : pour les cartes dont image_url contient /card_renders/,
        on regarde si source est NULL. Si oui, on ne peut rien faire.
        Sinon OK. On expose un compteur de cartes sans source."""
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("SELECT COUNT(*) AS n FROM cards "
                  "WHERE (image_url LIKE '%/card_renders/%' OR image_url LIKE '%/card_suggestions/%') "
                  "AND (source_image_url IS NULL OR source_image_url = '')")
        broken = int(c.fetchone()["n"])
        c.execute("SELECT COUNT(*) AS n FROM cards "
                  "WHERE source_image_url IS NOT NULL AND source_image_url != ''")
        ok = int(c.fetchone()["n"])
        conn.close()
        return jsonify({
            "ok": True,
            "without_source": broken,
            "with_source": ok,
            "note": "Cartes sans source ne peuvent pas etre re-cropees ou re-bakees. Re-importer la source d'origine si possible.",
        })


    @app.route("/api/owner/cards/bake-overlays-async", methods=["POST"])
    def api_owner_cards_bake_async():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.import_jobs import run_async
        from services.cards_overlay import bake_all_cards
        import os as _os
        data = request.json or {}
        force = bool(data.get("force"))
        public_base = (data.get("public_base_url")
                        or _os.getenv("PUBLIC_BASE_URL")
                        or "").strip() or None
        workers = max(1, min(int(data.get("workers", 10)), 30))
        label = f"Baker overlays {'(FORCE)' if force else ''} w={workers}"
        job_id = run_async(label, bake_all_cards,
                             force=force, public_base_url=public_base,
                             workers=workers)
        return jsonify({"ok": True, "job_id": job_id})


    @app.route("/api/owner/cards/bake-overlays", methods=["POST"])
    def api_owner_cards_bake():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from services.cards_overlay import bake_all_cards
        import os as _os
        data = request.json or {}
        force = bool(data.get("force"))
        public_base = (data.get("public_base_url")
                        or _os.getenv("PUBLIC_BASE_URL")
                        or "").strip() or None
        try:
            stats = bake_all_cards(force=force, public_base_url=public_base)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/user/<user_id>/cards/stats", methods=["GET"])
    def api_owner_user_cards_stats(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        total = c.execute("SELECT COUNT(*) AS n FROM user_cards WHERE user_id = ?",
                           (str(user_id),)).fetchone()["n"]
        uniq = c.execute("SELECT COUNT(DISTINCT card_id) AS n FROM user_cards WHERE user_id = ?",
                          (str(user_id),)).fetchone()["n"]
        cds = c.execute("SELECT COUNT(*) AS n FROM user_guild_roll_cooldown WHERE user_id = ?",
                         (str(user_id),)).fetchone()["n"]
        conn.close()
        return jsonify({"total_cards": int(total), "unique_cards": int(uniq),
                         "cooldowns_active": int(cds)})


    @app.route("/api/owner/user/<user_id>/inventory", methods=["GET"])
    def api_owner_user_inventory(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (user_item_get, roll_bonus_available, user_borders_list,
                              currency_get, global_event_get, event_coins_get)
        ev = global_event_get()
        out = {
            "rolls": roll_bonus_available(user_id),
            "epic_rolls": user_item_get(user_id, "epic_roll"),
            "mythic_fragments": user_item_get(user_id, "mythic_fragment"),
            "golden_rolls": user_item_get(user_id, "golden_roll"),
            "essences": currency_get(user_id),
            "borders": [{"name": b["name"], "qty": b["qty"]} for b in user_borders_list(user_id)],
            "event_active": bool(ev.get("active")),
            "event_key": ev.get("key") or "",
            "event_name": ev.get("coin") or "Jetons",
            "event_emoji": ev.get("coin_emoji") or "🎟️",
            "event_coins": event_coins_get(user_id, ev["key"]) if ev.get("active") else 0,
        }
        return jsonify(out)

    @app.route("/api/owner/user/<user_id>/inventory/set", methods=["POST"])
    def api_owner_user_inventory_set(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import (currency_set, roll_set_user, user_item_set,
                              user_item_get, roll_bonus_available, currency_get,
                              global_event_get, event_coins_set, event_coins_get)
        data = request.json or {}

        def _int(v):
            try:
                return max(0, int(v))
            except (TypeError, ValueError):
                return None
        if "essences" in data and _int(data["essences"]) is not None:
            currency_set(user_id, _int(data["essences"]))
        if "rolls" in data and _int(data["rolls"]) is not None:
            roll_set_user(user_id, _int(data["rolls"]))
        if "epic_rolls" in data and _int(data["epic_rolls"]) is not None:
            user_item_set(user_id, "epic_roll", _int(data["epic_rolls"]))
        if "mythic_fragments" in data and _int(data["mythic_fragments"]) is not None:
            user_item_set(user_id, "mythic_fragment", _int(data["mythic_fragments"]))
        if "golden_rolls" in data and _int(data["golden_rolls"]) is not None:
            user_item_set(user_id, "golden_roll", _int(data["golden_rolls"]))
        _ev = global_event_get()
        if "event_coins" in data and _int(data["event_coins"]) is not None and _ev.get("active"):
            event_coins_set(user_id, _ev["key"], _int(data["event_coins"]))
        return jsonify({
            "success": True,
            "rolls": roll_bonus_available(user_id),
            "epic_rolls": user_item_get(user_id, "epic_roll"),
            "mythic_fragments": user_item_get(user_id, "mythic_fragment"),
            "golden_rolls": user_item_get(user_id, "golden_roll"),
            "essences": currency_get(user_id),
            "event_coins": event_coins_get(user_id, _ev["key"]) if _ev.get("active") else 0,
        })

    @app.route("/api/owner/user/<user_id>/cards/clear", methods=["POST"])
    def api_owner_user_cards_clear(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("DELETE FROM user_cards WHERE user_id = ?", (str(user_id),))
        deleted = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": int(deleted)})


    @app.route("/api/owner/user/<user_id>/cards/give-existing", methods=["POST"])
    def api_owner_user_cards_give_existing(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_get, user_card_add_with_flag
        data = request.json or {}
        card_id = data.get("card_id")
        try:
            card_id = int(card_id) if card_id is not None else None
        except (ValueError, TypeError):
            card_id = None
        if not card_id:
            return jsonify({"error": "card_id requis"}), 400
        try:
            qty = max(1, min(int(data.get("qty", 1)), 100))
        except (ValueError, TypeError):
            qty = 1
        not_tradeable = bool(data.get("not_tradeable", False))
        card = card_get(card_id)
        if not card:
            return jsonify({"error": "carte introuvable"}), 404
        for _ in range(qty):
            user_card_add_with_flag(user_id, card_id, not_tradeable=not_tradeable)
        return jsonify({"ok": True, "given": qty, "card_name": card.get("name"),
                         "not_tradeable": not_tradeable})


    @app.route("/api/owner/user/<user_id>/cards/give-new", methods=["POST"])
    def api_owner_user_cards_give_new(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import card_add, user_card_add_with_flag
        from services.cards_overlay import composite_card
        import os as _os
        data = request.json or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name requis"}), 400
        rarity = (data.get("rarity") or "common").strip()
        if rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            rarity = "common"
        try:
            qty = max(1, min(int(data.get("qty", 1)), 100))
        except (ValueError, TypeError):
            qty = 1
        not_tradeable = bool(data.get("not_tradeable", False))
        image_url = (data.get("image_url") or "").strip() or None
        try:
            cid = card_add(
                name=name,
                universe=(data.get("universe") or "").strip() or None,
                subtitle=(data.get("subtitle") or "").strip() or None,
                rarity=rarity, image_url=image_url,
                description=(data.get("description") or "").strip() or None,
                flavor_subtitle=(data.get("flavor_subtitle") or "").strip() or None,
            )
        except Exception as e:
            return jsonify({"error": f"create card : {type(e).__name__}: {e}"}), 500
        # Bake overlay si image fournie
        if image_url and "/card_renders/" not in image_url and "/card_suggestions/" not in image_url:
            try:
                url = composite_card(image_url, rarity, cid)
                if url:
                    public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                    final = (public_base + url) if public_base else url
                    from database import get_db
                    conn = get_db(); c = conn.cursor()
                    c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                               (final, image_url, cid))
                    conn.commit(); conn.close()
            except Exception as e:
                print(f"[give-new bake] err {cid}: {e}")
        for _ in range(qty):
            user_card_add_with_flag(user_id, cid, not_tradeable=not_tradeable)
        return jsonify({"ok": True, "card_id": cid, "given": qty,
                         "not_tradeable": not_tradeable})


    @app.route("/api/owner/user/<user_id>/cards/reset-cooldown", methods=["POST"])
    def api_owner_user_cards_reset_cd(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("DELETE FROM user_guild_roll_cooldown WHERE user_id = ?",
                  (str(user_id),))
        deleted = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": int(deleted)})


    @app.route("/api/owner/user/<user_id>/cards/list", methods=["GET"])
    def api_owner_user_cards_list(user_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT uc.id AS uc_id, uc.card_id, uc.not_tradeable, uc.claimed_at, "
            "c.name, c.rarity, c.universe, c.subtitle, c.image_url "
            "FROM user_cards uc LEFT JOIN cards c ON c.id = uc.card_id "
            "WHERE uc.user_id = ? ORDER BY uc.claimed_at DESC, uc.id DESC",
            (str(user_id),)).fetchall()
        conn.close()
        return jsonify({"items": [dict(r) for r in rows]})


    @app.route("/api/owner/user/<user_id>/cards/<int:uc_id>", methods=["DELETE"])
    def api_owner_user_cards_remove_one(user_id, uc_id):
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        c.execute("DELETE FROM user_cards WHERE id = ? AND user_id = ?",
                  (int(uc_id), str(user_id)))
        deleted = c.rowcount
        conn.commit(); conn.close()
        if not deleted:
            return jsonify({"error": "ligne introuvable"}), 404
        return jsonify({"ok": True})


    @app.route("/api/owner/cards/bulk-update", methods=["POST"])
    def api_owner_cards_bulk_update():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids vide"}), 400
        try:
            ids_int = [int(x) for x in ids][:5000]
        except (ValueError, TypeError):
            return jsonify({"error": "ids invalides"}), 400
        fields = data.get("fields") or {}
        allowed = {"rarity", "universe", "subtitle", "description", "flavor_subtitle"}
        clean = {}
        for k, v in fields.items():
            if k not in allowed: continue
            if v is None or (isinstance(v, str) and v.strip() == ""): continue
            if k == "rarity" and v not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
                continue
            clean[k] = v.strip() if isinstance(v, str) else v
        if not clean:
            return jsonify({"error": "aucun champ a update"}), 400
        sets = ", ".join(f"{k} = ?" for k in clean.keys())
        placeholders = ",".join("?" * len(ids_int))
        sql_params = list(clean.values()) + ids_int
        conn = get_db(); c = conn.cursor()
        c.execute(f"UPDATE cards SET {sets} WHERE id IN ({placeholders})", sql_params)
        updated = c.rowcount
        conn.commit(); conn.close()

        # Si rarete changee : auto-rebake overlay pour ces cards
        rebake_stats = None
        if "rarity" in clean:
            from services.cards_overlay import composite_card
            import os as _os
            public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
            new_rarity = clean["rarity"]
            conn = get_db(); c = conn.cursor()
            rows = c.execute(
                f"SELECT id, source_image_url, image_url FROM cards "
                f"WHERE id IN ({placeholders})", ids_int).fetchall()
            conn.close()
            rebake_stats = {"baked": 0, "skipped": 0, "failed": 0}
            updates = []
            for r in rows:
                src = r["source_image_url"] or r["image_url"] or ""
                if not src or "/card_renders/" in src or "/card_suggestions/" in src:
                    rebake_stats["skipped"] += 1; continue
                try:
                    url = composite_card(src, new_rarity, int(r["id"]))
                except Exception as e:
                    print(f"[bulk_update auto-rebake] err {r['id']}: {e}")
                    rebake_stats["failed"] += 1; continue
                if not url:
                    rebake_stats["failed"] += 1; continue
                final = (public_base + url) if public_base else url
                updates.append((final, int(r["id"])))
                rebake_stats["baked"] += 1
            if updates:
                conn = get_db(); c = conn.cursor()
                for final, rid in updates:
                    c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                               (final, rid))
                conn.commit(); conn.close()

        return jsonify({"ok": True, "updated": updated, "applied": clean,
                         "rebake": rebake_stats})


    @app.route("/api/owner/cards/bulk-rebake", methods=["POST"])
    def api_owner_cards_bulk_rebake():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        from services.cards_overlay import composite_card
        import os as _os
        data = request.json or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids vide"}), 400
        try:
            ids_int = [int(x) for x in ids][:5000]
        except (ValueError, TypeError):
            return jsonify({"error": "ids invalides"}), 400
        overlay_rarity = (data.get("overlay_rarity") or "").strip().lower()
        if overlay_rarity not in ("common", "rare", "epic", "legendary", "mythic", "secret"):
            return jsonify({"error": "overlay_rarity invalide"}), 400

        conn = get_db(); c = conn.cursor()
        placeholders = ",".join("?" * len(ids_int))
        rows = c.execute(
            f"SELECT id, source_image_url, image_url FROM cards "
            f"WHERE id IN ({placeholders})", ids_int).fetchall()
        conn.close()

        public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        stats = {"baked": 0, "skipped": 0, "failed": 0}
        updates = []
        for r in rows:
            src = r["source_image_url"] or r["image_url"] or ""
            if not src or "/card_renders/" in src or "/card_suggestions/" in src:
                stats["skipped"] += 1; continue
            try:
                url = composite_card(src, overlay_rarity, int(r["id"]))
            except Exception as e:
                print(f"[bulk_rebake] err {r['id']}: {e}")
                stats["failed"] += 1; continue
            if not url:
                stats["failed"] += 1; continue
            final = (public_base + url) if public_base else url
            updates.append((final, int(r["id"])))
            stats["baked"] += 1

        if updates:
            conn = get_db(); c = conn.cursor()
            for final, rid in updates:
                c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                           (final, rid))
            conn.commit(); conn.close()
        return jsonify({"ok": True, "stats": stats})


    @app.route("/api/owner/cards/bulk-delete", methods=["POST"])
    def api_owner_cards_bulk_delete():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids vide"}), 400
        try:
            ids_int = [int(x) for x in ids][:5000]
        except (ValueError, TypeError):
            return jsonify({"error": "ids invalides"}), 400
        if not ids_int:
            return jsonify({"error": "ids vide"}), 400
        placeholders = ",".join("?" * len(ids_int))
        conn = get_db(); c = conn.cursor()
        c.execute(f"DELETE FROM user_cards WHERE card_id IN ({placeholders})", ids_int)
        uc_deleted = c.rowcount
        c.execute(f"DELETE FROM cards WHERE id IN ({placeholders})", ids_int)
        deleted = c.rowcount
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": deleted,
                         "user_cards_deleted": uc_deleted})


    @app.route("/api/owner/cards/rebalance-by-popularity", methods=["POST"])
    def api_owner_cards_rebalance():
        """Recalcule rarete par quantile de popularite.
        Body: {universes: [...], rebake: bool}
        - Anime : popularite = favoris Anilist (parse description)
        - Films/Série : popularite = ordre d'import (id ASC = top en premier)
        - Jeu Vidéo : NEVER touched (user curated manually)
        Repartition cibles : top 1% mythic, 4% leg, 15% epic, 30% rare, reste common.
        """
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        import re as _re, os as _os
        from database import get_db, CARD_RARITY_WEIGHTS
        from services.cards_overlay import composite_card
        data = request.json or {}
        universes = data.get("universes") or ["Anime", "Film/Série"]
        # Securite : exclure explicitement Jeu Vidéo
        universes = [u for u in universes if u != "Jeu Vidéo"]
        if not universes:
            return jsonify({"error": "aucun univers a rebalance"}), 400
        do_rebake = bool(data.get("rebake", True))

        conn = get_db(); c = conn.cursor()
        global_stats = {"universes": {}, "total_changed": 0, "total_rebaked": 0,
                          "total_rebake_failed": 0}

        for uni in universes:
            rows = c.execute(
                "SELECT id, rarity, description, source_image_url, image_url "
                "FROM cards WHERE universe = ?", (uni,)).fetchall()
            cards = [dict(r) for r in rows]
            if not cards:
                global_stats["universes"][uni] = {"total": 0, "changed": 0}
                continue

            # Score popularite
            def _score(r):
                desc = r.get("description") or ""
                m = _re.search(r"Favoris\s+(?:Anilist|MAL)\s*:\s*([\d,\s]+)", desc)
                if m:
                    s = m.group(1).replace(",", "").replace(" ", "").strip()
                    try: return int(s)
                    except ValueError: return 0
                # Films/Série fallback : id desc (recent = peu prio)
                # mais id asc = early imports = top games. On veut top = popular
                return -int(r["id"])  # plus petit id = plus populaire (early import)

            cards.sort(key=_score, reverse=True)
            n = len(cards)

            # Quantiles d'apres poids /roll : 1% mythic, 4% leg, 15% epic,
            # 30% rare, 50% common
            cuts = {
                "mythic":    int(n * 0.01),
                "legendary": int(n * 0.05),  # 1 + 4
                "epic":      int(n * 0.20),  # 5 + 15
                "rare":      int(n * 0.50),  # 20 + 30
            }
            uni_stats = {"total": n, "changed": 0, "by_rarity": {}}
            updates = []
            for idx, card in enumerate(cards):
                if idx < cuts["mythic"]:        new_rar = "mythic"
                elif idx < cuts["legendary"]:   new_rar = "legendary"
                elif idx < cuts["epic"]:        new_rar = "epic"
                elif idx < cuts["rare"]:        new_rar = "rare"
                else:                            new_rar = "common"
                uni_stats["by_rarity"][new_rar] = uni_stats["by_rarity"].get(new_rar, 0) + 1
                if card["rarity"] != new_rar:
                    updates.append((card, new_rar))

            uni_stats["changed"] = len(updates)
            print(f"[rebalance] {uni}: {n} cards, {len(updates)} change rarete")

            # Applique UPDATE
            for card, new_rar in updates:
                c.execute("UPDATE cards SET rarity = ? WHERE id = ?",
                          (new_rar, card["id"]))
            conn.commit()
            global_stats["universes"][uni] = uni_stats
            global_stats["total_changed"] += len(updates)

            # Rebake overlay parallelise (ThreadPool 10 workers)
            if do_rebake:
                from concurrent.futures import ThreadPoolExecutor
                import threading as _th
                public_base = (_os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
                to_bake = []
                for card, new_rar in updates:
                    src = card.get("source_image_url") or card.get("image_url") or ""
                    if not src or "/card_renders/" in src or "/card_suggestions/" in src:
                        continue
                    to_bake.append((card, new_rar))
                lock = _th.Lock(); counter = {"done": 0}
                results = []
                total = len(to_bake)
                def _w(item):
                    card, new_rar = item
                    try:
                        url = composite_card(card.get("source_image_url") or card.get("image_url"),
                                              new_rar, int(card["id"]))
                    except Exception as e:
                        print(f"[rebalance rebake] err {card['id']}: {e}")
                        url = None
                    with lock:
                        counter["done"] += 1
                        if counter["done"] % 200 == 0:
                            pct = counter["done"] * 100 // max(1, total)
                            print(f"[rebalance] {uni} rebake {counter['done']}/{total} ({pct}%)")
                    return (card["id"], url)
                with ThreadPoolExecutor(max_workers=10) as ex:
                    for cid, url in ex.map(_w, to_bake):
                        if url:
                            final = (public_base + url) if public_base else url
                            results.append((final, cid))
                            global_stats["total_rebaked"] += 1
                        else:
                            global_stats["total_rebake_failed"] += 1
                if results:
                    conn2 = get_db(); c2 = conn2.cursor()
                    for final, cid in results:
                        c2.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                                    (final, cid))
                    conn2.commit(); conn2.close()
                print(f"[rebalance] {uni}: rebaked {global_stats['total_rebaked']}")

        conn.close()
        return jsonify({"ok": True, "stats": global_stats})


    @app.route("/api/owner/cards/wipe", methods=["POST"])
    def api_owner_cards_wipe():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        data = request.json or {}
        universe = (data.get("universe") or "").strip() or None
        conn = get_db(); c = conn.cursor()
        if universe:
            # Wipe scoped : recupere ids cards, delete user_cards correspondants
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM cards WHERE universe = ?", (universe,)).fetchall()]
            if ids:
                placeholders = ",".join("?" * len(ids))
                c.execute(f"DELETE FROM user_cards WHERE card_id IN ({placeholders})", ids)
                c.execute(f"DELETE FROM cards WHERE id IN ({placeholders})", ids)
            deleted = len(ids)
        else:
            c.execute("DELETE FROM cards")
            deleted = c.rowcount
            c.execute("DELETE FROM user_cards")
        conn.commit(); conn.close()
        return jsonify({"ok": True, "deleted": deleted, "universe": universe})


    @app.route("/api/owner/cards/universes", methods=["GET"])
    def api_owner_cards_universes():
        if not _is_owner_session():
            return jsonify({"error": "owner only"}), 403
        from database import get_db
        conn = get_db(); c = conn.cursor()
        rows = c.execute(
            "SELECT universe, COUNT(*) AS n FROM cards "
            "WHERE universe IS NOT NULL AND universe != '' "
            "GROUP BY universe ORDER BY n DESC").fetchall()
        conn.close()
        return jsonify({"items": [{"universe": r["universe"], "count": r["n"]} for r in rows]})
