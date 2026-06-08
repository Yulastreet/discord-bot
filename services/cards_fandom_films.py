"""Bulk import personnages films/series TV via Fandom wikis.

Meme pattern que cards_fandom_games mais universe = 'Film/Série'.
Priorise franchises iconiques. The Amazing Digital Circus inclus.
"""
from __future__ import annotations

import time

from services.cards_fandom_games import (
    _list_chars, _fetch_pageimages, _parse_links_from_page,
    _is_blacklisted, _rarity_weighted, DEFAULT_BLACKLIST,
)


SHOWS: dict[str, dict] = {
    # === TIER S : iconique ===
    "marvel": {
        "sub": "marvel",
        "name": "Marvel",
        "categories": ["Category:Marvel_Cinematic_Universe_characters",
                         "Category:Characters"],
        "blacklist": [r"^Marvel ", r"Earth-\d"],
    },
    "dc": {
        "sub": "dc",
        "name": "DC Comics",
        "categories": ["Category:Characters"],
        "blacklist": [r"^DC ", r"^List "],
    },
    "starwars": {
        "sub": "starwars",
        "name": "Star Wars",
        "categories": ["Category:Individuals", "Category:Characters"],
        "blacklist": [r"^Individuals", r"^List "],
    },
    "harrypotter": {
        "sub": "harrypotter",
        "name": "Harry Potter",
        "categories": ["Category:Individuals", "Category:Characters"],
        "blacklist": [r"^Individuals", r"^List "],
    },
    "lotr": {
        "sub": "lotr",
        "name": "Lord of the Rings",
        "categories": ["Category:Characters", "Category:Individuals"],
        "blacklist": [r"^List "],
    },
    "got": {
        "sub": "gameofthrones",
        "name": "Game of Thrones",
        "categories": ["Category:Characters"],
        "blacklist": [r"House ", r"^List "],
    },
    "houseofthedragon": {
        "sub": "houseofthedragon",
        "name": "House of the Dragon",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    # === The Amazing Digital Circus ===
    "tadc": {
        "sub": "tadc",
        "name": "The Amazing Digital Circus",
        "categories": ["Category:Characters", "Category:Humans"],
        "blacklist": [r"^List "],
    },
    # === Pop culture animees ===
    "hazbinhotel": {
        "sub": "hazbinhotel",
        "name": "Hazbin Hotel",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "helluvaboss": {
        "sub": "helluvaboss",
        "name": "Helluva Boss",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    # === Series live action TIER A ===
    "strangerthings": {
        "sub": "strangerthings",
        "name": "Stranger Things",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "breakingbad": {
        "sub": "breakingbad",
        "name": "Breaking Bad",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "thewalkingdead": {
        "sub": "walkingdead",
        "name": "The Walking Dead",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "squidgame": {
        "sub": "squidgame",
        "name": "Squid Game",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "theoffice": {
        "sub": "theoffice",
        "name": "The Office",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "friends": {
        "sub": "friends",
        "name": "Friends",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "succession": {
        "sub": "succession",
        "name": "Succession",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    # === Cartoons ===
    "simpsons": {
        "sub": "simpsons",
        "name": "The Simpsons",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "southpark": {
        "sub": "southpark",
        "name": "South Park",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "familyguy": {
        "sub": "familyguy",
        "name": "Family Guy",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "rickandmorty": {
        "sub": "rickandmorty",
        "name": "Rick and Morty",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "spongebob": {
        "sub": "spongebob",
        "name": "SpongeBob SquarePants",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "adventuretime": {
        "sub": "adventuretime",
        "name": "Adventure Time",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    "avatar": {
        "sub": "avatar",
        "name": "Avatar: The Last Airbender",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
    # === Disney / Pixar ===
    "disney": {
        "sub": "disney",
        "name": "Disney",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List ", r"^Disney "],
    },
    "pixar": {
        "sub": "pixar",
        "name": "Pixar",
        "categories": ["Category:Characters"],
        "blacklist": [r"^List "],
    },
}


def bulk_import_show(show_key: str, limit: int = 200,
                      skip_existing: bool = True) -> dict:
    """Import 1 show specifique."""
    from database import get_db, card_add
    cfg = SHOWS.get(show_key)
    if not cfg:
        return {"error": f"Show inconnu : {show_key}. Disponibles : {list(SHOWS.keys())}"}

    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "blacklisted": 0,
              "total_seen": 0}
    rank = 0
    sub = cfg["sub"]
    franchise = cfg["name"]

    if cfg.get("list_page"):
        titles = _parse_links_from_page(sub, cfg["list_page"],
                                          suffix_filter=cfg.get("link_filter_suffix"),
                                          limit=limit * 3)
    else:
        titles = _list_chars(sub, cfg.get("categories") or [], limit=limit * 3)
    print(f"[fandom_show] {show_key} ({sub}): {len(titles)} titles raw")
    blacklist = cfg.get("blacklist", [])
    filtered = [t for t in titles if not _is_blacklisted(t, blacklist)]
    print(f"[fandom_show] {show_key}: {len(filtered)} apres blacklist")
    stats["blacklisted"] = len(titles) - len(filtered)
    filtered = filtered[:limit]
    img_map = _fetch_pageimages(sub, filtered)
    suffix_strip = cfg.get("link_filter_suffix")
    for title in filtered:
        stats["total_seen"] += 1
        rank += 1
        name = title.strip()
        if suffix_strip and name.endswith(suffix_strip):
            name = name[:-len(suffix_strip)].strip().rstrip("/")
        if not name:
            stats["failed"] += 1; continue
        if skip_existing and name.lower() in existing:
            stats["skipped"] += 1; continue
        img = img_map.get(title)
        if not img:
            stats["failed"] += 1; continue
        rarity = _rarity_weighted(rank)
        try:
            card_add(name=name, universe="Film/Série",
                      subtitle=franchise[:80],
                      rarity=rarity, image_url=img,
                      description=f"Personnage de {franchise}.")
            existing.add(name.lower())
            stats["inserted"] += 1
        except Exception as e:
            print(f"[fandom_show] insert err {name}: {e}")
            stats["failed"] += 1
    print(f"[fandom_show] {show_key} final stats={stats}")
    return stats


def bulk_import_multiple_shows(show_keys: list[str], limit_per_show: int = 200,
                                 sleep_between: float = 0.5,
                                 skip_existing: bool = True) -> dict:
    agg = {"inserted": 0, "skipped": 0, "failed": 0, "blacklisted": 0,
            "total_seen": 0, "shows_done": 0, "shows_failed": 0,
            "per_show": {}}
    for sk in show_keys:
        s = bulk_import_show(sk, limit=limit_per_show, skip_existing=skip_existing)
        if isinstance(s, dict) and s.get("error"):
            agg["shows_failed"] += 1
            agg["per_show"][sk] = {"error": s["error"]}
        else:
            agg["shows_done"] += 1
            agg["per_show"][sk] = s
            for k in ("inserted", "skipped", "failed", "blacklisted", "total_seen"):
                agg[k] += s.get(k, 0)
        time.sleep(sleep_between)
    return agg
