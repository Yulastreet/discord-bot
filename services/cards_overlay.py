"""Composite card image + rarity overlay -> PNG local servi via Flask.

Workflow :
- Download source image (Anilist CDN, Wikipedia, etc)
- Resize au format portrait standard 450x675 (ratio 2:3)
- Charge overlay rarete depuis assets/card_overlays/<rarity>.png
- Resize overlay aux memes dimensions
- Paste overlay sur source avec alpha
- Save PNG dans static/card_renders/<card_id>.png
- Retourne URL relative /static/card_renders/<card_id>.png
"""
from __future__ import annotations

import io
import os
import urllib.request
from PIL import Image

_USER_AGENT = "TookBot/1.0 (https://tookbot.click)"
_CARD_W = 450
_CARD_H = 675
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", "card_overlays")
_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "static", "card_renders")
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Originaux non-croppes heberges localement (pour re-cropper meme dans 3 ans)
_SOURCES_DIR = os.path.join(_PROJ_ROOT, "static", "card_sources")

_overlay_cache: dict[str, Image.Image] = {}


_SRC_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _existing_source_rel(card_id):
    for e in _SRC_EXTS:
        if os.path.exists(os.path.join(_SOURCES_DIR, f"{card_id}{e}")):
            return f"/static/card_sources/{card_id}{e}"
    return None


