"""Fetch image URL pour une carte depuis Wikipedia API.

Wikipedia REST API : /api/rest_v1/page/summary/<title> retourne un JSON
incluant `thumbnail.source` qui est une URL d'image stable (upload.wikimedia.org).

Pour les personnages obscurs sans page Wikipedia, fallback sur recherche
Wikipedia (action=opensearch) + retry.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request


_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_DEFAULT_TIMEOUT = 8


def _http_get(url: str, timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _wikipedia_summary_thumb(title: str, lang: str = "en") -> str | None:
    """Recupere thumbnail.source depuis l'API summary."""
    if not title:
        return None
    enc = urllib.parse.quote(title.replace(" ", "_"))
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{enc}"
    data = _http_get(url)
    if not data:
        return None
    thumb = (data.get("originalimage") or data.get("thumbnail") or {}).get("source")
    return thumb


def _wikipedia_search_first(query: str, lang: str = "en") -> str | None:
    """Cherche le 1er resultat Wikipedia pour ce query, retourne le titre."""
    if not query:
        return None
    url = (f"https://{lang}.wikipedia.org/w/api.php?"
           f"action=opensearch&format=json&limit=1&"
           f"search={urllib.parse.quote(query)}")
    data = _http_get(url)
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return None
    return data[1][0]


def _wikipedia_pageimages(title: str, lang: str = "en") -> str | None:
    """Utilise action=query&prop=pageimages qui marche meme quand summary
    n'a pas d'image. Plus exhaustif."""
    if not title:
        return None
    url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&format=json&"
           f"prop=pageimages&pithumbsize=600&pilimit=1&"
           f"titles={urllib.parse.quote(title.replace(' ', '_'))}")
    data = _http_get(url)
    if not data:
        return None
    pages = ((data.get("query") or {}).get("pages") or {})
    for _pid, page in pages.items():
        if page.get("pageid", 0) <= 0:
            continue
        thumb = (page.get("thumbnail") or {}).get("source")
        if thumb:
            return thumb
    return None


def _jikan_character_image(name: str) -> str | None:
    """API Jikan (MyAnimeList non-officielle) pour personnages anime.
    Pas de cle requise."""
    if not name:
        return None
    url = (f"https://api.jikan.moe/v4/characters?"
           f"q={urllib.parse.quote(name)}&limit=1&order_by=favorites&sort=desc")
    data = _http_get(url)
    if not data:
        return None
    items = data.get("data") or []
    if not items:
        return None
    images = (items[0].get("images") or {})
    # Preference webp > jpg
    for kind in ("webp", "jpg"):
        v = (images.get(kind) or {}).get("image_url")
        if v:
            return v
    return None


