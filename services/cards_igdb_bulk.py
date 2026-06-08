"""Bulk import personnages jeux video via IGDB API (Twitch).

IGDB /characters endpoint retourne nom + mug_shot (portrait) + games associated.
Volume disponible : ~30000+ chars avec mug_shot.

Auth flow :
- POST https://id.twitch.tv/oauth2/token (client_credentials grant)
- Headers IGDB : Client-ID + Authorization: Bearer <token>
- Body request : raw text (IGDB query syntax, pas JSON)

Rate limit IGDB : 4 req/sec. Sleep 0.3s entre pages = safe.

Token cache : valide ~60j, refetch si expire.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_IGDB_BASE = "https://api.igdb.com/v4"

# Cache token en memoire (process lifetime)
_token_cache = {"token": None, "expires_at": 0}


def _get_access_token() -> str | None:
    """Recupere bearer token via client_credentials, cache jusqu'a expiry."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    # Prefere TWITCH_CLIENT_ID/SECRET (deja set sur prod pour stream alerts),
    # fallback IGDB_CLIENT_ID/SECRET (alias). Memes credentials Twitch dev.
    cid = (os.getenv("TWITCH_CLIENT_ID") or os.getenv("IGDB_CLIENT_ID") or "").strip()
    secret = (os.getenv("TWITCH_CLIENT_SECRET") or os.getenv("IGDB_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        print("[igdb] missing TWITCH_CLIENT_ID/SECRET (or IGDB_*) env vars")
        return None

    params = urllib.parse.urlencode({
        "client_id":     cid,
        "client_secret": secret,
        "grant_type":    "client_credentials",
    })
    url = f"{_TWITCH_TOKEN_URL}?{params}"
    req = urllib.request.Request(url, method="POST",
                                   headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 0))
        if not token:
            print(f"[igdb] token response missing access_token: {data}")
            return None
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + expires_in
        return token
    except Exception as e:
        print(f"[igdb] token fetch err: {e}")
        return None


def _igdb_query(endpoint: str, body: str, timeout: int = 15) -> list | None:
    """POST IGDB endpoint avec body raw text query. Retourne list ou None."""
    token = _get_access_token()
    if not token:
        return None
    cid = (os.getenv("TWITCH_CLIENT_ID") or os.getenv("IGDB_CLIENT_ID") or "").strip()
    url = f"{_IGDB_BASE}/{endpoint}"
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                   headers={
        "User-Agent":    _USER_AGENT,
        "Client-ID":     cid,
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
        "Content-Type":  "text/plain",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[igdb] query err {endpoint}: {e}")
        return None


def _build_image_url(image_id: str) -> str:
    """Build URL portrait IGDB. Format t_cover_big = 264x374 (ratio 2:3).
    Plus grand : t_cover_huge = 1080x1528, t_1080p = 1920x1080."""
    return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg"


def _rarity_weighted(rank: int) -> str:
    """Distribution rarete deterministe selon rank d'insertion :
    rank 1-20    : mythic    (~2%)
    rank 21-80   : legendary (~6%)
    rank 81-200  : epic      (~12%)
    rank 201-450 : rare      (~25%)
    rank 451+    : common    (~55%)
    Ratio total proche d'un tirage Mudae."""
    if rank <= 20:   return "mythic"
    if rank <= 80:   return "legendary"
    if rank <= 200:  return "epic"
    if rank <= 450:  return "rare"
    return "common"


def bulk_import_igdb(pages: int = 4, page_size: int = 500,
                       sleep_between: float = 0.4,
                       skip_existing: bool = True,
                       wipe_first: bool = False) -> dict:
    """Recupere top N chars IGDB avec mug_shot, insere dans cards.

    pages * page_size = total max (default 4*500 = 2000 cartes).
    IGDB hard cap : 500/page.
    """
    from database import get_db, card_add

    conn = get_db(); c = conn.cursor()
    if wipe_first:
        c.execute("DELETE FROM user_cards")
        c.execute("DELETE FROM cards")
        conn.commit()
        print("[igdb_bulk] wiped cards + user_cards")

    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0}
    rank_counter = 0

    # Token check pre-flight
    if not _get_access_token():
        return {"error": "IGDB token fetch failed - check IGDB_CLIENT_ID/IGDB_CLIENT_SECRET env"}

    per_page = max(1, min(int(page_size), 500))
    for page in range(pages):
        offset = page * per_page
        # IGDB query : limite aux chars avec mug_shot, exclude vide
        body = (f"fields name, mug_shot.image_id, games.name, gender; "
                f"where mug_shot != null & name != null; "
                f"sort id asc; limit {per_page}; offset {offset};")
        rows = _igdb_query("characters", body)
        if not rows:
            print(f"[igdb_bulk] page {page+1} empty/failed")
            time.sleep(sleep_between)
            continue

        for ch in rows:
            rank_counter += 1
            stats["total_seen"] += 1
            try:
                name = (ch.get("name") or "").strip()
                if not name:
                    stats["failed"] += 1; continue
                if skip_existing and name.lower() in existing:
                    stats["skipped"] += 1; continue

                mug = ch.get("mug_shot") or {}
                img_id = mug.get("image_id")
                if not img_id:
                    stats["failed"] += 1; continue

                img_url = _build_image_url(img_id)

                # Subtitle = premier jeu associe
                games = ch.get("games") or []
                subtitle = None
                if games:
                    g = games[0]
                    if isinstance(g, dict):
                        subtitle = (g.get("name") or "")[:80] or None

                rarity = _rarity_weighted(rank_counter)
                desc = "Personnage de jeu video."
                if games:
                    n_games = len(games)
                    desc = f"Apparait dans {n_games} jeu{'s' if n_games > 1 else ''} (IGDB)."

                card_add(name=name, universe="Jeu Vidéo", subtitle=subtitle,
                          rarity=rarity, image_url=img_url, description=desc)
                existing.add(name.lower())
                stats["inserted"] += 1
            except Exception as e:
                print(f"[igdb_bulk] insert err {ch.get('name')}: {e}")
                stats["failed"] += 1

        print(f"[igdb_bulk] page {page+1}/{pages} done. stats={stats}")
        time.sleep(sleep_between)

    return stats
