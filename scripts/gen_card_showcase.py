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
import os, sys, shutil, urllib.request, random

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


def _shrink_animated(src_path, dst_path, max_w=300):
    """Re-encode une image animee (webp/gif) en webp anime plus leger (top.gg
    refuse les gros fichiers). Retourne True si ok."""
    from PIL import Image
    try:
        im = Image.open(src_path)
        w, h = im.size
        scale = min(1.0, max_w / w)
        nw, nh = int(w * scale), int(h * scale)
        frames, durations = [], []
        try:
            i = 0
            while True:
                im.seek(i)
                durations.append(im.info.get("duration", 80))
                frames.append(im.convert("RGBA").resize((nw, nh), Image.LANCZOS))
                i += 1
        except EOFError:
            pass
        if not frames:
            return False
        frames[0].save(dst_path, "WEBP", save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, quality=66, method=6)
        return True
    except Exception as e:
        print("  [x] shrink anime:", e)
        return False


def render_animated(cid, out_noext, max_w=300):
    c = card_get(cid) or {}
    dst = os.path.join(OUT, out_noext + ".webp")
    # source locale (render) puis distante (image_url)
    src = None
    for ext in (".webp", ".gif"):
        p = os.path.join(RENDERS, f"{cid}{ext}")
        if os.path.exists(p):
            src = p; break
    tmp = None
    if src is None:
        url = c.get("image_url") or ""
        if url.startswith("http"):
            ext = ".gif" if ".gif" in url.lower() else ".webp"
            tmp = os.path.join(OUT, "_tmp_anim" + ext)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "TookBot/1.0"})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    open(tmp, "wb").write(resp.read())
                src = tmp
            except Exception as e:
                print("  [x] download anime:", e)
    if src and _shrink_animated(src, dst, max_w=max_w):
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        kb = os.path.getsize(dst) // 1024
        print(f"  -> {out_noext}.webp (anime compresse, {kb} KB)")
        return out_noext + ".webp"
    # fallback : rendu statique
    render_plain(cid, out_noext + ".png")
    return out_noext + ".png"


# --- pool de cartes pour le carrousel (aleatoire) ---
auto = [int(f[:-4]) for f in os.listdir(RENDERS)
        if f.endswith(".png") and f[:-4].isdigit()]
used = {TOJI_ID, GOKU_ID, AUGUSTA_ID, BYAKUYA_ID}
auto = [i for i in auto if i not in used]
random.shuffle(auto)

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

# === SITE : carrousel de cartes ALEATOIRES, dont ~40% avec une bordure aleatoire ===
SHOWCASE_COUNT = 24        # nb de cartes qui defilent (la page reference show1..showN)
BORDER_CHANCE = 0.4        # proba qu'une carte porte une bordure
borders_pool = [b for b in (b_gold, b_frost, b_hell, b_void) if b]

print(f"\n== SITE (carrousel {SHOWCASE_COUNT} cartes aleatoires) ==")
sel = auto[:SHOWCASE_COUNT]
# nettoie les anciens show*.png / classic*.png / border_*.png
for old in os.listdir(OUT):
    if old.startswith(("show", "classic", "border_", "summer_")):
        try: os.remove(os.path.join(OUT, old))
        except Exception: pass
n_border = 0
for i, cid in enumerate(sel, 1):
    if borders_pool and random.random() < BORDER_CHANCE:
        render_border(cid, random.choice(borders_pool), f"show{i}.png"); n_border += 1
    else:
        render_plain(cid, f"show{i}.png")
print(f"  {SHOWCASE_COUNT} cartes, dont {n_border} avec bordure.")

print("\nManifest carte animee Goku :", goku_file)
print("-> si l'extension n'est pas .webp, dis-le moi pour ajuster la description top.gg.")
print(f"Carrousel site : show1..show{SHOWCASE_COUNT}.png")
print("Termine. Verifie static/cards_showcase/")
