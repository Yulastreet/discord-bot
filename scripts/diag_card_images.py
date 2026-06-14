"""Diagnostic : etat des images de cartes (recuperable direct vs original perdu)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db  # noqa: E402


def _ext(u):
    return (bool(u) and u.startswith("http")
            and "/static/" not in u and "/card_renders/" not in u
            and "/card_sources/" not in u)


def main():
    c = get_db().cursor()
    rows = c.execute("SELECT id,name,universe,subtitle,image_url,source_image_url "
                     "FROM cards").fetchall()
    src_ext = img_ext = both_local = 0
    lost = {}
    for r in rows:
        se = _ext(r["source_image_url"]); ie = _ext(r["image_url"])
        if se:
            src_ext += 1
        if ie:
            img_ext += 1
        if not se and not ie:
            both_local += 1
            k = (r["subtitle"] or r["universe"] or "?")
            lost[k] = lost.get(k, 0) + 1
    print("total cartes:", len(rows))
    print("source_image_url externe (recuperable direct):", src_ext)
    print("image_url externe (recuperable direct):", img_ext)
    print("TOUT local (original perdu, a re-deriver):", both_local)
    print("\norigines des perdues (top 25):")
    for k, v in sorted(lost.items(), key=lambda x: -x[1])[:25]:
        print("  %5d  %s" % (v, k))


if __name__ == "__main__":
    main()
