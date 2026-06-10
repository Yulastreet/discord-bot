"""Bulk import via SuperHero API (mirror GitHub akabab/superhero-api).

Source : https://github.com/akabab/superhero-api
Dump JSON public : ~731 superheros Marvel/DC/Image/Dark Horse/Indie.
Pas de token requis (mirror libre).

Format JSON : list de dicts {id, name, powerstats, biography (publisher,
alignment, aliases), appearance, work, connections, images.lg/md/sm/xs}.

Filter par publisher si necessaire (Marvel Comics, DC Comics, etc).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_DUMP_URL = "https://raw.githubusercontent.com/akabab/superhero-api/master/api/all.json"


def _fetch_dump(timeout: int = 30) -> list[dict] | None:
    req = urllib.request.Request(_DUMP_URL, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[superhero] dump fetch err: {e}")
        return None


def _rarity_for_publisher(publisher: str, rank: int) -> str:
    """Marvel/DC = chars iconiques -> distribution standard.
    Autres editeurs = plus rares (boost rarete)."""
    big = publisher in ("Marvel Comics", "DC Comics")
    if big:
        if rank <= 5:   return "mythic"
        if rank <= 20:  return "legendary"
        if rank <= 60:  return "epic"
        if rank <= 150: return "rare"
        return "common"
    # Petits editeurs : tout en epic+
    if rank <= 3:   return "mythic"
    if rank <= 10:  return "legendary"
    return "epic"


def bulk_import_superhero(publishers: list[str] | None = None,
                            skip_existing: bool = True,
                            limit: int | None = None,
                            progress_cb=None) -> dict:
    """Import superhero dataset.
    publishers : filter (ex ['Marvel Comics']). None = tous editeurs.
    limit : max chars a inserer.
    """
    from database import get_db, card_add

    dump = _fetch_dump()
    if not dump:
        return {"error": "Dump JSON inaccessible (GitHub raw down ?)"}

    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    # Group par publisher pour distribution rarete
    by_publisher: dict[str, list[dict]] = {}
    for ch in dump:
        pub = (((ch.get("biography") or {}).get("publisher")) or "").strip() or "Inconnu"
        if publishers and pub not in publishers:
            continue
        by_publisher.setdefault(pub, []).append(ch)

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "by_publisher": {}}
    for pub, chars in by_publisher.items():
        # Sort par id asc (early = iconiques)
        chars.sort(key=lambda x: int(x.get("id", 0)))
        pub_stats = {"total": len(chars), "inserted": 0}
        for rank, ch in enumerate(chars, start=1):
            try:
                name = (ch.get("name") or "").strip()
                if not name:
                    stats["failed"] += 1; continue
                if skip_existing and name.lower() in existing:
                    stats["skipped"] += 1; continue
                if limit and stats["inserted"] >= limit:
                    break

                # Image : prefere lg > md > sm
                images = ch.get("images") or {}
                img_url = (images.get("lg") or images.get("md")
                            or images.get("sm") or "")
                if not img_url:
                    stats["failed"] += 1; continue
                if img_url.startswith("http://"):
                    img_url = "https://" + img_url[7:]

                bio = ch.get("biography") or {}
                aliases = bio.get("aliases") or []
                alias_str = ", ".join(aliases[:3]) if aliases else ""
                desc_parts = []
                if bio.get("alterEgos") and bio.get("alterEgos") != "No alter egos found.":
                    desc_parts.append(f"Alter ego : {bio.get('alterEgos')}")
                if alias_str:
                    desc_parts.append(f"Alias : {alias_str}")
                desc = " · ".join(desc_parts)[:300] or f"Personnage {pub}."

                rarity = _rarity_for_publisher(pub, rank)
                # Universe : 'Comics' pour grouper Marvel/DC dans dashboard
                # Subtitle = publisher (Marvel Comics, DC Comics, etc)
                card_add(name=name, universe="Comics",
                          subtitle=pub[:80], rarity=rarity,
                          image_url=img_url, description=desc)
                existing.add(name.lower())
                stats["inserted"] += 1
                pub_stats["inserted"] += 1
            except Exception as e:
                print(f"[superhero] insert err {ch.get('name')}: {e}")
                stats["failed"] += 1
        stats["by_publisher"][pub] = pub_stats
        print(f"[superhero] {pub}: +{pub_stats['inserted']}/{pub_stats['total']}")

    print(f"[superhero] FINAL: {stats}")
    return stats
