"""Bulk import villagers Animal Crossing via Nookipedia API.

Source : https://nookipedia.com/
Token gratuit requis (signup nookipedia.com/wiki/Special:UserLogin).
~391 villagers New Horizons + autres jeux.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_BASE = "https://api.nookipedia.com"


def _http_get(url: str, timeout: int = 20) -> dict | list | None:
    api_key = os.getenv("NOOKIPEDIA_API_KEY", "").strip()
    if not api_key:
        print("[nookipedia] missing NOOKIPEDIA_API_KEY env")
        return None
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept-Version": "1.0.0",
        "X-API-KEY": api_key,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            body = ""
        print(f"[nookipedia] HTTP {e.code} {url[:80]}: {body}")
        return None
    except Exception as e:
        print(f"[nookipedia] err {url[:80]}: {e}")
        return None


def _rarity_from_personality(rank: int) -> str:
    """Villagers ont pas de rarete native. Distribue par rank."""
    if rank <= 5:   return "mythic"
    if rank <= 25:  return "legendary"
    if rank <= 80:  return "epic"
    if rank <= 200: return "rare"
    return "common"


def bulk_import_nookipedia(sleep_between: float = 0.1,
                              skip_existing: bool = True,
                              progress_cb=None) -> dict:
    """Import tous villagers AC."""
    from database import get_db, card_add
    if not os.getenv("NOOKIPEDIA_API_KEY", "").strip():
        return {"error": "NOOKIPEDIA_API_KEY non set dans .env"}

    villagers = _http_get(f"{_BASE}/villagers")
    if not villagers or not isinstance(villagers, list):
        return {"error": "Liste villagers inaccessible"}

    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0}
    total = len(villagers)
    print(f"[nookipedia] {total} villagers a importer")

    for rank, v in enumerate(villagers, start=1):
        stats["total_seen"] += 1
        try:
            name = (v.get("name") or "").strip()
            if not name:
                stats["failed"] += 1; continue
            if skip_existing and name.lower() in existing:
                stats["skipped"] += 1; continue

            img_url = v.get("image_url") or v.get("nh_details", {}).get("icon_url")
            if not img_url:
                stats["failed"] += 1; continue

            species = v.get("species") or ""
            personality = v.get("personality") or ""
            gender = v.get("gender") or ""
            phrase = v.get("phrase") or ""

            subtitle = "Animal Crossing"
            desc_parts = []
            if species: desc_parts.append(f"Species: {species}")
            if personality: desc_parts.append(f"Personality: {personality}")
            if phrase: desc_parts.append(f'"{phrase}"')
            desc = " · ".join(desc_parts)[:300] or "Villager Animal Crossing."

            rarity = _rarity_from_personality(rank)

            card_add(name=name, universe="Jeu Vidéo",
                      subtitle=subtitle, rarity=rarity,
                      image_url=img_url, description=desc)
            existing.add(name.lower())
            stats["inserted"] += 1
        except Exception as e:
            print(f"[nookipedia] insert err {v.get('name', '?')}: {e}")
            stats["failed"] += 1

        if progress_cb and rank % 10 == 0:
            try: progress_cb(rank, total)
            except Exception: pass
        time.sleep(sleep_between)

    if progress_cb:
        try: progress_cb(total, total)
        except Exception: pass
    print(f"[nookipedia] FINAL: {stats}")
    return stats
