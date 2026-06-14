"""Import des personnages Animal Crossing (villageois) via l'API Nookipedia.

Ajoute chaque villageois au catalogue de cartes :
  universe = "Jeu Vidéo", subtitle (origine) = "Animal Crossing".
Idempotent : les cartes deja presentes (meme nom) sont ignorees.

Cle API : variable d'env NOOKIPEDIA_API_KEY (recommande) ou --key <cle>.

Usage (depuis ~/discord-bot) :
    export NOOKIPEDIA_API_KEY=xxxxxxxx
    python3 scripts/import_animal_crossing.py --dry-run        # apercu, n'ecrit rien
    python3 scripts/import_animal_crossing.py                  # import (rarete repartie)
    python3 scripts/import_animal_crossing.py --rarity rare    # force une rarete
    python3 scripts/import_animal_crossing.py --limit 10       # test sur 10
"""
import argparse
import json
import os
import random
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import card_add, card_get_by_name  # noqa: E402

_UNIVERSE = "Jeu Vidéo"
_ORIGIN = "Animal Crossing"
_API = "https://api.nookipedia.com/villagers?game=nh"

# Repartition de rarete par defaut (comme la distribution globale des rolls)
_RARITY_SPREAD = {
    "common": 50, "rare": 30, "epic": 15, "legendary": 4, "mythic": 1,
}


def _fetch(api_key):
    req = urllib.request.Request(_API, headers={
        "X-API-KEY": api_key,
        "Accept-Version": "1.0.0",
        "User-Agent": "TookBot/1.0 (https://tookbot.click)",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _pick_rarity():
    return random.choices(list(_RARITY_SPREAD.keys()),
                          weights=list(_RARITY_SPREAD.values()), k=1)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", help="Cle API Nookipedia (sinon env NOOKIPEDIA_API_KEY)")
    ap.add_argument("--rarity", choices=list(_RARITY_SPREAD.keys()),
                    help="Force une rarete pour tous (sinon repartie)")
    ap.add_argument("--limit", type=int, default=0, help="Limite (test)")
    ap.add_argument("--dry-run", action="store_true", help="N'ecrit rien")
    args = ap.parse_args()

    api_key = args.key or os.getenv("NOOKIPEDIA_API_KEY")
    if not api_key:
        print("ERREUR : cle API absente. export NOOKIPEDIA_API_KEY=... ou --key")
        sys.exit(1)

    print("Recuperation des villageois...")
    villagers = _fetch(api_key)
    if args.limit:
        villagers = villagers[:args.limit]
    print(f"{len(villagers)} villageois recus.\n")

    added = skipped = noimg = 0
    for v in villagers:
        name = (v.get("name") or "").strip()
        img = (v.get("image_url") or "").strip()
        if not name:
            continue
        if not img:
            noimg += 1
            continue
        if card_get_by_name(name):
            skipped += 1
            continue
        rarity = args.rarity or _pick_rarity()
        # flavor : espece + personnalite ; description : citation du villageois
        flavor = " · ".join(p for p in (v.get("species"), v.get("personality")) if p)
        quote = (v.get("quote") or "").strip() or None
        if args.dry_run:
            print(f"[dry] + {name:22} {rarity:9} {flavor}")
            added += 1
            continue
        try:
            cid = card_add(name=name, universe=_UNIVERSE, subtitle=_ORIGIN,
                           rarity=rarity, image_url=img,
                           description=quote, flavor_subtitle=flavor or None)
            print(f"+ #{cid} {name:22} {rarity:9} {flavor}")
            added += 1
        except Exception as e:
            print(f"! erreur sur {name}: {e!r}")

    print(f"\nTermine. Ajoutes: {added} | Ignores (deja presents): {skipped} | Sans image: {noimg}")
    if not args.dry_run and added:
        print("Pense a (re)generer les renders/overlays si besoin pour ces cartes.")


if __name__ == "__main__":
    main()
