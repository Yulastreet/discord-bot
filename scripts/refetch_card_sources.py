"""Re-fetch les sources d'images perdues pour les cartes d'un jeu Fandom, puis rebake.

Usage (depuis ~/discord-bot) :
    python3 scripts/refetch_card_sources.py finalfantasy
    python3 scripts/refetch_card_sources.py <game_key>

game_key = une cle du dict GAMES dans services/cards_fandom_games.py.
Selectionne les cartes de l'univers correspondant dont la source est perdue
(source_image_url NULL ou deja locale), refetch la pageimage Fandom par nom,
set source_image_url, puis recompose l'overlay (rebake).
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.cards_fandom_games import GAMES, _fetch_pageimages
from services.cards_overlay import composite_card, localize_source

DB_FILE = os.getenv("DB_PATH") or "bot_database.db"


def _is_broken(src):
    """Source a re-deriver : vide, ou pointant sur un fichier LOCAL (render, crop,
    or a source flattened by the old bake) -> we want the real Fandom original."""
    if not src:
        return True
    return ("/card_renders/" in src or "/card_suggestions/" in src
            or "/card_sources/" in src or "/static/" in src)


def main():
    game_key = sys.argv[1] if len(sys.argv) > 1 else "finalfantasy"
    cfg = GAMES.get(game_key)
    if not cfg:
        print(f"Jeu inconnu : {game_key}. Dispo : {list(GAMES.keys())}")
        return
    sub = cfg["sub"]
    # Label a matcher : argv[2] override, sinon le nom du jeu (stocke en subtitle a l'import)
    label = sys.argv[2] if len(sys.argv) > 2 else cfg["name"]
    print(f"Jeu={game_key} sub={sub} label='{label}' (match subtitle ou universe)")

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, rarity, source_image_url, image_url FROM cards "
        "WHERE subtitle = ? OR universe = ?",
        (label, label)).fetchall()
    broken = [r for r in rows if _is_broken(r["source_image_url"])]
    print(f"{len(rows)} cartes '{label}', {len(broken)} a reparer.")
    if not broken:
        conn.close()
        return

    names = [r["name"] for r in broken]
    # Fetch pageimages par batches (gere via _fetch_pageimages)
    imgmap = _fetch_pageimages(sub, names)
    lower = {k.lower(): v for k, v in imgmap.items()}
    print(f"{len(imgmap)} images trouvees sur Fandom.")

    public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    fixed = 0
    missed = []
    for r in broken:
        thumb = lower.get(r["name"].lower())
        if not thumb:
            missed.append(r["name"])
            continue
        # Heberge l'original BRUT (re-crop perenne + qualite parfaite)
        rel = localize_source(r["id"], thumb)
        src_for_bake = rel or thumb
        src_db = ((public_base + rel) if (rel and public_base) else rel) or thumb
        # Render lossless depuis l'original brut
        url = composite_card(src_for_bake, r["rarity"] or "common", r["id"])
        if url:
            final = (public_base + url) if public_base else url
            conn.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                         (final, src_db, r["id"]))
            conn.commit()
            fixed += 1
            if fixed % 20 == 0:
                print(f"  ... {fixed} reparees")
        else:
            missed.append(r["name"] + " (composite fail)")
    conn.close()
    print(f"\nTERMINE : {fixed}/{len(broken)} reparees, {len(missed)} ratees.")
    if missed:
        print("Ratees (nom Fandom different ou image absente) :")
        for m in missed[:60]:
            print("  -", m)


if __name__ == "__main__":
    main()
