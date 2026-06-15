"""Import des creatures Bakugan via le Fandom wiki (bakugan.fandom.com).

Source : Category:Bakugan (~500 pages, mixte). On ne garde QUE les vraies
creatures = pages ayant un attribut (Pyrus/Aquos/Haos/Darkus/Ventus/Subterra)
ET une image d'infobox. Ca vire les accessoires/jouets/listes.

  universe = "Bakugan", subtitle (origine) = "Bakugan",
  flavor_subtitle = l'attribut Bakugan, element = ALEATOIRE (sans rapport),
  rarete = aleatoire ponderee, variantes "(Generation X)" FUSIONNEES (1 carte/nom).

Idempotent : un nom deja en base est ignore. L'image (URL wikia) est hebergee
en local au prochain bake (loop quotidienne) ou via scripts/bake_all_renders.py.

Usage (depuis ~/discord-bot) :
    python3 scripts/import_bakugan.py --dry-run     # apercu, n'ecrit rien
    python3 scripts/import_bakugan.py               # import
    python3 scripts/import_bakugan.py --limit 20    # test
    python3 scripts/import_bakugan.py --rarity rare # force une rarete
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

_API = "https://bakugan.fandom.com/api.php"
_UNIVERSE = "Anime"
_ORIGIN = "Bakugan"
_UA = "TookBot/1.0 (https://tookbot.click)"

# Attributs Bakugan (pour le flavor). Pas un filtre fiable seul (certaines creatures
# n'ont pas de cat attribut, certains accessoires en ont une).
_ATTRS = ("Pyrus", "Aquos", "Haos", "Darkus", "Ventus", "Subterra")

# Categories qui marquent un NON-creature (accessoire / jouet / arme).
_JUNK_CATS = {
    "Merchandise", "Weapons", "Ultimate Weapons", "Vestal Technology",
    "Battle Gear", "BakuNano", "Mobile Assault", "Traps", "Gauntlets",
    "Bakugan vs Marvel",
}
# Titres clairement non-creature (accessoires, pages concept/mode/attaque).
_JUNK_TITLE = re.compile(
    r"^(Baku[\s\-]?[A-Z0-9]|List of\b|Unknown\b|Unnamed\b)"
    r"|(\bSystem\b|\bLauncher\b|\bGauntlet\b|\bGear\b|\bMode\b|\bGate Cards\b"
    r"|\bSpecial Attack\b|\bAttribute\b|\bTreatments?\b|\bSoldiers\b)",
    re.I)


def _creature_attr(title, cats):
    """Retourne l'attribut Bakugan si la page est une vraie creature, sinon None.
    Creature = a une categorie espece '... Bakugan' OU un attribut, ET pas de
    marqueur junk. Exclut les pages concept/groupe dont le NOM finit par 'Bakugan'
    (ex 'Guardian Bakugan', 'Nonet Bakugan') et les pages d'attribut."""
    if title.endswith(" Bakugan") or title in _ATTRS:
        return False, None
    names = {c.replace("Category:", "") for c in cats}
    if names & _JUNK_CATS or _JUNK_TITLE.search(title):
        return False, None
    # categorie espece : '... Bakugan' autre que la categorie parente nue 'Bakugan'
    has_species = any(n.endswith(" Bakugan") for n in names)
    attr = next((a for a in _ATTRS if a in names), None)
    if not (has_species or attr):
        return False, None
    return True, attr

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
    """Tous les titres (namespace 0) de Category:Bakugan, avec pagination."""
    titles = []
    cont = None
    while True:
        p = {"action": "query", "list": "categorymembers",
             "cmtitle": "Category:Bakugan", "cmtype": "page",
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


def _clean_name(title):
    # fusionne les variantes : "Dragonoid (Generation 1)" -> "Dragonoid"
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def main():
    import random
    ap = argparse.ArgumentParser()
    ap.add_argument("--rarity", choices=list(_RARITY_SPREAD.keys()),
                    help="Force une rarete (sinon ponderee)")
    ap.add_argument("--limit", type=int, default=0, help="Limite (test)")
    ap.add_argument("--dry-run", action="store_true", help="N'ecrit rien")
    args = ap.parse_args()

    print("Recuperation de la liste Category:Bakugan...")
    titles = _all_member_titles()
    print(f"{len(titles)} pages dans Category:Bakugan.\n")

    added = skipped = nocreature = 0
    seen_names = set()   # dedup par nom de base (variantes fusionnees)

    # Batch de 50 titres : prop=pageimages (image infobox) + categories (attribut)
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
            is_creature, attr = _creature_attr(title, cats)
            if not img or not is_creature:
                nocreature += 1
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
            rarity = args.rarity or _pick_rarity()
            if args.dry_run:
                print(f"[dry] + {name:24} {rarity:9} {(attr or '-'):9} {img[:55]}")
                added += 1
                continue
            try:
                cid = card_add(name=name, universe=_UNIVERSE, subtitle=_ORIGIN,
                               rarity=rarity, image_url=img, flavor_subtitle=attr)
                print(f"+ #{cid} {name:24} {rarity:9} {attr}")
                added += 1
            except Exception as e:
                print(f"! erreur sur {name}: {e!r}")
        if args.limit and added >= args.limit:
            break

    print(f"\nTermine. Ajoutes: {added} | Ignores (deja en base): {skipped} | "
          f"Non-creatures filtrees: {nocreature}")
    if not args.dry_run and added:
        print("Images hebergees au prochain bake (loop quotidienne) ou "
              "scripts/bake_all_renders.py.")


if __name__ == "__main__":
    main()
