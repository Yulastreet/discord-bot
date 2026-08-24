"""Bulk import via hakush.in API (Wuthering Waves, Marvel Rivals, etc).

Unofficial public API aggregating data from miHoYo games and others.
- Wuthering Waves : api.hakush.in/ww/data/character.json
- Marvel Rivals   : api.hakush.in/mr/data/character.json
- Zenless Zone Zero : api.hakush.in/zzz/data/character.json
- Genshin / HSR / HI3 disponibles aussi

Sans token, gratuit.
"""
from __future__ import annotations

import json
import time
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"


# Config par jeu : (slug api, nom complet, universe, subtitle, img base)
GAMES_HAKUSH = {
    "wuwa": {
        "name": "Wuthering Waves",
        "list_url": "https://api.hakush.in/ww/data/character.json",
        "detail_url": "https://api.hakush.in/ww/data/en/character/{id}.json",
        "img_base": "https://api.hakush.in/ww/UI/UIResources/Common/Image/IconRoleHead256/T_IconRoleHead256_{id}_UI.webp",
    },
    "mr": {
        "name": "Marvel Rivals",
        "list_url": "https://api.hakush.in/mr/data/character.json",
        "detail_url": "https://api.hakush.in/mr/data/en/character/{id}.json",
        "img_base": "https://api.hakush.in/mr/UI/Characters/{id}_full.webp",
    },
    "zzz": {
        "name": "Zenless Zone Zero",
        "list_url": "https://api.hakush.in/zzz/data/character.json",
        "detail_url": "https://api.hakush.in/zzz/data/en/character/{id}.json",
        "img_base": "https://api.hakush.in/zzz/UI/Sprite/DynamicResources/IconRoleSelect/IconRoleSelect{id}.webp",
    },
}


def _http_get(url: str, timeout: int = 15) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[hakush] err {url[:80]}: {e}")
        return None


def _rarity_from_rank(rank_value, default_rank: int) -> str:
    """Hakush rank conventions :
    - WuWa : 5 stars, 4 stars
    - MR : Generic / Excellent / Master (skip)
    - ZZZ : S, A, B
    Default fallback par rank d'insertion."""
    if rank_value:
        s = str(rank_value).strip()
        if s in ("5", "S", "Master"):     return "mythic"
        if s in ("4", "A", "Excellent"):  return "legendary"
        if s in ("3", "B", "Generic"):    return "epic"
    if default_rank <= 5:   return "mythic"
    if default_rank <= 20:  return "legendary"
    if default_rank <= 60:  return "epic"
    return "rare"


def bulk_import_hakush(game_key: str, sleep_between: float = 0.3,
                         skip_existing: bool = True,
                         progress_cb=None) -> dict:
    """Import chars d'un jeu hakush.in."""
    from database import get_db, card_add
    cfg = GAMES_HAKUSH.get(game_key)
    if not cfg:
        return {"error": f"Jeu hakush inconnu : {game_key}"}

    list_data = _http_get(cfg["list_url"])
    if not list_data:
        return {"error": f"Liste {game_key} inaccessible"}

    # Format hakush : dict {id: {name, rank, element, etc}} ou list
    if isinstance(list_data, dict):
        items = []
        for cid, item in list_data.items():
            if not isinstance(item, dict): continue
            item["_id"] = cid
            items.append(item)
    elif isinstance(list_data, list):
        items = list_data
    else:
        return {"error": "Format API inattendu"}

    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "total_seen": 0}
    total = len(items)
    print(f"[hakush] {game_key}: {total} items")

    for rank, item in enumerate(items, start=1):
        stats["total_seen"] += 1
        try:
            cid = item.get("_id") or item.get("Id") or item.get("id") or ""
            # Nom : prefere EN si dispo
            name = (item.get("EN") or item.get("en") or item.get("Name")
                    or item.get("name") or "").strip()
            if not name:
                stats["failed"] += 1; continue
            if skip_existing and name.lower() in existing:
                stats["skipped"] += 1; continue

            # Image
            img_url = cfg["img_base"].format(id=cid)

            # Rarity
            rank_val = item.get("rank") or item.get("Rank") or item.get("Quality")
            rarity = _rarity_from_rank(rank_val, rank)

            # Subtitle : element / faction si dispo
            element = item.get("element") or item.get("Element") or item.get("Weapon")
            subtitle = f"{cfg['name']}" + (f" · {element}" if element else "")

            desc = item.get("desc") or item.get("Description") or f"Personnage de {cfg['name']}."

            card_add(name=name, universe="Jeu Vidéo",
                      subtitle=subtitle[:80], rarity=rarity,
                      image_url=img_url, description=str(desc)[:300])
            existing.add(name.lower())
            stats["inserted"] += 1
        except Exception as e:
            print(f"[hakush] insert err {item.get('name', '?')}: {e}")
            stats["failed"] += 1

        if progress_cb and rank % 5 == 0:
            try: progress_cb(rank, total)
            except Exception: pass
        time.sleep(sleep_between)

    if progress_cb:
        try: progress_cb(total, total)
        except Exception: pass
    print(f"[hakush] {game_key} FINAL: {stats}")
    return stats
