#!/usr/bin/env python3
"""Genere les cartes vitrine de la page /cartes.html avec le rendu EXACT de /show
(bordures positionnees via la calibration de la DB prod). A lancer sur le VPS :

    cd ~/discord-bot && python scripts/gen_card_showcase.py

Sortie : static/cards_showcase/{card1..card4}.(png|gif)
Rien a commit : ce sont des fichiers statiques servis directement.

Si un nom ne resout pas la bonne carte, mets l'ID en dur dans OVERRIDE ci-dessous
(tu trouves l'ID via /card <nom> sur Discord ou sur le dashboard)."""
import os, sys, shutil, sqlite3, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db, card_get, card_get_by_name, border_get, event_skin_owned_set  # noqa
from services import card_render as R

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "cards_showcase")
os.makedirs(OUT, exist_ok=True)

# ---- forcer un ID si la resolution par nom se trompe (0 = auto par nom) ----
OVERRIDE = {
    "toji":    0,   # Toji Summer (art alternatif event)
    "goku":    0,   # Goku secret (carte animee / gif)
    "augusta": 0,   # Augusta (Wuthering Waves) + bordure enfer
    "byakuya": 0,   # Byakuya Kuchiki (Bleach) + bordure neant
}


def find_card(key, *names, rarity=None, universe_like=None):
    if OVERRIDE.get(key):
        c = card_get(OVERRIDE[key]);
        if c: return c
    conn = get_db(); cur = conn.cursor()
    for nm in names:
        q = "SELECT * FROM cards WHERE name LIKE ?"
        p = [f"%{nm}%"]
        if rarity:
            q += " AND rarity = ?"; p.append(rarity)
        if universe_like:
            q += " AND (subtitle LIKE ? OR universe LIKE ?)"; p += [f"%{universe_like}%", f"%{universe_like}%"]
        rows = cur.execute(q + " ORDER BY id LIMIT 5", p).fetchall()
        if rows:
            conn.close()
            if len(rows) > 1:
                print(f"  [!] plusieurs '{nm}' : " + ", ".join(f"{r['id']}={r['name']}({r['rarity']})" for r in rows))
            return dict(rows[0])
    conn.close()
    return None


def border_by_filename(substr):
    """Retourne le dict bordure dont le fichier contient substr (void/hell...)."""
    conn = get_db(); cur = conn.cursor()
    try:
        rows = cur.execute("SELECT * FROM borders").fetchall()
    except Exception:
        rows = []
    conn.close()
    for r in rows:
        d = dict(r)
        if substr in (d.get("filename") or "").lower():
            return d
    return None


def save_png(img, name):
    p = os.path.join(OUT, name)
    img.convert("RGBA").save(p, "PNG")
    print("  ->", name)


def gen_bordered(card, border_dict, out_name):
    img = R.compose_card_image(card["id"], border=border_dict,
                               fallback_url=card.get("image_url"))
    if img is None:
        print(f"  [x] rendu echoue pour {card['name']}"); return
    save_png(img, out_name)


def gen_alt(card, out_name):
    img = R.compose_card_image(card["id"], border=None, fallback_url=card.get("image_url"), alt=True)
    if img is None:
        print(f"  [x] rendu alt echoue pour {card['name']}"); return
    save_png(img, out_name)


def gen_animated(card, out_name_noext):
    """Carte secrete animee : copie le fichier anime tel quel (webp/gif) pour garder l'anim."""
    rid = card["id"]
    root = os.path.dirname(OUT)  # static/
    for ext in (".gif", ".webp"):
        src = os.path.join(root, "card_renders", f"{rid}{ext}")
        if os.path.exists(src):
            dst = os.path.join(OUT, out_name_noext + ext)
            shutil.copyfile(src, dst)
            print("  ->", os.path.basename(dst), "(anime, copie)")
            return out_name_noext + ext
    # sinon telecharge la source (image_url) si c'est un gif/webp anime
    url = card.get("image_url") or ""
    if url.startswith("http"):
        ext = ".gif" if ".gif" in url.lower() else ".webp"
        dst = os.path.join(OUT, out_name_noext + ext)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TookBot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                open(dst, "wb").write(resp.read())
            print("  ->", os.path.basename(dst), "(anime, telecharge)")
            return out_name_noext + ext
        except Exception as e:
            print(f"  [x] download anime echoue: {e}")
    # fallback : rendu statique
    img = R.compose_card_image(rid, border=None, fallback_url=url)
    if img:
        save_png(img, out_name_noext + ".png")
        return out_name_noext + ".png"
    return None


print("Resolution des cartes...")
toji = find_card("toji", "Toji Summer", "Toji", "Touji Fushiguro")
goku = find_card("goku", "Goku", "Son Goku", "Gokuu", rarity="secret") or find_card("goku", "Goku", "Son Goku")
augusta = find_card("augusta", "Augusta", universe_like="Wuthering") or find_card("augusta", "Augusta")
byakuya = find_card("byakuya", "Byakuya Kuchiki", "Byakuya")

for lbl, c in [("Toji", toji), ("Goku", goku), ("Augusta", augusta), ("Byakuya", byakuya)]:
    print(f"  {lbl}: " + (f"#{c['id']} {c['name']} ({c['rarity']}) [{c.get('subtitle')}]" if c else "INTROUVABLE"))

b_hell = border_by_filename("hell")
b_void = border_by_filename("void")
print("  bordure enfer:", b_hell["border_key"] if b_hell else "INTROUVABLE",
      "| bordure neant:", b_void["border_key"] if b_void else "INTROUVABLE")

print("\nGeneration...")
manifest = {}
if toji:    gen_alt(toji, "card1_toji_alt.png");                 manifest["card1"] = "card1_toji_alt.png"
if goku:    manifest["card2"] = gen_animated(goku, "card2_goku_secret")
if augusta and b_hell: gen_bordered(augusta, b_hell, "card3_augusta_hell.png"); manifest["card3"] = "card3_augusta_hell.png"
if byakuya and b_void: gen_bordered(byakuya, b_void, "card4_byakuya_void.png"); manifest["card4"] = "card4_byakuya_void.png"

print("\nManifest (mets ces noms de fichiers dans cartes.html):")
for k, v in manifest.items():
    print(f"  {k}: {v}")
print("\nTermine. Verifie les images dans static/cards_showcase/")
