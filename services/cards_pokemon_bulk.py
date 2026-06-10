"""Bulk import Pokémon via PokeAPI (officielle, gratuite, sans token).

Source : https://pokeapi.co
1025 pokémons (gen 1-9), artwork officiel HD.
"""
from __future__ import annotations

import json
import time
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_BASE = "https://pokeapi.co/api/v2"
_ARTWORK = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork"


def _http_get(url: str, timeout: int = 12) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[pokemon] err {url}: {e}")
        return None


def _rarity_from_species(species: dict, base_total: int) -> str:
    if species.get("is_mythical"):
        return "mythic"
    if species.get("is_legendary"):
        return "legendary"
    # Pseudo-legendaries : 600 base stats
    if base_total >= 600:
        return "epic"
    if base_total >= 500:
        return "rare"
    return "common"


def bulk_import_pokemon(start_id: int = 1, end_id: int = 1025,
                          sleep_between: float = 0.1,
                          skip_existing: bool = True,
                          progress_cb=None) -> dict:
    """Import pokemons par ID range."""
    from database import get_db, card_add

    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0}
    total = end_id - start_id + 1

    for i, pid in enumerate(range(start_id, end_id + 1), start=1):
        stats["total_seen"] += 1
        try:
            poke = _http_get(f"{_BASE}/pokemon/{pid}")
            if not poke:
                stats["failed"] += 1
                continue
            name = (poke.get("name") or "").strip().title()
            if not name:
                stats["failed"] += 1; continue
            if skip_existing and name.lower() in existing:
                stats["skipped"] += 1; continue

            img_url = f"{_ARTWORK}/{pid}.png"
            # Stats
            base_total = sum(s.get("base_stat", 0) for s in (poke.get("stats") or []))
            types = [t.get("type", {}).get("name", "") for t in (poke.get("types") or [])]
            types_str = " / ".join(t.title() for t in types if t)

            # Species pour rarity
            species_url = (poke.get("species") or {}).get("url")
            species = _http_get(species_url) if species_url else {}
            rarity = _rarity_from_species(species or {}, base_total)
            generation = (species or {}).get("generation", {}).get("name", "")
            gen_label = generation.replace("generation-", "Gen ").upper() if generation else ""

            subtitle = f"Pokémon {gen_label}".strip()
            desc = f"Type : {types_str}. Total stats : {base_total}." if types_str else "Pokémon."

            card_add(name=name, universe="Jeu Vidéo",
                      subtitle=subtitle[:80], rarity=rarity,
                      image_url=img_url, description=desc[:300])
            existing.add(name.lower())
            stats["inserted"] += 1
        except Exception as e:
            print(f"[pokemon] insert err id={pid}: {e}")
            stats["failed"] += 1

        if progress_cb and i % 5 == 0:
            try: progress_cb(i, total)
            except Exception: pass
        if i % 50 == 0:
            print(f"[pokemon] {i}/{total} stats={stats}")
        time.sleep(sleep_between)

    if progress_cb:
        try: progress_cb(total, total)
        except Exception: pass
    print(f"[pokemon] FINAL: {stats}")
    return stats
