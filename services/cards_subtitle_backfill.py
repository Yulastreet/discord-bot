"""Backfill subtitle (anime d'origine) pour cards Anime sans subtitle.

Query Anilist par nom, recupere premier media non-NSFW associe.
Rate limit Anilist : sleep 2.5s entre requetes.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_ENDPOINT = "https://graphql.anilist.co"

_QUERY = """
query ($name: String) {
  Character(search: $name) {
    name { full }
    media(perPage: 5, sort: POPULARITY_DESC) {
      nodes { title { romaji english } isAdult }
    }
  }
}
"""


def _gql(name: str, timeout: int = 12, max_retries: int = 3) -> dict | None:
    body = json.dumps({"query": _QUERY, "variables": {"name": name}}).encode("utf-8")
    for attempt in range(max_retries):
        req = urllib.request.Request(
            _ENDPOINT, data=body, method="POST",
            headers={"User-Agent": _USER_AGENT,
                      "Content-Type": "application/json",
                      "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else (60 * (attempt + 1))
                print(f"[subtitle_backfill] 429 '{name}', sleep {wait}s")
                time.sleep(wait)
                continue
            if e.code == 404:
                return None
            print(f"[subtitle_backfill] HTTP {e.code} '{name}'")
            return None
        except Exception as e:
            print(f"[subtitle_backfill] err '{name}': {e}")
            return None
    return None


def backfill_subtitles(universe: str = "Anime", limit: int = None,
                          sleep_between: float = 2.5) -> dict:
    """Iter cards de cet univers sans subtitle, query Anilist, update."""
    from database import get_db
    conn = get_db(); c = conn.cursor()
    where = "universe = ? AND (subtitle IS NULL OR subtitle = '')"
    if limit:
        rows = c.execute(
            f"SELECT id, name FROM cards WHERE {where} LIMIT ?",
            (universe, int(limit))).fetchall()
    else:
        rows = c.execute(
            f"SELECT id, name FROM cards WHERE {where}",
            (universe,)).fetchall()
    cards = [dict(r) for r in rows]
    conn.close()

    stats = {"total": len(cards), "updated": 0, "skipped": 0, "failed": 0}
    print(f"[subtitle_backfill] {len(cards)} cards a backfill (univers={universe})")

    for i, card in enumerate(cards):
        if i and i % 20 == 0:
            print(f"[subtitle_backfill] progress {i}/{len(cards)} stats={stats}")
        data = _gql(card["name"])
        if not data:
            stats["failed"] += 1
            time.sleep(sleep_between); continue
        ch = ((data.get("data") or {}).get("Character") or {})
        if not ch:
            stats["skipped"] += 1
            time.sleep(sleep_between); continue
        media = ((ch.get("media") or {}).get("nodes") or [])
        subtitle = None
        for m in media:
            if m.get("isAdult"):
                continue
            t = (m.get("title") or {})
            subtitle = (t.get("english") or t.get("romaji") or "")[:80] or None
            if subtitle:
                break
        if subtitle:
            conn = get_db(); c = conn.cursor()
            c.execute("UPDATE cards SET subtitle = ? WHERE id = ?",
                      (subtitle, card["id"]))
            conn.commit(); conn.close()
            stats["updated"] += 1
        else:
            stats["skipped"] += 1
        time.sleep(sleep_between)

    print(f"[subtitle_backfill] FINAL : {stats}")
    return stats
