"""Pre-remplit le compteur de rolls a vie (card_roll_total) pour les joueurs
existants, en estimant a partir de leur collection.

Estimation par joueur :
    rolls ~= nb de cartes possedees (1 row = 1 copie obtenue au roll)
             + copies CONSOMMEES par fusion (elles aussi rollees).
Pour une carte fusionnee a N etoiles, copies consommees = N*(N+1)/2
(le fuse retire (cost-1) copies par etoile, cost = etoile+1 -> retrait = etoile).

Par defaut : ne touche QUE les joueurs sans total (ou total 0), pour ne pas
ecraser les rolls deja comptes depuis le deploiement. --force ecrase tout.

Usage (depuis ~/discord-bot) :
    python3 scripts/backfill_roll_totals.py --dry-run
    python3 scripts/backfill_roll_totals.py
    python3 scripts/backfill_roll_totals.py --force
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from database import get_db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="N'ecrit rien, montre un apercu")
    ap.add_argument("--force", action="store_true", help="Ecrase meme les totaux existants")
    args = ap.parse_args()

    conn = get_db(); c = conn.cursor()

    counts = {r["user_id"]: int(r["n"]) for r in c.execute(
        "SELECT user_id, COUNT(*) AS n FROM user_cards GROUP BY user_id").fetchall()}

    consumed = {}
    try:
        rows = c.execute("SELECT user_id, fusion_level FROM card_customizations "
                         "WHERE fusion_level > 0").fetchall()
    except Exception:
        rows = []
    for r in rows:
        lvl = int(r["fusion_level"])
        consumed[r["user_id"]] = consumed.get(r["user_id"], 0) + lvl * (lvl + 1) // 2

    c.execute('''CREATE TABLE IF NOT EXISTS card_roll_total (
        user_id TEXT PRIMARY KEY, total INTEGER DEFAULT 0)''')
    existing = {r["user_id"]: int(r["total"]) for r in c.execute(
        "SELECT user_id, total FROM card_roll_total").fetchall()}

    users = set(counts) | set(consumed)
    updated = skipped = 0
    preview = []
    for uid in users:
        est = counts.get(uid, 0) + consumed.get(uid, 0)
        if est <= 0:
            continue
        cur = existing.get(uid, 0)
        # max : ne perd jamais les rolls deja comptes, garantit au moins l'estimation.
        # --force ecrase a l'estimation meme si le total actuel est plus haut.
        new = est if args.force else max(cur, est)
        if new == cur:
            skipped += 1
            continue
        preview.append((uid, counts.get(uid, 0), consumed.get(uid, 0), new))
        if not args.dry_run:
            c.execute("INSERT INTO card_roll_total (user_id, total) VALUES (?, ?) "
                      "ON CONFLICT(user_id) DO UPDATE SET total = excluded.total",
                      (str(uid), new))
        updated += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    preview.sort(key=lambda x: -x[3])
    for uid, nc, cons, est in preview[:25]:
        print(f"{uid:20} cartes={nc:5} fusion+={cons:5} -> rolls={est}")
    if len(preview) > 25:
        print(f"... (+{len(preview) - 25} autres)")
    print(f"\n{'[dry] ' if args.dry_run else ''}Mis a jour: {updated} | Ignores (deja comptes): {skipped}")


if __name__ == "__main__":
    main()
