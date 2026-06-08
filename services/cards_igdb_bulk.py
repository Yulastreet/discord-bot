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


def _fetch_top_game_ids(target_count: int = 1000,
                          sleep_between: float = 0.4) -> list[int]:
    """Iter /games top par total_rating_count desc, retourne game IDs."""
    game_ids: list[int] = []
    offset = 0
    per_page = 500
    while len(game_ids) < target_count and offset < 9500:
        body = (f"fields id, name, total_rating_count; "
                f"where total_rating_count > 5; "
                f"sort total_rating_count desc; "
                f"limit {per_page}; offset {offset};")
        rows = _igdb_query("games", body)
        if not rows:
            print(f"[igdb_bulk] games page offset={offset} empty")
            break
        for g in rows:
            gid = g.get("id")
            if gid:
                game_ids.append(gid)
        print(f"[igdb_bulk] games offset={offset}: total {len(game_ids)} game_ids")
        offset += per_page
        time.sleep(sleep_between)
    return game_ids[:target_count]


def _fetch_chars_in_games(game_ids: list[int], exclude_char_ids: set,
                            target_count: int, sleep_between: float = 0.4) -> list[dict]:
    """Query /characters where games contient un des game_ids fournis.
    IGDB syntax 'games = (id1, id2, ...)' = any-of pour array field."""
    if not game_ids:
        return []
    all_chars = []
    seen_char_ids = set(exclude_char_ids)
    # Batch game_ids par groupe pour eviter query body trop long
    game_batch = 200
    char_offset = 0
    char_per_page = 500
    for gi in range(0, len(game_ids), game_batch):
        gbatch = game_ids[gi:gi + game_batch]
        ids_str = ",".join(str(x) for x in gbatch)
        # Pagine sur les chars matching ce subset
        offset = 0
        while True:
            body = (f"fields id, name, mug_shot.image_id, games.name, gender; "
                    f"where games = ({ids_str}) & mug_shot != null & name != null; "
                    f"sort id asc; limit {char_per_page}; offset {offset};")
            rows = _igdb_query("characters", body)
            if not rows:
                break
            new_in_batch = 0
            for ch in rows:
                cid = ch.get("id")
                if cid and cid not in seen_char_ids:
                    seen_char_ids.add(cid)
                    all_chars.append(ch)
                    new_in_batch += 1
                    if len(all_chars) >= target_count:
                        return all_chars
            print(f"[igdb_bulk] chars-in-games gbatch {gi} offset={offset}: "
                  f"+{new_in_batch} (total {len(all_chars)})")
            if len(rows) < char_per_page:
                break  # plus rien dans ce subset
            offset += char_per_page
            time.sleep(sleep_between)
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

    # === Phase 2 : top games -> chars in those games ===
    remaining = max(0, target_total - stats["inserted"])
    if remaining <= 0:
        return stats
    print(f"[igdb_bulk] phase2 : fetch top games + leurs chars pour {remaining} supp")
    # 1000 top games suffit pour acceder a ~milliers chars iconiques
    top_games = _fetch_top_game_ids(target_count=1000,
                                       sleep_between=sleep_between)
    if not top_games:
        print(f"[igdb_bulk] phase2 abort : pas de top games recuperes")
        return stats
    print(f"[igdb_bulk] phase2 : {len(top_games)} top games recup, query chars...")
    new_chars = _fetch_chars_in_games(top_games, exclude_char_ids=seen_char_ids,
                                         target_count=remaining + 200,
                                         sleep_between=sleep_between)
    print(f"[igdb_bulk] phase2 : {len(new_chars)} chars frais avec mug_shot")
    for ch in new_chars:
        rank_counter += 1
        stats["total_seen"] += 1
        _process_char_row(ch, rank_counter, existing, skip_existing, stats)
        if stats["inserted"] >= target_total:
            break

    print(f"[igdb_bulk] final stats={stats}")
    return stats
