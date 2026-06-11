"""Re-actualise les cartes Pokémon dont l'image est morte (sans toucher les OK).

Pour chaque carte Pokémon : verifie si image_url charge. Si morte, re-derive
l'artwork officiel PokeAPI et re-bake un render LOCAL (servi par le dashboard,
donc jamais "mort"). Les cartes OK sont ignorees.

Usage (depuis ~/discord-bot) :
    python3 scripts/refetch_pokemon.py            # verifie tout, repare les mortes
    python3 scripts/refetch_pokemon.py --all      # rebake TOUT (meme les OK)
"""
import os
import re
import sys
import sqlite3
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.cards_overlay import composite_card

DB_FILE = os.getenv("DB_PATH") or "bot_database.db"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ARTWORK = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _url_ok(url: str) -> bool:
    """True si l'URL renvoie une image (200)."""
    if not url:
        return False
    # Chemin local servi en /static/
    if "/static/" in url and "http" not in url.split("/static/")[0][-6:]:
        rel = "static/" + url.split("/static/", 1)[1].split("?")[0]
        return os.path.exists(os.path.join(_ROOT, rel.replace("/", os.sep)))
    if url.startswith("http"):
        # Si c'est un render local exposé via PUBLIC_BASE_URL, teste le fichier disque
        if "/static/card_renders/" in url:
            rel = "static/" + url.split("/static/", 1)[1].split("?")[0]
            if os.path.exists(os.path.join(_ROOT, rel.replace("/", os.sep))):
                return True
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as r:
                return 200 <= r.status < 300
        except Exception:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return 200 <= r.status < 300
            except Exception:
                return False
    return False


def _extract_pid(*urls):
    """Extrait l'id Pokémon UNIQUEMENT depuis une URL artwork PokeAPI
    (pas depuis un render local /card_renders/<card_id>.png)."""
    for u in urls:
        if not u or "official-artwork" not in u:
            continue
        m = re.search(r"/(\d+)\.png", u)
        if m:
            return m.group(1)
    return None


def _pid_from_name(name):
    slug = name.lower().strip().replace(" ", "-").replace(".", "").replace("'", "")
    try:
        req = urllib.request.Request(f"https://pokeapi.co/api/v2/pokemon/{slug}",
                                     headers={"User-Agent": _UA})
        import json
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return str(data.get("id"))
    except Exception:
        return None


def _needs_bake(image_url: str) -> bool:
    """True si l'image n'est PAS un render local (donc à baker en local).
    Les URLs github/remote ne s'affichent pas dans les embeds Discord."""
    if not image_url:
        return True
    if "/static/card_renders/" in image_url:
        # render local : OK seulement si le fichier existe sur disque
        rel = "static/" + image_url.split("/static/", 1)[1].split("?")[0]
        return not os.path.exists(os.path.join(_ROOT, rel.replace("/", os.sep)))
    # Tout le reste (github raw, autre remote) -> baker en local
    return True


def main():
    force_all = "--all" in sys.argv
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, rarity, image_url, source_image_url FROM cards "
        "WHERE subtitle LIKE 'Pokémon%' OR subtitle LIKE 'Pokemon%'").fetchall()
    print(f"{len(rows)} cartes Pokémon.")
    public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    fixed = 0
    ok = 0
    failed = []
    for i, r in enumerate(rows, 1):
        if not force_all and not _needs_bake(r["image_url"]):
            ok += 1
            continue
        pid = _extract_pid(r["source_image_url"], r["image_url"]) or _pid_from_name(r["name"])
        if not pid:
            failed.append(f"{r['name']} (pid introuvable)")
            continue
        art = f"{_ARTWORK}/{pid}.png"
        url = composite_card(art, r["rarity"] or "common", r["id"])
        if url:
            final = (public_base + url) if public_base else url
            conn.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                         (final, art, r["id"]))
            conn.commit()
            fixed += 1
            if fixed % 20 == 0:
                print(f"  ... {fixed} reparees")
        else:
            failed.append(f"{r['name']} (artwork {pid} mort)")
    conn.close()
    print(f"\nTERMINE : {fixed} reparees, {ok} deja OK, {len(failed)} ratees.")
    for f in failed[:60]:
        print("  -", f)


if __name__ == "__main__":
    main()
