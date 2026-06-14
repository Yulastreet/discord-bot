"""Diagnostic : cartes SANS original brut recupere (= risque d'artefact, a re-deriver).

Une carte est 'OK' si elle a un fichier brut static/card_sources/<id>.<ext>
(recupere proprement). Sinon = pas d'original propre -> potentiellement artefactee.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from database import get_db  # noqa: E402

_SOURCES_DIR = os.path.join(_ROOT, "static", "card_sources")
_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _has_raw(cid):
    return any(os.path.exists(os.path.join(_SOURCES_DIR, f"{cid}{e}")) for e in _EXTS)


def main():
    c = get_db().cursor()
    rows = c.execute("SELECT id,name,universe,subtitle FROM cards").fetchall()
    ok = no_raw = 0
    by_uni = {}
    for r in rows:
        if _has_raw(r["id"]):
            ok += 1
        else:
            no_raw += 1
            k = (r["subtitle"] or r["universe"] or "?")
            by_uni[k] = by_uni.get(k, 0) + 1
    print("total cartes:", len(rows))
    print("avec original brut recupere (propres):", ok)
    print("SANS original brut (a re-deriver depuis la source):", no_raw)
    print("\norigines des cartes a re-deriver (top 30):")
    for k, v in sorted(by_uni.items(), key=lambda x: -x[1])[:30]:
        print("  %5d  %s" % (v, k))


if __name__ == "__main__":
    main()
