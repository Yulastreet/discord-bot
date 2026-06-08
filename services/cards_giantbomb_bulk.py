"""Bulk import personnages jeux video via Giant Bomb API.

Catalogue : ~60k+ chars avec images. Rate limit : 200 req/heure/resource.
Auth : api_key dans query string + User-Agent obligatoire (refuse default).

Endpoint : https://www.giantbomb.com/api/characters/
Format : ?format=json&api_key=XXX&offset=N&limit=100&field_list=...

Pagination : limit max 100/page. 600 pages = 60000 chars.
Sleep 18s/page pour respecter 200 req/heure (3.6 req/min). Long mais
seul moyen sans ban. Phases : import par batch de 50-100 pages = ~5k chars
en ~15-30min.

Image URL : `image.medium_url` ou `image.super_url` (Giant Bomb CDN).
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_BASE = "https://www.giantbomb.com/api"
# Rate limit Giant Bomb : 200/heure/resource = ~3.6/min. Sleep 17s safe.
_DEFAULT_SLEEP = 17.0


def _gb_get(endpoint: str, params: dict, timeout: int = 20) -> dict | None:
    """GET Giant Bomb avec api_key auto-injected."""
    key = os.getenv("GIANTBOMB_API_KEY", "").strip()
    if not key:
        print("[giantbomb] missing GIANTBOMB_API_KEY env")
        return None
    params = dict(params)
    params["api_key"] = key
    params["format"] = "json"
    qs = urllib.parse.urlencode(params)
    url = f"{_BASE}/{endpoint}/?{qs}"
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept":     "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
        print(f"[giantbomb] HTTPError {endpoint} {e.code}: {body}")
        return None
    except Exception as e:
        print(f"[giantbomb] err {endpoint}: {e}")
        return None


def _rarity_weighted(rank: int) -> str:
    if rank <= 30:    return "mythic"
    if rank <= 120:   return "legendary"
    if rank <= 350:   return "epic"
    if rank <= 800:   return "rare"
    return "common"


def _process_gb_char(ch: dict, rank: int, existing: set,
                      skip_existing: bool, stats: dict) -> bool:
    from database import card_add
    name = (ch.get("name") or "").strip()
    if not name:
        stats["failed"] += 1; return False
    if skip_existing and name.lower() in existing:
        stats["skipped"] += 1; return False
    image = ch.get("image") or {}
    img_url = (image.get("medium_url") or image.get("super_url")
                or image.get("original_url") or image.get("small_url"))
    if not img_url:
        stats["failed"] += 1; return False
    fag = ch.get("first_appeared_in_game") or {}
    subtitle = None
    if isinstance(fag, dict):
        subtitle = (fag.get("name") or "")[:80] or None
    deck = (ch.get("deck") or "").strip()[:300]
    desc = deck or "Personnage de jeu video."
    rarity = _rarity_weighted(rank)
    try:
        card_add(name=name, universe="Jeu Vidéo", subtitle=subtitle,
                  rarity=rarity, image_url=img_url, description=desc)
        existing.add(name.lower())
        stats["inserted"] += 1
        return True
    except Exception as e:
        print(f"[giantbomb] insert err {name}: {e}")
        stats["failed"] += 1
        return False


def bulk_import_giantbomb(pages: int = 50, page_size: int = 100,
                             sleep_between: float = _DEFAULT_SLEEP,
                             skip_existing: bool = True,
                             wipe_first: bool = False,
                             start_offset: int = 0) -> dict:
    """Pagine Giant Bomb /characters. Default 50 pages * 100 = 5000 chars
    en ~15min (sleep 17s).
    pages max conseille 200 (200/heure rate limit).
    start_offset pour reprendre une session interrompue."""
    from database import get_db
    if not os.getenv("GIANTBOMB_API_KEY", "").strip():
        return {"error": "GIANTBOMB_API_KEY non set dans .env"}

    conn = get_db(); c = conn.cursor()
    if wipe_first:
        c.execute("DELETE FROM user_cards")
        c.execute("DELETE FROM cards")
        conn.commit()
        print("[giantbomb] wiped cards + user_cards")
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0}
    rank = 0
    per_page = max(1, min(int(page_size), 100))

    fields = "name,image,deck,first_appeared_in_game"
    for page in range(pages):
        offset = start_offset + page * per_page
        params = {
            "limit":      per_page,
            "offset":     offset,
            "field_list": fields,
            "sort":       "number_of_user_reviews:desc",
        }
        data = _gb_get("characters", params)
        if not data:
            print(f"[giantbomb] page {page+1} offset={offset} request failed, sleep & retry next")
            time.sleep(sleep_between)
            continue
        results = data.get("results") or []
        if not results:
            print(f"[giantbomb] page {page+1} offset={offset} empty (end of catalog ?)")
            break
        for ch in results:
            rank += 1
            stats["total_seen"] += 1
            _process_gb_char(ch, rank, existing, skip_existing, stats)
        print(f"[giantbomb] page {page+1}/{pages} offset={offset} done. stats={stats}")
        if page < pages - 1:
            time.sleep(sleep_between)

    print(f"[giantbomb] final stats={stats}")
    return stats