def localize_source(card_id: int, source_url: str) -> str | None:
    """Telecharge l'ORIGINAL et sauve les OCTETS BRUTS (format d'origine, ZERO
    re-encodage = qualite parfaite, alpha gardee, pas de banding). Retourne l'URL
    relative /static/card_sources/<id>.<ext>, ou None si echec."""
    if not source_url:
        return None
    # Deja l'original local de CETTE carte ? rien a faire.
    if f"/card_sources/{card_id}." in source_url:
        return _existing_source_rel(card_id)
    try:
        req = urllib.request.Request(source_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
            ct = (r.headers.get("Content-Type") or "").lower()
    except Exception as e:
        print(f"[overlay] localize dl err cid={card_id}: {e}")
        return None
    # Extension : depuis l'URL, sinon le content-type, defaut .png
    low = source_url.split("?")[0].lower()
    ext = next((e for e in _SRC_EXTS if low.endswith(e)), None)
    if not ext:
        ext = (".png" if "png" in ct else ".webp" if "webp" in ct
               else ".gif" if "gif" in ct else ".jpg")
    if ext == ".jpeg":
        ext = ".jpg"
    try:
        os.makedirs(_SOURCES_DIR, exist_ok=True)
        # purge les anciennes versions (autre ext / vieux webp ré-encodé)
        for e in _SRC_EXTS:
            old = os.path.join(_SOURCES_DIR, f"{card_id}{e}")
            if e != ext and os.path.exists(old):
                try: os.remove(old)
                except Exception: pass
        with open(os.path.join(_SOURCES_DIR, f"{card_id}{ext}"), "wb") as f:
            f.write(data)
        return f"/static/card_sources/{card_id}{ext}"
    except Exception as e:
        print(f"[overlay] localize save err cid={card_id}: {e}")
        return None


def _download_image(url: str, timeout: int = 15) -> Image.Image | None:
    if not url:
        return None
    # Chemin local servi en /static/... (ou full URL pointant sur notre domaine) :
    # lire le fichier sur le disque (evite un aller-retour HTTP et marche sans PUBLIC_BASE_URL).
    local_rel = None
    if url.startswith("/static/"):
        local_rel = url
    elif "/static/" in url and url.startswith("http"):
        local_rel = "/static/" + url.split("/static/", 1)[1]
    if local_rel:
        p = os.path.join(_PROJ_ROOT, local_rel.lstrip("/").split("?")[0].replace("/", os.sep))
        if os.path.exists(p):
            try:
                return Image.open(p).convert("RGBA")
            except Exception as e:
                print(f"[overlay] local open err {p}: {e}")
                return None
    # Skip SVG : PIL ne supporte pas. Convertir via cairosvg si dispo.
    is_svg = ".svg" in url.lower().split("?")[0]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if is_svg:
            # Tente conversion via cairosvg si module dispo
            try:
                import cairosvg
                png_bytes = cairosvg.svg2png(bytestring=data,
                                              output_width=_CARD_W,
                                              output_height=_CARD_H)
                return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
            except ImportError:
                print(f"[overlay] SVG skip (cairosvg manquant): {url}")
                return None
            except Exception as e:
                print(f"[overlay] SVG convert err {url}: {e}")
                return None
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        print(f"[overlay] download err {url}: {e}")
        return None


def _get_overlay(rarity: str) -> Image.Image | None:
    if rarity in _overlay_cache:
        return _overlay_cache[rarity]
    path = os.path.join(_ASSETS_DIR, f"{rarity}.png")
    if not os.path.exists(path):
        print(f"[overlay] missing {path}")
        return None
    img = Image.open(path).convert("RGBA")
    if img.size != (_CARD_W, _CARD_H):
        img = img.resize((_CARD_W, _CARD_H), Image.LANCZOS)
    _overlay_cache[rarity] = img
    return img


def composite_card(source_url: str, rarity: str, card_id: int) -> str | None:
    """Genere carte compositee. Retourne URL relative ou None si echec."""
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    src = _download_image(source_url)
    if src is None:
        return None
    # Resize source en cover 450x675 (preserve aspect, crop center si needed)
    sw, sh = src.size
    target_ratio = _CARD_W / _CARD_H
    src_ratio = sw / sh
    if src_ratio > target_ratio:
        # Source plus large : resize par hauteur, crop largeur
        new_h = _CARD_H
        new_w = int(sw * new_h / sh)
        src = src.resize((new_w, new_h), Image.LANCZOS)
        x0 = (new_w - _CARD_W) // 2
        src = src.crop((x0, 0, x0 + _CARD_W, _CARD_H))
    else:
        # Source plus haute ou egale : resize par largeur, crop hauteur
        new_w = _CARD_W
        new_h = int(sh * new_w / sw)
        src = src.resize((new_w, new_h), Image.LANCZOS)
        y0 = (new_h - _CARD_H) // 2
        src = src.crop((0, y0, _CARD_W, y0 + _CARD_H))

    overlay = _get_overlay(rarity)
    if overlay is None:
        # Pas d'overlay : flatten sur fond sombre (gere transparence sources)
        bg = Image.new("RGBA", (_CARD_W, _CARD_H), (26, 26, 26, 255))
        bg.paste(src, (0, 0), src)
        return _save_render(bg, card_id)

    # Composite : fond sombre opaque + src (avec alpha) + overlay au-dessus
    # Fix images avec fond transparent qui devenaient noires/buggy sans bg.
    canvas = Image.new("RGBA", (_CARD_W, _CARD_H), (26, 26, 26, 255))
    canvas.paste(src, (0, 0), src)  # 3eme arg = mask alpha de src
    canvas = Image.alpha_composite(canvas, overlay)
    return _save_render(canvas, card_id)


def _save_render(img: "Image.Image", card_id: int) -> str:
    """Sauve le render en WebP LOSSLESS (zéro perte, pas de banding sur les
    degradés de l'overlay / fonds unis). Plus lourd que lossy mais propre.
    Supprime l'ancien .png s'il existe (evite les doublons sur disque)."""
    out_path = os.path.join(_OUTPUT_DIR, f"{card_id}.webp")
    # lossless (pas de banding sur l'overlay) + method=0 = encodage rapide
    img.convert("RGB").save(out_path, "WEBP", lossless=True, method=0)
    old_png = os.path.join(_OUTPUT_DIR, f"{card_id}.png")
    if os.path.exists(old_png):
        try:
            os.remove(old_png)
        except Exception:
            pass
    return f"/static/card_renders/{card_id}.webp"


def bake_all_cards(force: bool = False, public_base_url: str | None = None,
                     workers: int = 10, progress_cb=None, host_sources: bool = True) -> dict:
    """Boucle parallelisee. ThreadPool pour download+localize+composite (I/O bound).
    Heberge l'ORIGINAL en local (re-crop perenne) ET le render. DB writes en bulk.
    Une carte est consideree faite si son image_url ET sa source pointent en local."""
    from database import get_db, card_list_all
    from concurrent.futures import ThreadPoolExecutor
    import threading

    pub = (public_base_url or "").rstrip("/")
    rows = card_list_all(limit=50000)
    stats = {"updated": 0, "skipped": 0, "failed": 0, "total": len(rows)}
    print(f"[overlay] bake_all_cards : {len(rows)} cards, force={force}, "
          f"workers={workers}, host_sources={host_sources}")

    # 1. Filter rows a baker
    # NON-DESTRUCTIF : si un render local existe deja (cadrage manuel approuve),
    # on NE le regenere PAS. On heberge seulement la source (only_source=True).
    # Le render n'est (re)genere que pour les cartes SANS render local, ou en --force.
    to_bake = []
    for r in rows:
        img = r.get("image_url") or ""
        src = r.get("source_image_url") or ""
        render_local = "/card_renders/" in img
        source_local = "/card_sources/" in src
        if not force and render_local and (source_local or not host_sources):
            stats["skipped"] += 1
            continue
        source = (r.get("source_image_url") or img)
        if not source or "/card_renders/" in source:
            stats["failed"] += 1
            continue
        only_source = render_local and not force   # render OK -> on ne touche pas l'image
        to_bake.append((r["id"], source, r.get("rarity", "common"), only_source))
    print(f"[overlay] {len(to_bake)} cards a traiter, {stats['skipped']} skipped, "
          f"{stats['failed']} sans source")

    counter = {"done": 0, "ok": 0, "fail": 0}
    counter_lock = threading.Lock()
    results = []   # (cid, render_url, source_local_url_or_None)
    total = len(to_bake)

    def _worker(item):
        cid, source, rarity, only_source = item
        source_local_url = None
        bake_from = source
        url = None
        try:
            if host_sources:
                rel_src = localize_source(cid, source)
                if rel_src:
                    source_local_url = (pub + rel_src) if pub else rel_src
                    bake_from = rel_src  # composite depuis l'original local (chemin /static)
            if not only_source:   # NE re-bake PAS un render existant (cadrage manuel)
                url = composite_card(bake_from, rarity, cid)
        except Exception as e:
            print(f"[overlay] worker err cid={cid}: {e}")
            url = None
        with counter_lock:
            counter["done"] += 1
            counter["ok" if (url or (only_source and source_local_url)) else "fail"] += 1
            if counter["done"] % 100 == 0 or counter["done"] == total:
                pct = counter["done"] * 100 // max(1, total)
                print(f"[overlay] progress {counter['done']}/{total} ({pct}%) "
                      f"ok={counter['ok']} fail={counter['fail']}")
            if progress_cb and counter["done"] % 20 == 0:
                try: progress_cb(counter["done"], total)
                except Exception: pass
        return cid, url, source_local_url, only_source

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, url, src_local, only_source in ex.map(_worker, to_bake):
            final = (pub + url) if (url and pub) else url
            results.append((cid, final, src_local, only_source))

    # Bulk DB update. only_source -> on ne touche QUE source_image_url (render preservé).
    if results:
        conn = get_db(); c = conn.cursor()
        for cid, final_url, src_local, only_source in results:
            if only_source:
                if src_local:
                    c.execute("UPDATE cards SET source_image_url = ? WHERE id = ?",
                               (src_local, cid))
            elif final_url and src_local:
                c.execute("UPDATE cards SET image_url = ?, source_image_url = ? WHERE id = ?",
                           (final_url, src_local, cid))
            elif final_url:
                c.execute("UPDATE cards SET image_url = ? WHERE id = ?", (final_url, cid))
        conn.commit(); conn.close()
    stats["updated"] = len(results)
    stats["failed"] += (len(to_bake) - len(results))
    print(f"[overlay] DONE : updated={stats['updated']} skipped={stats['skipped']} "
          f"failed={stats['failed']} total={stats['total']}")
    if progress_cb:
        try: progress_cb(len(to_bake), len(to_bake))
        except Exception: pass
    return stats