def _fandom_search_thumb(name: str, universe_keyword: str | None = None) -> str | None:
    """Cherche sur Fandom via leur API search globale, qui retourne des
    pages avec un thumbnail. Endpoint : community.fandom.com cross-wiki.

    En pratique on cible le wiki dedie au franchise. Map quelques univers
    connus -> sous-domaine fandom. Pour le reste, fallback global search.
    """
    if not name:
        return None
    wiki_map = {
        "star wars":          "starwars",
        "anime":              None,        # ambigus, voir below
        "one piece":          "onepiece",
        "naruto":             "naruto",
        "dragon ball":        "dragonball",
        "attack on titan":    "shingekinokyojin",
        "demon slayer":       "kimetsu-no-yaiba",
        "jujutsu kaisen":     "jujutsu-kaisen",
        "code geass":         "codegeass",
        "death note":         "deathnote",
        "cowboy bebop":       "cowboybebop",
        "halo":               "halo",
        "zelda":              "zelda",
        "god of war":         "godofwar",
        "the witcher":        "witcher",
        "metal gear":         "metalgear",
        "tomb raider":        "tombraider",
        "pokemon":            "pokemon",
        "final fantasy vii":  "finalfantasy",
        "hazbin hotel":       "hazbinhotel",
        "helluva boss":       "helluva-boss",
        "digital circus":     "the-amazing-digital-circus",
        "south park":         "southpark",
        "the simpsons":       "simpsons",
        "nickelodeon":        "spongebob",
        "rick and morty":     "rickandmorty",
        "breaking bad":       "breakingbad",
        "dreamworks":         "shrek",
        "nintendo":           "mario",
        "sega":               "sonic",
    }
    sub = None
    if universe_keyword:
        sub = wiki_map.get(universe_keyword.lower())
    if not sub:
        return None
    # API : recherche puis page details
    search_url = (f"https://{sub}.fandom.com/api.php?action=opensearch&format=json&"
                  f"limit=1&search={urllib.parse.quote(name)}")
    res = _http_get(search_url)
    if not isinstance(res, list) or len(res) < 2 or not res[1]:
        return None
    page_title = res[1][0]
    # Pageimages sur ce wiki Fandom (compatible MediaWiki API)
    img_url = (f"https://{sub}.fandom.com/api.php?action=query&format=json&"
               f"prop=pageimages&pithumbsize=600&titles={urllib.parse.quote(page_title.replace(' ', '_'))}")
    data = _http_get(img_url)
    if not data:
        return None
    pages = ((data.get("query") or {}).get("pages") or {})
    for _pid, page in pages.items():
        thumb = (page.get("thumbnail") or {}).get("source")
        if thumb:
            return thumb
    return None


def fetch_card_image(name: str, universe: str | None = None,
                      subtitle: str | None = None) -> str | None:
    """Strategie cascade multi-sources :

    1) Wikipedia EN summary direct
    2) Wikipedia EN pageimages direct (plus exhaustif que summary)
    3) Wikipedia EN search + summary
    4) Avec contexte univers ('Luke Skywalker Star Wars')
    5) Fandom (mapping franchise -> sous-domaine connu)
    6) Jikan API (si universe = Anime)
    7) Wikipedia FR
    """
    # Try by character name strategies
    # 1)
    img = _wikipedia_summary_thumb(name, "en")
    if img: return img
    # 2)
    img = _wikipedia_pageimages(name, "en")
    if img: return img
    # 3)
    found = _wikipedia_search_first(name, "en")
    if found:
        img = _wikipedia_summary_thumb(found, "en") or _wikipedia_pageimages(found, "en")
        if img: return img
    # 4) Avec contexte
    if universe:
        ctx = f"{name} {universe}"
        found = _wikipedia_search_first(ctx, "en")
        if found:
            img = _wikipedia_summary_thumb(found, "en") or _wikipedia_pageimages(found, "en")
            if img: return img
    # 5) Fandom (cible le wiki du franchise)
    # subtitle est souvent le franchise (ex 'One Piece') donc essaie d'abord
    for key in (subtitle, universe):
        if key:
            img = _fandom_search_thumb(name, key)
            if img: return img
    # 6) Jikan (anime)
    if universe and universe.lower() in ("anime", "manga"):
        img = _jikan_character_image(name)
        if img: return img
    # 7) FR
    img = _wikipedia_summary_thumb(name, "fr") or _wikipedia_pageimages(name, "fr")
    if img: return img
    return None


def refresh_all_cards_images(force_overwrite: bool = False) -> dict:
    """Boucle sur toutes les cartes du catalogue, met a jour image_url.

    Si force_overwrite=False, ne touche pas aux cartes qui ont deja une
    image_url. Retourne stats {updated, kept, failed}.
    """
    from database import get_db, card_list_all
    stats = {"updated": 0, "kept": 0, "failed": 0}
    rows = card_list_all(limit=5000)
    for r in rows:
        if r.get("image_url") and not force_overwrite:
            stats["kept"] += 1
            continue
        img = fetch_card_image(r["name"], r.get("universe"), r.get("subtitle"))
        if img:
            conn = get_db(); c = conn.cursor()
            c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                       (img, r["id"]))
            conn.commit(); conn.close()
            stats["updated"] += 1
        else:
            stats["failed"] += 1
    return stats
