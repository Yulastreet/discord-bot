"""Bulk import de cartes via Jikan API (MyAnimeList non-officielle).

Approche Mudae-like : top N personnages anime par favorites. Images
Jikan toutes au format portrait ~225x350 (MAL CDN standard) donc
affichage uniforme garanti.

Rate limit Jikan : 3 req/sec, 60 req/min. On sleep 1.2s entre pages.

Mapping rarity selon rank MAL favorites :
- rank 1-10    : mythic
- rank 11-50   : legendary
- rank 51-200  : epic
- rank 201-500 : rare
- rank 501+    : common
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_BASE = "https://api.jikan.moe/v4"


def _rarity_from_rank(rank: int) -> str:
    if rank <= 10:   return "mythic"
    if rank <= 50:   return "legendary"
    if rank <= 200:  return "epic"
    if rank <= 500:  return "rare"
    return "common"


def _http_get(url: str, timeout: int = 12) -> dict | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[jikan_bulk] HTTP error {url}: {e}")
        return None


def bulk_import_jikan(pages: int = 40, sleep_between: float = 1.2,
                      skip_existing: bool = True) -> dict:
    """Recupere top N personnages anime, insere dans cards.

    pages * 25 = total cartes (default 40 pages = 1000).
    skip_existing : skip si nom deja en DB (no duplicates).
    Retourne stats {inserted, skipped, failed, total_seen}.
    """
    from database import get_db, card_add

    # Pre-fetch existing names pour dedup rapide
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0}
    rank_counter = 0

    for page in range(1, pages + 1):
        url = f"{_BASE}/top/characters?page={page}"
        data = _http_get(url)
        if not data or not data.get("data"):
            print(f"[jikan_bulk] page {page} empty/failed")
            time.sleep(sleep_between)
            continue

        for ch in data["data"]:
            rank_counter += 1
            stats["total_seen"] += 1
            try:
                name = (ch.get("name") or "").strip()
                if not name:
                    stats["failed"] += 1; continue
                if skip_existing and name.lower() in existing:
                    stats["skipped"] += 1; continue

                img_url = ((ch.get("images") or {}).get("jpg") or {}).get("image_url")
                if not img_url:
                    stats["failed"] += 1; continue

                # Anime origin : premier anime de la liste si dispo
                anime_list = ch.get("anime") or []
                subtitle = None
                if anime_list:
                    subtitle = ((anime_list[0].get("anime") or {}).get("title")
                                 or "")[:80] or None

                rarity = _rarity_from_rank(rank_counter)

                # Description : favorites count + about excerpt si dispo
                fav = ch.get("favorites", 0)
                about = (ch.get("about") or "").strip()
                desc_parts = [f"Favoris MAL : {fav:,}"]
                if about:
                    excerpt = about.split("\n")[0][:200]
                    if excerpt:
                        desc_parts.append(excerpt)
                desc = " · ".join(desc_parts)

                card_add(name=name, universe="Anime", subtitle=subtitle,
                          rarity=rarity, image_url=img_url, description=desc)
                existing.add(name.lower())
                stats["inserted"] += 1
            except Exception as e:
                print(f"[jikan_bulk] insert error {ch.get('name')}: {e}")
                stats["failed"] += 1

        print(f"[jikan_bulk] page {page}/{pages} done. running stats={stats}")
        time.sleep(sleep_between)

    return stats
