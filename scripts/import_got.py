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

# Spread pour les persos NON references (mineurs) : garde un ratio correct
# (majorite commune). Pas de mythic/legendary aleatoire -> reserves a l'importance.
_RARITY_SPREAD = {"common": 58, "rare": 30, "epic": 12}

# Rarete par IMPORTANCE dans la serie. Cle = mots distinctifs (minuscule) :
# match si TOUS les mots de la cle sont dans le nom de la carte (gere "Gendry Baratheon").
# On teste les cles les plus longues d'abord (plus specifiques).
_IMPORTANCE = {
    # --- mythic : les icones absolues ---
    "jon snow": "mythic", "daenerys": "mythic", "tyrion": "mythic",
    "cersei": "mythic", "arya": "mythic", "eddard": "mythic",
    # --- legendary : premiers roles / personnages majeurs ---
    "jaime": "legendary", "sansa": "legendary", "bran stark": "legendary",
    "robb": "legendary", "joffrey": "legendary", "drogo": "legendary",
    "tywin": "legendary", "petyr baelish": "legendary", "varys": "legendary",
    "brienne": "legendary", "sandor clegane": "legendary", "theon": "legendary",
    "margaery": "legendary", "stannis": "legendary", "melisandre": "legendary",
    "samwell": "legendary", "bronn": "legendary", "davos": "legendary",
    "jorah": "legendary", "ramsay": "legendary", "night king": "legendary",
    "catelyn": "legendary", "robert baratheon": "legendary", "gregor clegane": "legendary",
    "drogon": "legendary",
    # --- epic : personnages recurrents notables ---
    "tormund": "epic", "gendry": "epic", "missandei": "epic",
    "grey worm": "epic", "ygritte": "epic", "podrick": "epic", "jaqen": "epic",
    "daario": "epic", "olenna": "epic", "roose bolton": "epic",
    "ellaria": "epic", "oberyn": "epic", "gilly": "epic", "shae": "epic",
    "hodor": "epic", "yara": "epic", "euron": "epic", "tommen": "epic",
    "myrcella": "epic", "viserys": "epic", "walder frey": "epic",
    "beric": "epic", "qyburn": "epic", "high sparrow": "epic", "rickon": "epic",
    "lyanna mormont": "epic", "meera": "epic", "jojen": "epic",
    "shireen": "epic", "loras": "epic", "renly": "epic",
    "barristan": "epic", "pycelle": "epic", "maester aemon": "epic", "aemon": "epic",
    "osha": "epic", "talisa": "epic", "alliser": "epic",
    "mance rayder": "epic", "rhaegal": "epic", "viserion": "epic",
    "lancel": "epic", "kevan": "epic", "selyse": "epic",
}
# pre-trie : cles a plusieurs mots d'abord (plus specifiques)
_IMPORTANCE_KEYS = sorted(_IMPORTANCE.keys(), key=lambda k: -len(k.split()))


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


def _rarity_for(name, forced=None):
    """Rarete d'un perso : forcee > importance (serie) > aleatoire ponderee.
    Match importance = tous les mots de la cle presents dans le nom."""
    if forced:
        return forced
    words = set(re.findall(r"[a-z']+", (name or "").lower()))
    for k in _IMPORTANCE_KEYS:
        if set(k.split()) <= words:
            return _IMPORTANCE[k]
    return _pick_rarity()


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


def _rebake(args):
    """Re-genere le render (overlay selon la rarete ACTUELLE) de toutes les cartes GoT.
    Source = source_image_url (sinon image_url si encore distante)."""
    from database import get_db
    from services.cards_overlay import composite_card
    pub = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT id, name, rarity, image_url, source_image_url "
                     "FROM cards WHERE subtitle = ?", (_ORIGIN,)).fetchall()
    print(f"{len(rows)} cartes d'origine {_ORIGIN}.\n")
    ok = skip = fail = 0
    for r in rows:
        src = r["source_image_url"] or r["image_url"] or ""
        if not src or "/card_renders/" in src:
            skip += 1
            continue
        if args.dry_run:
            print(f"[dry] rebake {r['name']:26} ({r['rarity']}) <- {src[:55]}")
            ok += 1
            continue
        try:
            url = composite_card(src, r["rarity"], r["id"])
            if not url:
                fail += 1
                continue
            final = (pub + url) if pub else url
            c.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final, r["id"]))
            ok += 1
            if ok % 25 == 0:
                conn.commit(); print(f"  ... {ok} rebakees")
        except Exception as e:
            print(f"! {r['name']}: {e!r}"); fail += 1
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"\nTermine. Rebakees: {ok} | Ignorees (pas de source distante): {skip} | Echecs: {fail}")


def _reassign(args):
    """Re-affecte la rarete de toutes les cartes d'origine Game of Thrones :
    importance (serie) pour les persos connus, spread aleatoire faible pour le reste."""
    from database import get_db
    conn = get_db(); c = conn.cursor()
    rows = c.execute("SELECT id, name, rarity FROM cards WHERE subtitle = ?",
                     (_ORIGIN,)).fetchall()
    print(f"{len(rows)} cartes d'origine {_ORIGIN}.\n")
    counts = {}
    for r in rows:
        new_r = _rarity_for(r["name"], args.rarity)
        counts[new_r] = counts.get(new_r, 0) + 1
        tag = "IMPORTANCE" if (r["name"] or "").strip().lower() in _IMPORTANCE else ""
        if args.dry_run:
            print(f"[dry] {r['name']:28} {r['rarity']:9} -> {new_r:9} {tag}")
        else:
            c.execute("UPDATE cards SET rarity = ? WHERE id = ?", (new_r, r["id"]))
    if not args.dry_run:
        conn.commit()
    conn.close()
    print("\nRepartition :", ", ".join(f"{k}={v}" for k, v in
          sorted(counts.items(), key=lambda x: -x[1])))
    print("Termine." if not args.dry_run else "Dry-run : rien ecrit.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rarity", choices=list(_RARITY_SPREAD.keys()),
                    help="Force une rarete (sinon ponderee)")
    ap.add_argument("--limit", type=int, default=0, help="Limite (test)")
    ap.add_argument("--dry-run", action="store_true", help="N'ecrit rien")
    ap.add_argument("--reassign", action="store_true",
                    help="Ne pas importer : re-affecte la rarete des cartes GoT existantes selon l'importance")
    ap.add_argument("--rebake", action="store_true",
                    help="Ne pas importer : re-genere les renders GoT (overlay selon rarete actuelle)")
    args = ap.parse_args()

    if args.reassign:
        _reassign(args)
        return
    if args.rebake:
        _rebake(args)
        return

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
            rarity = _rarity_for(name, args.rarity)
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
