"""Bulk import personnages Marvel via Marvel Developer API.

Auth : ts + apikey (public) + hash MD5(ts + private + public).
Rate limit 3000 calls/jour. Pagination 100/page max.

Endpoint : https://gateway.marvel.com/v1/public/characters

Total chars dispos : ~1500.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_BASE = "https://gateway.marvel.com/v1/public"


def _auth_params() -> dict | None:
    pub = (os.getenv("MARVEL_PUBLIC_KEY") or "").strip()
    priv = (os.getenv("MARVEL_PRIVATE_KEY") or "").strip()
    if not pub or not priv:
        print("[marvel] missing MARVEL_PUBLIC_KEY / MARVEL_PRIVATE_KEY env vars")
        return None
    ts = str(int(time.time()))
    h = hashlib.md5(f"{ts}{priv}{pub}".encode("utf-8")).hexdigest()
    return {"ts": ts, "apikey": pub, "hash": h}


def _marvel_get(endpoint: str, params: dict, timeout: int = 15) -> dict | None:
    auth = _auth_params()
    if not auth:
        return None
    full = {**params, **auth}
    qs = urllib.parse.urlencode(full)
    url = f"{_BASE}/{endpoint}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        print(f"[marvel] HTTPError {endpoint} {e.code}: {body}")
        return None
    except Exception as e:
        print(f"[marvel] err {endpoint}: {e}")
        return None


def _rarity_from_rank(rank: int) -> str:
    if rank <= 5:    return "mythic"
    if rank <= 25:   return "legendary"
    if rank <= 100:  return "epic"
    if rank <= 300:  return "rare"
    return "common"


def bulk_import_marvel(pages: int = 15, page_size: int = 100,
                         sleep_between: float = 1.0,
                         skip_existing: bool = True) -> dict:
    """Fetch top N Marvel characters. Default 15 x 100 = 1500 (max available)."""
    from database import get_db, card_add
    if not _auth_params():
        return {"error": "MARVEL_PUBLIC_KEY/MARVEL_PRIVATE_KEY non set"}

    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "no_image": 0, "total_seen": 0}
    rank = 0
    per_page = max(1, min(int(page_size), 100))

    for page in range(pages):
        offset = page * per_page
        # Sort by modified desc = active chars en premier
        data = _marvel_get("characters", {
            "limit": per_page, "offset": offset,
            "orderBy": "-modified",
        })
        if not data:
            print(f"[marvel] page {page+1} HTTP fail")
            time.sleep(sleep_between)
            continue
        results = ((data.get("data") or {}).get("results") or [])
        if not results:
            print(f"[marvel] page {page+1} empty (fin catalogue)")
            break

        for ch in results:
            rank += 1
            stats["total_seen"] += 1
            try:
                name = (ch.get("name") or "").strip()
                if not name:
                    stats["failed"] += 1; continue
                if skip_existing and name.lower() in existing:
                    stats["skipped"] += 1; continue

                thumb = ch.get("thumbnail") or {}
                tpath = thumb.get("path") or ""
                text = thumb.get("extension") or "jpg"
                # Marvel placeholder 'image_not_available' = pas d'image
                if "image_not_available" in tpath:
                    stats["no_image"] += 1; continue
                # URL portrait : standard_xlarge donne 200x300, format portrait OK
                img_url = f"{tpath}/portrait_uncanny.{text}"  # 300x450 portrait
                # Switch en HTTPS
                if img_url.startswith("http://"):
                    img_url = "https://" + img_url[7:]

                desc = (ch.get("description") or "").strip()[:300] or "Personnage Marvel."
                rarity = _rarity_from_rank(rank)
                # Subtitle : premier comic ou serie associee si dispo
                series_items = ((ch.get("series") or {}).get("items") or [])
                subtitle = None
                if series_items:
                    subtitle = (series_items[0].get("name") or "")[:80] or None
                if not subtitle:
                    subtitle = "Marvel"

                card_add(name=name, universe="Film/Série", subtitle=subtitle,
                          rarity=rarity, image_url=img_url, description=desc)
                existing.add(name.lower())
                stats["inserted"] += 1
            except Exception as e:
                print(f"[marvel] insert err {ch.get('name')}: {e}")
                stats["failed"] += 1

        print(f"[marvel] page {page+1}/{pages}: stats={stats}")
        time.sleep(sleep_between)

    print(f"[marvel] FINAL: {stats}")
    return stats
