"""Supprime les renders .webp PERIMES qui masquent un .png recadre sur Discord.

Contexte : la bake enregistre en .webp, le recadrage en .png. La resolution
d'image charge .webp AVANT .png -> apres un recadrage, l'ancien .webp non recadre
prenait le dessus. On supprime donc le <id>.webp UNIQUEMENT quand l'image
canonique de la carte (cards.image_url) pointe sur <id>.png.

NE SUPPRIME JAMAIS :
- les .png (tes images recadrees),
- les .webp qui SONT l'image canonique (cartes baked / secretes animees).

Usage (depuis ~/discord-bot) :
    python3 scripts/purge_stale_webp.py --dry-run   # apercu, ne supprime rien
    python3 scripts/purge_stale_webp.py             # applique
"""
import argparse
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from database import get_db  # noqa: E402

_RENDERS = os.path.join(_ROOT, "static", "card_renders")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="N'efface rien, montre seulement")
    args = ap.parse_args()

    conn = get_db(); c = conn.cursor()
    rows = c.execute(
        "SELECT id, image_url FROM cards "
        "WHERE image_url LIKE '%/card_renders/%.png'").fetchall()
    conn.close()

    removed = 0
    skipped_no_webp = 0
    checked = 0
    for r in rows:
        cid = r["id"]
        img = r["image_url"] or ""
        # securite : l'image canonique doit bien etre le .png de CETTE carte
        if not re.search(rf"/card_renders/{cid}\.png(\?|$)", img):
            continue
        checked += 1
        webp = os.path.join(_RENDERS, f"{cid}.webp")
        png = os.path.join(_RENDERS, f"{cid}.png")
        if not os.path.exists(webp):
            skipped_no_webp += 1
            continue
        if not os.path.exists(png):
            # pas de .png sur le disque -> on NE touche pas (eviter de tout casser)
            continue
        if args.dry_run:
            print(f"[dry] supprimerait {cid}.webp (canonique = {cid}.png)")
        else:
            try:
                os.remove(webp)
                print(f"supprime {cid}.webp")
            except Exception as e:
                print(f"echec {cid}.webp : {e}")
                continue
        removed += 1

    print(f"\n{'[dry] ' if args.dry_run else ''}cartes png canoniques verifiees: {checked} | "
          f".webp perimes {'a supprimer' if args.dry_run else 'supprimes'}: {removed} | "
          f"deja propres (pas de webp): {skipped_no_webp}")


if __name__ == "__main__":
    main()
