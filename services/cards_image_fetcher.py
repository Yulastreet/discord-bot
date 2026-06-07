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


# URLs curees manuellement pour les cartes ou auto-fetch retourne le logo
# ou rien. Priorite absolue avant n'importe quelle API. Mapping name -> URL.
# URLs upload.wikimedia.org (Wikipedia Commons) ou static.wikia.nocookie.net
# direct (sans scale-to-width parametres).
CURATED_IMAGES: dict[str, str] = {
    # ===== Star Wars =====
    "Luke Skywalker":   "https://lumiere-a.akamaihd.net/v1/images/luke-skywalker-main_269abf1d.jpeg",
    "Darth Vader":      "https://lumiere-a.akamaihd.net/v1/images/databank_darthvader_01_169_a2f44f55.jpeg",
    "Yoda":             "https://lumiere-a.akamaihd.net/v1/images/databank_yoda_01_169_d5d3eaa3.jpeg",
    "The Mandalorian":  "https://lumiere-a.akamaihd.net/v1/images/the-mandalorian-main_4f297a99.jpeg",
    "Grogu":            "https://lumiere-a.akamaihd.net/v1/images/cd_grogu_f6173336.jpeg",
    "Boba Fett":        "https://lumiere-a.akamaihd.net/v1/images/databank_bobafett_01_169_e7d2f6e4.jpeg",
    "Princess Leia":    "https://lumiere-a.akamaihd.net/v1/images/databank_leiaorgana_01_169_b3aa8194.jpeg",
    "Han Solo":         "https://lumiere-a.akamaihd.net/v1/images/databank_hansolo_01_169_be20e4d1.jpeg",
    "Chewbacca":        "https://lumiere-a.akamaihd.net/v1/images/databank_chewbacca_01_169_ec05bcb5.jpeg",
    "Obi-Wan Kenobi":   "https://lumiere-a.akamaihd.net/v1/images/databank_obiwankenobi_01_169_46ab6b4a.jpeg",
    # ===== Anime (preferer Jikan via API mais en cas de fallback) =====
    "Naruto Uzumaki":   "https://cdn.myanimelist.net/images/characters/2/284121.jpg",
    "Sasuke Uchiha":    "https://cdn.myanimelist.net/images/characters/9/131317.jpg",
    "Goku":             "https://cdn.myanimelist.net/images/characters/7/284129.jpg",
    "Vegeta":           "https://cdn.myanimelist.net/images/characters/16/47282.jpg",
    "Monkey D. Luffy":  "https://cdn.myanimelist.net/images/characters/9/310307.jpg",
    "Roronoa Zoro":     "https://cdn.myanimelist.net/images/characters/3/100534.jpg",
    "Levi Ackerman":    "https://cdn.myanimelist.net/images/characters/2/241413.jpg",
    "Eren Yeager":      "https://cdn.myanimelist.net/images/characters/10/216895.jpg",
    "Tanjiro Kamado":   "https://cdn.myanimelist.net/images/characters/6/386735.jpg",
    "Nezuko Kamado":    "https://cdn.myanimelist.net/images/characters/9/383496.jpg",
    "Gojo Satoru":      "https://cdn.myanimelist.net/images/characters/7/422168.jpg",
    "Itadori Yuji":     "https://cdn.myanimelist.net/images/characters/13/424044.jpg",
    "Lelouch Lamperouge": "https://cdn.myanimelist.net/images/characters/3/50617.jpg",
    "Light Yagami":     "https://cdn.myanimelist.net/images/characters/6/63870.jpg",
    "Spike Spiegel":    "https://cdn.myanimelist.net/images/characters/8/82533.jpg",
    # ===== Jeux Video =====
    "Master Chief":     "https://upload.wikimedia.org/wikipedia/en/d/d6/Master_Chief_helmet.svg",
    "Mario":            "https://mario.wiki.gallery/images/thumb/4/4d/MarioNSMBUDeluxe.png/800px-MarioNSMBUDeluxe.png",
    "Link":             "https://www.zeldadungeon.net/wiki/images/c/c9/Link-TotK-Render.png",
    "Kratos":           "https://upload.wikimedia.org/wikipedia/en/d/d0/Kratos_-_PlayStation_-_The_Concept.png",
    "Geralt of Rivia":  "https://upload.wikimedia.org/wikipedia/en/0/03/Geralt_of_Rivia_05.jpg",
    "Solid Snake":      "https://upload.wikimedia.org/wikipedia/en/8/8b/Solid_Snake_MGS2.jpg",
    "Lara Croft":       "https://upload.wikimedia.org/wikipedia/en/6/63/Lara_Croft_2013.png",
    "Sonic the Hedgehog": "https://upload.wikimedia.org/wikipedia/en/b/b4/Sonic_modern_and_classic_designs.png",
    "Pikachu":          "https://upload.wikimedia.org/wikipedia/en/a/a6/Pok%C3%A9mon_Pikachu_art.png",
    "Cloud Strife":     "https://upload.wikimedia.org/wikipedia/en/c/c7/Cloud_Strife_Final_Fantasy_VII_Remake.png",
    # ===== Hazbin Hotel / Helluva Boss =====
    "Charlie Morningstar": "https://static.wikia.nocookie.net/hazbinhotel/images/0/0d/Charlie_Morningstar.png/revision/latest?cb=20240120175430",
    "Alastor":          "https://static.wikia.nocookie.net/hazbinhotel/images/3/36/Alastor_Profile.png/revision/latest?cb=20240120175438",
    "Vaggie":           "https://static.wikia.nocookie.net/hazbinhotel/images/9/9c/Vaggie_Profile.png/revision/latest?cb=20240120175433",
    "Angel Dust":       "https://static.wikia.nocookie.net/hazbinhotel/images/3/3d/Angel_Dust_Profile.png/revision/latest?cb=20240120175433",
    "Blitzo":           "https://static.wikia.nocookie.net/helluva-boss/images/4/41/Blitzo.png/revision/latest?cb=20221008041648",
    # ===== Amazing Digital Circus =====
    "Pomni":            "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/9/9a/Pomni_alt.png/revision/latest",
    "Caine":            "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/9/9e/Caine.png/revision/latest",
    "Ragatha":          "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/4/4f/Ragatha.png/revision/latest",
    "Jax":              "https://static.wikia.nocookie.net/the-amazing-digital-circus/images/4/47/Jax.png/revision/latest",
    # ===== Pop Culture divers =====
    "Stan Marsh":       "https://static.wikia.nocookie.net/southpark/images/d/db/Stan_Marsh.png/revision/latest",
    "Homer Simpson":    "https://upload.wikimedia.org/wikipedia/en/0/02/Homer_Simpson_2006.png",
    "SpongeBob":        "https://upload.wikimedia.org/wikipedia/en/3/3b/SpongeBob_SquarePants_character.svg",
    "Rick Sanchez":     "https://upload.wikimedia.org/wikipedia/en/a/a6/Rick_Sanchez.png",
    "Walter White":     "https://upload.wikimedia.org/wikipedia/en/0/03/Walter_White_S5B.png",
    "Shrek":            "https://upload.wikimedia.org/wikipedia/en/4/4a/Shrek_%28character%29.png",
}


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
    """Strategie cascade multi-sources.

    0) CURATED_IMAGES (override manuel, priorite absolue)
    1) Wikipedia EN summary direct
    2) Wikipedia EN pageimages direct (plus exhaustif que summary)
    3) Wikipedia EN search + summary
    4) Avec contexte univers ('Luke Skywalker Star Wars')
    5) Fandom (mapping franchise -> sous-domaine connu)
    6) Jikan API (si universe = Anime)
    7) Wikipedia FR
    """
    # 0) Curated override
    if name in CURATED_IMAGES:
        return CURATED_IMAGES[name]
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
