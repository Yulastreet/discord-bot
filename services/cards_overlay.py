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

_overlay_cache: dict[str, Image.Image] = {}


def _download_image(url: str, timeout: int = 15) -> Image.Image | None:
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
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
        # Pas d'overlay : save juste resized
        out_path = os.path.join(_OUTPUT_DIR, f"{card_id}.png")
        src.save(out_path, "PNG", optimize=True)
        return f"/static/card_renders/{card_id}.png"

    # Composite : src en bas, overlay au-dessus avec alpha
    canvas = Image.new("RGBA", (_CARD_W, _CARD_H), (0, 0, 0, 0))
    canvas.paste(src, (0, 0))
    canvas = Image.alpha_composite(canvas, overlay)

    out_path = os.path.join(_OUTPUT_DIR, f"{card_id}.png")
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return f"/static/card_renders/{card_id}.png"


def bake_all_cards(force: bool = False, public_base_url: str | None = None,
                     workers: int = 10) -> dict:
    """Boucle parallelisee. ThreadPool pour download+composite (I/O bound).
    DB writes regroupes en bulk a la fin pour eviter contention SQLite."""
    from database import get_db, card_list_all
    from concurrent.futures import ThreadPoolExecutor
    import threading

    rows = card_list_all(limit=50000)
    stats = {"updated": 0, "skipped": 0, "failed": 0, "total": len(rows)}
    print(f"[overlay] bake_all_cards : {len(rows)} cards, force={force}, workers={workers}")

    # 1. Filter rows a baker
    to_bake = []
    for r in rows:
        img = r.get("image_url") or ""
        src = r.get("source_image_url")
        already = "/static/card_renders/" in img or "/card_renders/" in img
        if not force and already:
            stats["skipped"] += 1
            continue
        source = src or img
        if not source or "/card_renders/" in source:
            stats["failed"] += 1
            continue
        to_bake.append((r["id"], source, r.get("rarity", "common"), src is not None))
    print(f"[overlay] {len(to_bake)} cards a baker, {stats['skipped']} skipped, "
          f"{stats['failed']} sans source")

    # 2. Save source_image_url pour ceux qui n'en ont pas encore (one-time)
    needs_src = [(cid, source) for cid, source, _, had_src in to_bake if not had_src]
    if needs_src:
        conn = get_db(); c = conn.cursor()
        for cid, source in needs_src:
            c.execute("UPDATE cards SET source_image_url = ? WHERE id = ?",
                       (source, cid))
        conn.commit(); conn.close()
        print(f"[overlay] sauve source_image_url pour {len(needs_src)} cards")

    # 3. Parallel composite
    counter = {"done": 0, "ok": 0, "fail": 0}
    counter_lock = threading.Lock()
    results = []
    total = len(to_bake)

    def _worker(item):
        cid, source, rarity, _ = item
        try:
            url = composite_card(source, rarity, cid)
        except Exception as e:
            print(f"[overlay] worker err cid={cid}: {e}")
            url = None
        with counter_lock:
            counter["done"] += 1
            if url:
                counter["ok"] += 1
            else:
                counter["fail"] += 1
            if counter["done"] % 100 == 0 or counter["done"] == total:
                pct = counter["done"] * 100 // max(1, total)
                print(f"[overlay] progress {counter['done']}/{total} ({pct}%) "
                      f"ok={counter['ok']} fail={counter['fail']}")
        return cid, url

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, url in ex.map(_worker, to_bake):
            if url:
                final = (public_base_url.rstrip("/") + url) if public_base_url else url
                results.append((cid, final))

    # 4. Bulk DB update
    if results:
        conn = get_db(); c = conn.cursor()
        for cid, final_url in results:
            c.execute("UPDATE cards SET image_url = ? WHERE id = ?",
                       (final_url, cid))
        conn.commit(); conn.close()
    stats["updated"] = len(results)
    stats["failed"] += (len(to_bake) - len(results))
    print(f"[overlay] DONE : updated={stats['updated']} skipped={stats['skipped']} "
          f"failed={stats['failed']} total={stats['total']}")
    return stats
