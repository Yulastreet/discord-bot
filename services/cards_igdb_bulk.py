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
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = ""
        print(f"[igdb] HTTPError {endpoint} {e.code}\n  body: {body!r}\n  resp: {err_body}")
        return None
    except Exception as e:
        print(f"[igdb] query err {endpoint}: {e}\n  body: {body!r}")
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


def _process_char_row(ch: dict, rank_counter: int, existing: set,
                        skip_existing: bool, stats: dict) -> bool:
    """Insert one char row. Return True si inserted."""
    from database import card_add
    name = (ch.get("name") or "").strip()
    if not name:
        stats["failed"] += 1; return False
    if skip_existing and name.lower() in existing:
        stats["skipped"] += 1; return False
    mug = ch.get("mug_shot") or {}
    img_id = mug.get("image_id")
    if not img_id:
        stats["failed"] += 1; return False
    img_url = _build_image_url(img_id)
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
    try:
        card_add(name=name, universe="Jeu Vidéo", subtitle=subtitle,
                  rarity=rarity, image_url=img_url, description=desc)
        existing.add(name.lower())
        stats["inserted"] += 1
        return True
    except Exception as e:
        print(f"[igdb_bulk] insert err {name}: {e}")
        stats["failed"] += 1
        return False


def _fetch_top_games_character_ids(target_count: int = 2000,
                                     sleep_between: float = 0.4) -> list[int]:
    """Iter /games top par rating_count desc, extract characters arrays,
    retourne liste unique des character_ids jusqu'a target_count."""
    char_ids: list[int] = []
    seen = set()
    offset = 0
    per_page = 500
    while len(char_ids) < target_count and offset < 9500:
        # IGDB requirement : champ utilise par sort doit etre dans fields.
        # rating_count > 5 = jeux serieux. total_rating_count = sum users+critics.
        body = (f"fields characters, name, total_rating_count; "
                f"where total_rating_count > 5; "
                f"sort total_rating_count desc; "
                f"limit {per_page}; offset {offset};")
        rows = _igdb_query("games", body)
        if not rows:
            print(f"[igdb_bulk] games page offset={offset} empty")
            break
        for g in rows:
            for cid in (g.get("characters") or []):
                if cid not in seen:
                    seen.add(cid)
                    char_ids.append(cid)
                    if len(char_ids) >= target_count:
                        break
            if len(char_ids) >= target_count:
                break
        print(f"[igdb_bulk] games offset={offset}: collected {len(char_ids)} char_ids")
        offset += per_page
        time.sleep(sleep_between)
    return char_ids


def _fetch_chars_batch(char_ids: list[int], sleep_between: float = 0.4) -> list[dict]:
    """Batch fetch chars by ids. IGDB max 500 per call."""
    if not char_ids:
        return []
    all_chars = []
    batch_size = 500
    for i in range(0, len(char_ids), batch_size):
        batch = char_ids[i:i + batch_size]
        ids_str = ",".join(str(x) for x in batch)
        body = (f"fields name, mug_shot.image_id, games.name, gender; "
                f"where id = ({ids_str}) & mug_shot != null; "
                f"limit {batch_size};")
        rows = _igdb_query("characters", body)
        if rows:
            all_chars.extend(rows)
            print(f"[igdb_bulk] batch {i}-{i+len(batch)}: {len(rows)} chars avec mug_shot")
        time.sleep(sleep_between)
    return all_chars


def bulk_import_igdb(pages: int = 4, page_size: int = 500,
                       sleep_between: float = 0.4,
                       skip_existing: bool = True,
                       wipe_first: bool = False) -> dict:
    """Strategie 2 phases :
    1. /characters direct paginated (cap ~400-500 avec mug_shot non null)
    2. Si demande > 500 : fetch top games par rating_count, extract leurs
       character_ids, batch fetch ces chars (chars iconiques dans jeux pop)

    Total cible = pages * page_size.
    """
    from database import get_db

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

    if not _get_access_token():
        return {"error": "IGDB token fetch failed - check TWITCH_CLIENT_ID/SECRET env"}

    target_total = pages * page_size
    per_page = max(1, min(int(page_size), 500))

    # === Phase 1 : /characters direct ===
    seen_char_ids: set[int] = set()
    for page in range(pages):
        offset = page * per_page
        body = (f"fields id, name, mug_shot.image_id, games.name, gender; "
                f"where mug_shot != null & name != null; "
                f"sort id asc; limit {per_page}; offset {offset};")
        rows = _igdb_query("characters", body)
        if not rows:
            print(f"[igdb_bulk] phase1 page {page+1} empty - bascule sur phase 2")
            break
        for ch in rows:
            rank_counter += 1
            stats["total_seen"] += 1
            cid = ch.get("id")
            if cid: seen_char_ids.add(cid)
            _process_char_row(ch, rank_counter, existing, skip_existing, stats)
        print(f"[igdb_bulk] phase1 page {page+1}: stats={stats}")
        time.sleep(sleep_between)
        if stats["inserted"] >= target_total:
            return stats

    # === Phase 2 : top games -> char_ids -> batch fetch ===
    remaining = max(0, target_total - stats["inserted"])
    if remaining <= 0:
        return stats
    print(f"[igdb_bulk] phase2 : fetch top games pour {remaining} chars supp")
    extra_target = remaining + 200  # marge pour skip/fail
    game_char_ids = _fetch_top_games_character_ids(target_count=extra_target,
                                                      sleep_between=sleep_between)
    # Exclude ids deja vus phase 1
    fresh_ids = [cid for cid in game_char_ids if cid not in seen_char_ids]
    print(f"[igdb_bulk] phase2 : {len(fresh_ids)} char_ids frais a fetch")
    new_chars = _fetch_chars_batch(fresh_ids, sleep_between=sleep_between)
    for ch in new_chars:
        rank_counter += 1
        stats["total_seen"] += 1
        _process_char_row(ch, rank_counter, existing, skip_existing, stats)
        if stats["inserted"] >= target_total:
            break

    print(f"[igdb_bulk] final stats={stats}")
    return stats
