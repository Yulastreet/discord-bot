"""Export tables cartes vers cards_export.json pour migration dev -> prod.

Usage :
    python scripts/cards_export.py [db_path]

Default db_path = bot_database_dev.db (env DB_PATH override).
Output : cards_export.json a la racine du repo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys


TABLES = [
    "cards",
    "user_cards",
    "card_trades",
    "card_trade_items",
    "user_guild_roll_cooldown",
    "guild_card_config",
    "card_suggestions",
    "user_card_settings",
]


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else (
        os.getenv("DB_PATH") or "bot_database_dev.db")
    if not os.path.exists(db_path):
        print(f"ERR : DB introuvable : {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    out = {"version": 1, "source_db": db_path, "tables": {}}
    for t in TABLES:
        try:
            rows = c.execute(f"SELECT * FROM {t}").fetchall()
            out["tables"][t] = [dict(r) for r in rows]
            print(f"[export] {t}: {len(rows)} rows")
        except sqlite3.OperationalError as e:
            print(f"[export] {t} skip ({e})")
            out["tables"][t] = []
    conn.close()

    out_path = "cards_export.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"[OK] {out_path} ecrit ({size_mb:.2f} Mo)")


if __name__ == "__main__":
    main()
