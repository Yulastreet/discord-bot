"""Bulk import jeux video via Fandom MediaWiki API, par jeu specifique.

Chaque jeu a sa config :
- sub : sous-domaine fandom
- name : nom franchise pour subtitle
- categories : liste de Category: a query (premiere qui hit gagne)
- blacklist_patterns : regex pour exclure pages meta (ex 'Champion classes')
- title_must_match : optionnel regex que le titre DOIT matcher
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"


# Patterns blacklist par defaut (toujours appliques) : meta pages
DEFAULT_BLACKLIST = [
    r"^Category:",
    r"^Template:",
    r"^File:",
    r"^User:",
    r"^Help:",
    r"^Talk:",
    r"^Special:",
    r"^Module:",
    r"^MediaWiki:",
    r"^List of",
    r"/Gallery$",
    r"/Quotes$",
]


GAMES: dict[str, dict] = {
    "lol": {
        "sub": "leagueoflegends",
        "name": "League of Legends",
        "categories": ["Category:Champions"],
        "blacklist": [
            r"^Champion(s|\s|$)",   # 'Champion', 'Champions', 'Champion classes', etc
            r"^Gamer",              # 'Gamer's Choice Pack'
            r"Pack$",
            r"^Season",
            r"^Patch",
            r"Mastery",
            r"Skin",
            r"Update",
            r"Bundle",
            r"Roster",
            r"^Ability",
            r"^Class$",
        ],
    },
    "overwatch": {
        "sub": "overwatch",
        "name": "Overwatch",
        "categories": ["Category:Heroes", "Category:Overwatch_2_Heroes"],
        "blacklist": [
            r"^Hero ",
            r"^Heroes",
            r"Ability",
            r"^Class",
        ],
    },
    "mario": {
        "sub": "mario",
        "name": "Mario",
        "categories": ["Category:Characters"],
        "blacklist": [r"^Mario \(", r"Sub-series", r"^List "],
    },
    "sonic": {
        "sub": "sonic",
        "name": "Sonic the Hedgehog",
        "categories": ["Category:Characters"],
        "blacklist": [r"Sub-series", r"^List "],
    },
    "dragonquest": {
        "sub": "dragonquest",
        "name": "Dragon Quest",
        "categories": ["Category:Characters"],
        "blacklist": [r"Sub-series"],
    },
    "zelda": {
        "sub": "zelda",
        "name": "The Legend of Zelda",
        "categories": ["Category:Characters"],
        "blacklist": [r"Sub-series"],
    },
    "genshin": {
        "sub": "genshin-impact",
        "name": "Genshin Impact",
        "categories": ["Category:Playable_Characters",
                         "Category:Characters"],
        "blacklist": [r"^Playable", r"^Character "],
    },
    "honkai-sr": {
        "sub": "honkai-star-rail",
        "name": "Honkai: Star Rail",
        "categories": ["Category:Playable_Characters",
                         "Category:Characters"],
        "blacklist": [r"^Playable", r"^Character "],
    },
    "wow": {
        "sub": "warcraft",   # warcraft.fandom.com (wowwiki / wowpedia)
        "name": "World of Warcraft",
        "categories": ["Category:Lore_characters",
                         "Category:Major_characters",
                         "Category:Characters"],
        "blacklist": [r"^Lore ", r"^Major "],
    },
}


def _http_get_json(url: str, timeout: int = 12) -> dict | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept":     "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[fandom] HTTP {e.code} {url[:100]}")
        return None
    except Exception as e:
        print(f"[fandom] err {url[:100]}: {e}")
        return None


def _list_chars(sub: str, categories: list[str], limit: int = 500) -> list[str]:
    """Pagine categorymembers via cmcontinue, recupere jusqu'a limit titles."""
    titles: list[str] = []
    seen = set()
    for cat in categories:
        cmcontinue = None
        while len(titles) < limit:
            params = (f"action=query&format=json&list=categorymembers&"
                       f"cmtitle={urllib.parse.quote(cat)}&cmlimit=500&cmtype=page")
            if cmcontinue:
                params += f"&cmcontinue={urllib.parse.quote(cmcontinue)}"
            url = f"https://{sub}.fandom.com/api.php?{params}"
            data = _http_get_json(url)
            if not data:
                break
            members = ((data.get("query") or {}).get("categorymembers") or [])
            for m in members:
                t = m.get("title")
                if t and t not in seen:
                    seen.add(t)
                    titles.append(t)
                    if len(titles) >= limit:
                        break
            cmcontinue = ((data.get("continue") or {}).get("cmcontinue"))
            if not cmcontinue:
                break
        if titles:
            break  # category hit, stop trying fallbacks
    return titles


