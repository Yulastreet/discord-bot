"""Import des personnages Game of Thrones via le Fandom wiki (gameofthrones.fandom.com).

Source : Category:Characters. On ne garde QUE les pages ayant une image d'infobox
(vire les ebauches / pages sans portrait) et on filtre les titres non-personnage
(House ..., List of ..., disambiguation).

  universe = "Film/Série", subtitle (origine) = "Game of Thrones",
  flavor_subtitle = la Maison (House X) si trouvee dans les categories,
  element = ALEATOIRE (combat), rarete = aleatoire ponderee.

Idempotent : un nom deja en base est ignore. L'image (URL wikia) est hebergee
en local au prochain bake (loop quotidienne) ou via scripts/bake_all_renders.py.

Usage (depuis ~/discord-bot) :
    python3 scripts/import_got.py --dry-run      # apercu, n'ecrit rien
    python3 scripts/import_got.py                # import
    python3 scripts/import_got.py --limit 20     # test
    python3 scripts/import_got.py --rarity rare  # force une rarete
"""
import argparse
import os
import re
import sys
import time
import urllib.parse
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from database import card_add, card_get_by_name  # noqa: E402

_API = "https://gameofthrones.fandom.com/api.php"
_UNIVERSE = "Film/Série"
_ORIGIN = "Game of Thrones"
_CATEGORY = "Category:Individuals appearing in Game of Thrones"
_UA = "TookBot/1.0 (https://tookbot.click)"

# Titres clairement non-personnage.
_JUNK_TITLE = re.compile(
    r"^(House\b|List of\b|Unknown\b|Unnamed\b|Category\b)"
    r"|\(disambiguation\)"
    r"|\b(House|Houses|Family|Lineage|Characters?)\b$",
    re.I)

_RARITY_SPREAD = {"common": 50, "rare": 30, "epic": 15, "legendary": 4, "mythic": 1}


def _api(params):
    params = dict(params); params["format"] = "json"
    url = _API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    import json
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 3:
                raise
            print(f"  (retry {attempt+1} apres erreur: {e})")
            time.sleep(2)


def _all_member_titles():
    """Tous les titres (namespace 0) de Category:Characters, avec pagination."""
    titles = []
    cont = None
    while True:
        p = {"action": "query", "list": "categorymembers",
             "cmtitle": _CATEGORY, "cmtype": "page",
             "cmnamespace": "0", "cmlimit": "500"}
        if cont:
            p["cmcontinue"] = cont
        d = _api(p)
        titles += [m["title"] for m in d["query"]["categorymembers"]]
        cont = d.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    return titles


def _pick_rarity():
    import random
    return random.choices(list(_RARITY_SPREAD.keys()),
                          weights=list(_RARITY_SPREAD.values()), k=1)[0]


def _house(cats):
    """Maison du personnage depuis les categories ('Members of House Stark' -> 'House Stark')."""
    names = [c.replace("Category:", "") for c in cats]
    for n in names:
        if n.startswith("Members of House "):
            return n.replace("Members of ", "")
    for n in names:
        if n.startswith("House "):
            return n
    return None


def _clean_name(title):
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rarity", choices=list(_RARITY_SPREAD.keys()),
                    help="Force une rarete (sinon ponderee)")
    ap.add_argument("--limit", type=int, default=0, help="Limite (test)")
    ap.add_argument("--dry-run", action="store_true", help="N'ecrit rien")
    args = ap.parse_args()

    print(f"Recuperation de la liste {_CATEGORY}...")
    titles = _all_member_titles()
    print(f"{len(titles)} pages dans {_CATEGORY}.\n")

    added = skipped = nocard = 0
    seen_names = set()

    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        d = _api({"action": "query", "titles": "|".join(batch),
                  "prop": "pageimages|categories", "piprop": "original",
                  "cllimit": "500"})
        pages = d.get("query", {}).get("pages", {})
        for pg in pages.values():
            title = pg.get("title", "")
            cats = [c["title"] for c in pg.get("categories", [])]
            img = pg.get("original", {}).get("source")
            if not img or _JUNK_TITLE.search(title):
                nocard += 1
                continue
            name = _clean_name(title)
            key = name.lower()
            if not name or key in seen_names:
                continue
            seen_names.add(key)
            if card_get_by_name(name):
                skipped += 1
                continue
            if args.limit and added >= args.limit:
                continue
            house = _house(cats)
            rarity = args.rarity or _pick_rarity()
            if args.dry_run:
                print(f"[dry] + {name:28} {rarity:9} {(house or '-'):16} {img[:55]}")
                added += 1
                continue
            try:
                cid = card_add(name=name, universe=_UNIVERSE, subtitle=_ORIGIN,
                               rarity=rarity, image_url=img, flavor_subtitle=house)
                print(f"+ #{cid} {name:28} {rarity:9} {house or ''}")
                added += 1
            except Exception as e:
                print(f"! erreur sur {name}: {e!r}")
        if args.limit and added >= args.limit:
            break

    print(f"\nTermine. Ajoutes: {added} | Ignores (deja en base): {skipped} | "
          f"Sans image/filtres: {nocard}")
    if not args.dry_run and added:
        print("Images hebergees au prochain bake (loop quotidienne) ou "
              "scripts/bake_all_renders.py.")


if __name__ == "__main__":
    main()
