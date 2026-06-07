"""Bulk import via Anilist GraphQL.

Avantages vs Jikan :
- Single query retourne char + image + media title (subtitle garanti)
- Images Anilist uniformes (cdn anime-pictures style portrait)
- Rate limit confortable (90 req/min)
- 50 chars/page (vs 25 Jikan), 20 pages = 1000 cartes

Endpoint : POST https://graphql.anilist.co
"""
from __future__ import annotations

import json
import time
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_ENDPOINT = "https://graphql.anilist.co"

_QUERY = """
query ($page: Int) {
  Page(page: $page, perPage: 50) {
    characters(sort: FAVOURITES_DESC) {
      id
      name { full native }
      image { large }
      favourites
      description(asHtml: false)
      media(perPage: 1, sort: POPULARITY_DESC) {
        nodes { title { romaji english } }
      }
    }
  }
}
"""


def _rarity_from_rank(rank: int) -> str:
    if rank <= 10:   return "mythic"
    if rank <= 50:   return "legendary"
    if rank <= 200:  return "epic"
    if rank <= 500:  return "rare"
    return "common"


def _gql(page: int, timeout: int = 15) -> dict | None:
    body = json.dumps({"query": _QUERY, "variables": {"page": page}}).encode("utf-8")
    req = urllib.request.Request(
        _ENDPOINT, data=body, method="POST",
        headers={"User-Agent": _USER_AGENT,
                  "Content-Type": "application/json",
                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[anilist_bulk] HTTP error page {page}: {e}")
        return None


def _clean_description(raw: str | None) -> str:
    if not raw:
        return ""
    # Anilist description peut contenir BBCode (__bold__, ~!spoiler!~)
    # Strip tags simple
    txt = raw.replace("__", "").replace("~!", "").replace("!~", "")
    txt = txt.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    # Premiere phrase ou 250 chars
    txt = txt.strip().split("\n")[0][:250]
    return txt


def bulk_import_anilist(pages: int = 20, sleep_between: float = 0.8,
                         skip_existing: bool = True,
                         wipe_first: bool = False) -> dict:
    """Recupere top N personnages, insere dans cards.

    pages * 50 = total cartes (default 20 pages = 1000).
    wipe_first : DELETE FROM cards avant import (clean slate).
    Retourne stats.
    """
    from database import get_db, card_add

    conn = get_db(); c = conn.cursor()
    if wipe_first:
        c.execute("DELETE FROM user_cards")
        c.execute("DELETE FROM cards")
        conn.commit()
        print("[anilist_bulk] wiped cards + user_cards")

    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0}
    rank_counter = 0

    for page in range(1, pages + 1):
        data = _gql(page)
        chars = (((data or {}).get("data") or {}).get("Page") or {}).get("characters") or []
        if not chars:
            print(f"[anilist_bulk] page {page} empty")
            time.sleep(sleep_between)
            continue

        for ch in chars:
            rank_counter += 1
            stats["total_seen"] += 1
            try:
                name = ((ch.get("name") or {}).get("full") or "").strip()
                if not name:
                    stats["failed"] += 1; continue
                if skip_existing and name.lower() in existing:
                    stats["skipped"] += 1; continue

                img_url = ((ch.get("image") or {}).get("large") or "").strip()
                if not img_url:
                    stats["failed"] += 1; continue

                media_nodes = ((ch.get("media") or {}).get("nodes") or [])
                subtitle = None
                if media_nodes:
                    t = (media_nodes[0].get("title") or {})
                    subtitle = (t.get("english") or t.get("romaji") or "")[:80] or None

                rarity = _rarity_from_rank(rank_counter)
                fav = ch.get("favourites") or 0
                desc_excerpt = _clean_description(ch.get("description"))
                desc_parts = [f"Favoris Anilist : {fav:,}"]
                if desc_excerpt:
                    desc_parts.append(desc_excerpt)
                desc = " · ".join(desc_parts)[:1000]  # cap embed safe

                card_add(name=name, universe="Anime", subtitle=subtitle,
                          rarity=rarity, image_url=img_url, description=desc)
                existing.add(name.lower())
                stats["inserted"] += 1
            except Exception as e:
                print(f"[anilist_bulk] insert error {ch.get('name')}: {e}")
                stats["failed"] += 1

        print(f"[anilist_bulk] page {page}/{pages} done. stats={stats}")
        time.sleep(sleep_between)

    return stats
