#!/usr/bin/env python3
"""Genere les cartes vitrine (page /cartes.html + description top.gg) avec le rendu
EXACT de /show (bordures positionnees via la calibration de la DB prod).

A lancer sur le VPS :
    cd ~/discord-bot && python scripts/gen_card_showcase.py

Sortie -> static/cards_showcase/ (fichiers statiques servis, rien a commit).

Jeu SITE (carrousel) : classic1..3, border_gold, border_frost, augusta_hell,
byakuya_void, toji_alt.
Jeu TOP.GG (4 cartes) : toji_alt, goku_secret.<ext>, augusta_hell, byakuya_void.
"""
import os, sys, shutil, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db, card_get  # noqa
from services import card_render as R  # noqa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERS = os.path.join(ROOT, "static", "card_renders")
OUT = os.path.join(ROOT, "static", "cards_showcase")
os.makedirs(OUT, exist_ok=True)

# IDs fournis par l'owner
TOJI_ID = 42682      # Toji Summer -> art alternatif, PAS de bordure
GOKU_ID = 38651      # Son Goku secret -> gif anime, PAS de bordure
AUGUSTA_ID = 40078   # Augusta -> bordure ENFER
BYAKUYA_ID = 22185   # Byakuya Kuchiki -> bordure NEANT


def border_by_filename(substr):
    conn = get_db(); cur = conn.cursor()
    try:
        rows = cur.execute("SELECT * FROM borders").fetchall()
    except Exception as e:
        print("  [x] table borders:", e); rows = []
    conn.close()
    for r in rows:
        if substr in (dict(r).get("filename") or "").lower():
            return dict(r)
    return None


def save_png(img, name):
    img.convert("RGBA").save(os.path.join(OUT, name), "PNG")
    print("  ->", name)


def render_plain(cid, name):
    c = card_get(cid)
    img = R.compose_card_image(cid, border=None, fallback_url=(c or {}).get("image_url"))
    if img is None:
        print(f"  [x] plain {cid} echoue"); return
    save_png(img, name)


def render_alt(cid, name):
    c = card_get(cid)
    img = R.compose_card_image(cid, border=None, fallback_url=(c or {}).get("image_url"), alt=True)
    if img is None:
        print(f"  [x] alt {cid} echoue (pas de skin alt ?)"); return
    save_png(img, name)


def render_border(cid, border_dict, name):
    if not border_dict:
        print(f"  [x] bordure introuvable pour {name}"); return
    c = card_get(cid)
    img = R.compose_card_image(cid, border=border_dict, fallback_url=(c or {}).get("image_url"))
    if img is None:
        print(f"  [x] bordure {cid} echoue"); return
    save_png(img, name)


def render_animated(cid, out_noext):
    c = card_get(cid) or {}
    for ext in (".gif", ".webp"):
        src = os.path.join(RENDERS, f"{cid}{ext}")
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(OUT, out_noext + ext))
            print("  ->", out_noext + ext, "(anime, copie)")
            return out_noext + ext
    url = c.get("image_url") or ""
    if url.startswith("http"):
        ext = ".gif" if ".gif" in url.lower() else ".webp"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TookBot/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                open(os.path.join(OUT, out_noext + ext), "wb").write(resp.read())
            print("  ->", out_noext + ext, "(anime, telecharge)")
            return out_noext + ext
        except Exception as e:
            print("  [x] download anime:", e)
    render_plain(cid, out_noext + ".png")
    return out_noext + ".png"


# --- ids "classiques/bordures auto" pour remplir le carrousel ---
auto = []
for f in sorted(os.listdir(RENDERS)):
    if f.endswith(".png") and f[:-4].isdigit():
        auto.append(int(f[:-4]))
    if len(auto) >= 40:
        break
used = {TOJI_ID, GOKU_ID, AUGUSTA_ID, BYAKUYA_ID}
auto = [i for i in auto if i not in used]

b_hell = border_by_filename("hell")
b_void = border_by_filename("void")
b_gold = border_by_filename("gold")
b_frost = border_by_filename("frost")
print("bordures:", "hell" if b_hell else "-", "void" if b_void else "-",
      "gold" if b_gold else "-", "frost" if b_frost else "-")

print("\n== TOP.GG (4 cartes) ==")
render_alt(TOJI_ID, "toji_alt.png")
goku_file = render_animated(GOKU_ID, "goku_secret")
render_border(AUGUSTA_ID, b_hell, "augusta_hell.png")
render_border(BYAKUYA_ID, b_void, "byakuya_void.png")

print("\n== SITE (carrousel) ==")
render_plain(auto[0], "classic1.png")
render_plain(auto[1], "classic2.png")
render_plain(auto[2], "classic3.png")
render_border(auto[3], b_gold, "border_gold.png")
render_border(auto[4], b_frost, "border_frost.png")
# augusta_hell.png et byakuya_void.png (ci-dessus) servent aussi au carrousel
# toji_alt.png (ci-dessus) sert aussi au carrousel

print("\nManifest carte animee Goku :", goku_file)
print("-> si l'extension n'est pas .webp, dis-le moi pour ajuster la page/description.")
print("Termine. Verifie static/cards_showcase/")
