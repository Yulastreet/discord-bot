"""Recuperation post-bake : restaure les ORIGINAUX (transparence/alpha) depuis un
backup DB pre-bake, et re-bake proprement les renders cassés.

Contexte : un bake a re-genere des renders depuis l'original (center-crop) +
aplati les PNG transparents (alpha perdu). Ce script :
  1. lit un backup DB pre-bake pour retrouver la VRAIE URL d'origine de chaque carte
  2. re-telecharge l'original en alpha preservee (WebP lossless) -> source locale
  3. re-bake le render (lossless) UNIQUEMENT si aucun render .png n'a survecu
     (les renders survivants = cadrages manuels intacts, on n'y touche pas)

Les cadrages manuels dont le .png a ete supprime sont IRRECUPERABLES (pixels perdus,
pas de backup fichier) : ce script restaure leur original pour pouvoir re-cropper.

Usage (depuis ~/discord-bot) :
    nohup python3 -u scripts/recover_card_images.py \
        --backup bot_database.db.bak.2026-06-08 > recover.log 2>&1 &
    tail -f recover.log
    # apercu sans rien ecrire :
    python3 scripts/recover_card_images.py --backup bot_database.db.bak.2026-06-08 --dry-run
"""
import argparse
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

try:
    from dotenv import load_dotenv
    _env = os.path.join(_ROOT, ".env.dev") if os.path.exists(os.path.join(_ROOT, ".env.dev")) \
        else os.path.join(_ROOT, ".env")
    load_dotenv(_env)
except Exception:
    pass

from database import get_db                                  # noqa: E402
from services.cards_overlay import localize_source, composite_card  # noqa: E402

_RENDERS_DIR = os.path.join(_ROOT, "static", "card_renders")


def _true_original(bimg, bsrc):
    """URL de l'original NON croppe d'apres le backup."""
    if bsrc:
        return bsrc
    if bimg and "/card_renders/" not in bimg:
        return bimg   # image_url externe = l'original
    return None       # n'avait qu'un render local (crop) -> original inconnu


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", help="Chemin d'un backup DB pre-bake (.db)")
    ap.add_argument("--export", help="Chemin d'un export JSON pre-bake (cards_export.json)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not args.backup and not args.export:
        print("Fournis --export cards_export.json (recommande) ou --backup <db>"); sys.exit(1)

    pub = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

    # 1. Map id -> (image_url, source_image_url) pre-bake (export JSON ou DB)
    backup = {}
    if args.export:
        import json
        epath = args.export if os.path.isabs(args.export) else os.path.join(_ROOT, args.export)
        if not os.path.exists(epath):
            print(f"Export introuvable : {epath}"); sys.exit(1)
        data = json.load(open(epath, encoding="utf-8"))
        rows = (data.get("tables", {}) or {}).get("cards") or data.get("cards") or []
        for r in rows:
            backup[r["id"]] = (r.get("image_url"), r.get("source_image_url"))
    else:
        bpath = args.backup if os.path.isabs(args.backup) else os.path.join(_ROOT, args.backup)
        if not os.path.exists(bpath):
            print(f"Backup introuvable : {bpath}"); sys.exit(1)
        bc = sqlite3.connect(bpath); bc.row_factory = sqlite3.Row
        for r in bc.execute("SELECT id, image_url, source_image_url FROM cards"):
            backup[r["id"]] = (r["image_url"], r["source_image_url"])
        bc.close()
    print(f"Source pre-bake : {len(backup)} cartes.")

    # 2. Cartes actuelles
    conn = get_db(); c = conn.cursor()
    cards = c.execute("SELECT id, rarity FROM cards ORDER BY id").fetchall()
    conn.close()
    if args.limit:
        cards = cards[:args.limit]

    n_src = n_render = n_keep = n_noorig = 0
    for i, row in enumerate(cards, 1):
        cid = row["id"]; rarity = row["rarity"] or "common"
        bimg, bsrc = backup.get(cid, (None, None))
        orig = _true_original(bimg, bsrc)
        png_survived = os.path.exists(os.path.join(_RENDERS_DIR, f"{cid}.png"))

        if not orig:
            n_noorig += 1
            continue
        if args.dry_run:
            act = "keep-render" if png_survived else "re-bake"
            print(f"[dry] #{cid} orig={orig[:60]} -> {act}")
            continue

        # a. ré-héberge l'original en alpha (re-telecharge)
        rel = localize_source(cid, orig)
        if not rel:
            print(f"! #{cid} original injoignable : {orig[:70]}")
            continue
        src_local = (pub + rel) if pub else rel
        n_src += 1

        # b. render : on garde le .png survivant (cadrage manuel), sinon re-bake lossless
        new_img = None
        if not png_survived:
            url = composite_card(rel, rarity, cid)
            if url:
                new_img = (pub + url) if pub else url
                n_render += 1
        else:
            n_keep += 1

        conn = get_db(); c = conn.cursor()
        if new_img:
            c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                       (new_img, src_local, cid))
        else:
            c.execute("UPDATE cards SET source_image_url = ? WHERE id = ?", (src_local, cid))
        conn.commit(); conn.close()

        if i % 200 == 0:
            print(f"progress {i}/{len(cards)} | sources={n_src} re-bake={n_render} "
                  f"keep={n_keep} sans-orig={n_noorig}")

    print(f"\nFini. originaux re-heberges={n_src} | renders re-bakes={n_render} | "
          f"renders gardes={n_keep} | sans original recuperable={n_noorig}")
    if n_noorig:
        print(f"-> {n_noorig} cartes sans original dans le backup : a re-sourcer a la main.")


if __name__ == "__main__":
    main()
