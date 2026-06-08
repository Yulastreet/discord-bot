"""Import cards_export.json dans la DB courante (prod ou autre).

Usage :
    python scripts/cards_import.py [json_path] [db_path] [--replace|--merge]

Defaults :
    json_path = cards_export.json
    db_path   = env DB_PATH ou bot_database.db
    mode      = --replace (DROP+INSERT pour cards-tables uniquement)
                --merge   (INSERT OR IGNORE, garde existant)

ATTENTION --replace efface TOUTES les cartes/collections/trades/etc
de la DB cible. Backup d'abord :
    cp bot_database.db bot_database.db.bak

DB users/levels/etc PAS touchee : on opere SEULEMENT sur les tables
cards_*.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys


TABLES_ORDER = [
    # ordre = FK : cards d'abord, user_cards/trades apres
    "cards",
    "user_card_settings",
    "guild_card_config",
    "user_guild_roll_cooldown",
    "user_cards",
    "card_trades",
    "card_trade_items",
    "card_suggestions",
]


def insert_rows(c: sqlite3.Cursor, table: str, rows: list[dict], mode: str):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    cols_sql = ", ".join(cols)
    placeholders = ", ".join("?" * len(cols))
    verb = "INSERT OR REPLACE" if mode == "replace" else "INSERT OR IGNORE"
    sql = f"{verb} INTO {table} ({cols_sql}) VALUES ({placeholders})"
    n = 0
    for r in rows:
        try:
            c.execute(sql, [r.get(k) for k in cols])
            n += 1
        except sqlite3.IntegrityError as e:
            print(f"[import] {table} skip row : {e}")
    return n


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    json_path = args[0] if len(args) > 0 else "cards_export.json"
    db_path = args[1] if len(args) > 1 else (
        os.getenv("DB_PATH") or "bot_database.db")
    mode = "merge" if "--merge" in sys.argv else "replace"

    if not os.path.exists(json_path):
        print(f"ERR : JSON introuvable : {json_path}"); sys.exit(1)
    if not os.path.exists(db_path):
        print(f"ERR : DB introuvable : {db_path}"); sys.exit(1)

    print(f"[import] json={json_path} db={db_path} mode={mode}")

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    tables = data.get("tables") or {}

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    if mode == "replace":
        print("[import] mode REPLACE : wipe cards-tables existantes")
        # DELETE FROM dans ordre inverse FK
        for t in reversed(TABLES_ORDER):
            try:
                c.execute(f"DELETE FROM {t}")
                print(f"  wiped {t}")
            except sqlite3.OperationalError as e:
                print(f"  skip {t} ({e})")
        # Reset sequences AUTOINCREMENT pour rester sur les memes IDs
        try:
            for t in ("cards", "user_cards", "card_trades", "card_trade_items",
                       "card_suggestions"):
                c.execute(f"DELETE FROM sqlite_sequence WHERE name = ?", (t,))
        except sqlite3.OperationalError:
            pass

    for t in TABLES_ORDER:
        rows = tables.get(t) or []
        n = insert_rows(c, t, rows, mode)
        print(f"[import] {t}: {n}/{len(rows)} rows insérés")

    conn.commit(); conn.close()
    print(f"[OK] Import termine.")


if __name__ == "__main__":
    main()
