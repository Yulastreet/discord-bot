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


def fetch_card_image(name: str, universe: str | None = None,
                      subtitle: str | None = None) -> str | None:
    """Strategie en cascade pour obtenir une URL d'image pour une carte.

    1) Wikipedia summary EN directement avec le nom
    2) Search EN + summary sur le titre trouve
    3) Avec contexte univers (ex 'Luke Skywalker Star Wars')
    4) Wikipedia FR
    """
    # 1) Direct EN
    img = _wikipedia_summary_thumb(name, "en")
    if img:
        return img
    # 2) Search EN
    found = _wikipedia_search_first(name, "en")
    if found:
        img = _wikipedia_summary_thumb(found, "en")
        if img:
            return img
    # 3) Avec contexte
    if universe:
        ctx = f"{name} {universe}"
        found = _wikipedia_search_first(ctx, "en")
        if found:
            img = _wikipedia_summary_thumb(found, "en")
            if img:
                return img
    # 4) FR
    img = _wikipedia_summary_thumb(name, "fr")
    if img:
        return img
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
