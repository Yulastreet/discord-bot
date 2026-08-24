"""Card image health check: artefacts (flattened .webp source) + external links.

- source brute OK  : static/card_sources/<id>.(png|jpg|jpeg|gif)  -> propre
- flattened source : static/card_sources/<id>.webp               -> ARTEFACT (re-derive)
- pas de source    : check image_url (externe = risque lien mort)
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from database import get_db  # noqa: E402

_SOURCES_DIR = os.path.join(_ROOT, "static", "card_sources")
_RENDERS_DIR = os.path.join(_ROOT, "static", "card_renders")
_RAW = (".png", ".jpg", ".jpeg", ".gif")


def _src_kind(cid):
    if os.path.exists(os.path.join(_SOURCES_DIR, f"{cid}.webp")):
        return "flat"
    if any(os.path.exists(os.path.join(_SOURCES_DIR, f"{cid}{e}")) for e in _RAW):
        return "raw"
    return "none"


def _ext_url(u):
    return (bool(u) and u.startswith("http") and "/static/" not in u
            and "/card_renders/" not in u and "/card_sources/" not in u)


def _has_render(cid, img):
    if "/card_renders/" in (img or ""):
        return True
    return (os.path.exists(os.path.join(_RENDERS_DIR, f"{cid}.webp"))
            or os.path.exists(os.path.join(_RENDERS_DIR, f"{cid}.png")))


def main():
    c = get_db().cursor()
    rows = c.execute("SELECT id,name,universe,subtitle,image_url,source_image_url "
                     "FROM cards").fetchall()
    raw = flat = none = 0
    flat_uni, linkrisk = {}, 0
    for r in rows:
        k = _src_kind(r["id"])
        if k == "raw":
            raw += 1
        elif k == "flat":
            flat += 1
            u = (r["subtitle"] or r["universe"] or "?")
            flat_uni[u] = flat_uni.get(u, 0) + 1
        else:
            none += 1
        if not _has_render(r["id"], r["image_url"]) and _ext_url(r["image_url"]):
            linkrisk += 1
    print("total cartes:", len(rows))
    print("source brute propre (png/jpg):", raw)
    print("flattened .webp source (ARTEFACT, must be re-derived):", flat)
    print("sans source locale:", none)
    print("risque lien mort (pas de render local + image externe):", linkrisk)
    print("\norigins of the artefacted ones (.webp) - top 30:")
    for k, v in sorted(flat_uni.items(), key=lambda x: -x[1])[:30]:
        print("  %5d  %s" % (v, k))


if __name__ == "__main__":
    main()
