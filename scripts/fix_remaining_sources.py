"""Finit les cartes sans original brut : re-localise depuis leur source externe
actuelle (source_image_url ou image_url) + re-bake lossless. Pour les stragglers
post-export qui ont encore un lien externe vivant."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass

from database import get_db  # noqa: E402
from services.cards_overlay import localize_source, composite_card  # noqa: E402

_SOURCES_DIR = os.path.join(_ROOT, "static", "card_sources")
_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _has_raw(cid):
    return any(os.path.exists(os.path.join(_SOURCES_DIR, f"{cid}{e}")) for e in _EXTS)


def _ext(u):
    return (bool(u) and u.startswith("http")
            and "/static/" not in u and "/card_renders/" not in u and "/card_sources/" not in u)


def main():
    dry = "--dry-run" in sys.argv
    pub = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    c = get_db().cursor()
    rows = c.execute("SELECT id,name,rarity,image_url,source_image_url FROM cards").fetchall()
    todo = []
    for r in rows:
        if _has_raw(r["id"]):
            continue
        orig = r["source_image_url"] if _ext(r["source_image_url"]) else \
               (r["image_url"] if _ext(r["image_url"]) else None)
        todo.append((r["id"], r["name"], r["rarity"] or "common", orig))

    print(f"{len(todo)} cartes sans original brut.")
    fixed = dead = 0
    for cid, name, rarity, orig in todo:
        if not orig:
            print(f"  MORTE #{cid} {name} (aucune source externe) -> a re-sourcer a la main")
            dead += 1
            continue
        if dry:
            print(f"  [dry] #{cid} {name} <- {orig[:70]}")
            continue
        rel = localize_source(cid, orig)
        if not rel:
            print(f"  ECHEC #{cid} {name} (source injoignable: {orig[:60]})")
            dead += 1
            continue
        url = composite_card(rel, rarity, cid)
        conn = get_db(); cc = conn.cursor()
        if url:
            final = (pub + url) if pub else url
            cc.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                       (final, (pub + rel) if pub else rel, cid))
        else:
            cc.execute("UPDATE cards SET source_image_url = ? WHERE id = ?",
                       ((pub + rel) if pub else rel, cid))
        conn.commit(); conn.close()
        print(f"  OK #{cid} {name}")
        fixed += 1

    print(f"\nFini. reparees={fixed} | mortes/sans-source={dead}")


if __name__ == "__main__":
    main()