def _fetch_pageimages(sub: str, titles: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        titles_param = "|".join(urllib.parse.quote(t) for t in batch)
        url = (f"https://{sub}.fandom.com/api.php?action=query&format=json&"
               f"prop=pageimages&pithumbsize=600&pilimit=50&"
               f"titles={titles_param}")
        data = _http_get_json(url)
        if not data:
            continue
        pages = ((data.get("query") or {}).get("pages") or {})
        for _pid, page in pages.items():
            t = page.get("title")
            thumb = (page.get("thumbnail") or {}).get("source")
            if t and thumb:
                out[t] = thumb
    return out


def _is_blacklisted(title: str, patterns: list[str]) -> bool:
    all_patterns = DEFAULT_BLACKLIST + (patterns or [])
    for pat in all_patterns:
        if re.search(pat, title, re.IGNORECASE):
            return True
    return False


def _rarity_weighted(rank: int) -> str:
    if rank <= 20:    return "mythic"
    if rank <= 80:    return "legendary"
    if rank <= 200:   return "epic"
    if rank <= 500:   return "rare"
    return "common"


def bulk_import_game(game_key: str, limit: int = 200,
                       skip_existing: bool = True) -> dict:
    """Import 1 jeu specifique. game_key dans GAMES dict."""
    from database import get_db, card_add
    cfg = GAMES.get(game_key)
    if not cfg:
        return {"error": f"Jeu inconnu : {game_key}. Disponibles : {list(GAMES.keys())}"}

    conn = get_db(); c = conn.cursor()
    c.execute("SELECT LOWER(name) FROM cards")
    existing = {row[0] for row in c.fetchall()}
    conn.close()

    stats = {"inserted": 0, "skipped": 0, "failed": 0, "blacklisted": 0,
              "total_seen": 0}
    rank = 0
    sub = cfg["sub"]
    franchise = cfg["name"]
    titles = _list_chars(sub, cfg["categories"], limit=limit * 3)  # marge x3 blacklist
    print(f"[fandom_game] {game_key} ({sub}): {len(titles)} titles raw")
    # Filtre blacklist
    blacklist = cfg.get("blacklist", [])
    filtered = [t for t in titles if not _is_blacklisted(t, blacklist)]
    print(f"[fandom_game] {game_key}: {len(filtered)} apres blacklist")
    stats["blacklisted"] = len(titles) - len(filtered)
    filtered = filtered[:limit]
    img_map = _fetch_pageimages(sub, filtered)
    for title in filtered:
        stats["total_seen"] += 1
        rank += 1
        name = title.strip()
        if skip_existing and name.lower() in existing:
            stats["skipped"] += 1; continue
        img = img_map.get(title)
        if not img:
            stats["failed"] += 1; continue
        rarity = _rarity_weighted(rank)
        try:
            card_add(name=name, universe="Jeu Vidéo",
                      subtitle=franchise[:80],
                      rarity=rarity, image_url=img,
                      description=f"Personnage de {franchise}.")
            existing.add(name.lower())
            stats["inserted"] += 1
        except Exception as e:
            print(f"[fandom_game] insert err {name}: {e}")
            stats["failed"] += 1
    print(f"[fandom_game] {game_key} final stats={stats}")
    return stats


def bulk_import_multiple_games(game_keys: list[str], limit_per_game: int = 200,
                                 sleep_between: float = 0.5,
                                 skip_existing: bool = True) -> dict:
    """Boucle sur plusieurs game keys, agrege stats."""
    agg = {"inserted": 0, "skipped": 0, "failed": 0, "blacklisted": 0,
            "total_seen": 0, "games_done": 0, "games_failed": 0,
            "per_game": {}}
    for gk in game_keys:
        s = bulk_import_game(gk, limit=limit_per_game, skip_existing=skip_existing)
        if isinstance(s, dict) and s.get("error"):
            agg["games_failed"] += 1
            agg["per_game"][gk] = {"error": s["error"]}
        else:
            agg["games_done"] += 1
            agg["per_game"][gk] = s
            for k in ("inserted", "skipped", "failed", "blacklisted", "total_seen"):
                agg[k] += s.get(k, 0)
        time.sleep(sleep_between)
    return agg
