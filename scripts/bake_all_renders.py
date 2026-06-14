"""Bake les renders locaux (WebP) de toutes les cartes -> anti-liens-morts.

Pour chaque carte sans render local, telecharge la source une fois, compose
(crop 450x675 + overlay rarete), sauve en static/card_renders/<id>.webp et pointe
image_url sur ton domaine. Les cartes deja locales sont ignorees (sauf --force).

Usage (depuis ~/discord-bot) :
    python3 scripts/bake_all_renders.py              # bake les cartes externes/manquantes
    python3 scripts/bake_all_renders.py --force      # re-bake TOUT (migre png -> webp)
    python3 scripts/bake_all_renders.py --workers 16 # plus de parallelisme

Lancer en arriere-plan pour 19k cartes :
    nohup python3 scripts/bake_all_renders.py > bake.log 2>&1 &
    tail -f bake.log
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

try:
    from dotenv import load_dotenv
    _env = os.path.join(_ROOT, ".env.dev") if os.path.exists(os.path.join(_ROOT, ".env.dev")) \
        else os.path.join(_ROOT, ".env")
    load_dotenv(_env)
except Exception:
    pass

from services.cards_overlay import bake_all_cards  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Re-bake meme les cartes deja locales")
    ap.add_argument("--workers", type=int, default=10, help="Threads paralleles (defaut 10)")
    args = ap.parse_args()

    public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    if not public_base:
        print("ATTENTION : PUBLIC_BASE_URL absent du .env -> image_url sera relative.")
        print("Mets PUBLIC_BASE_URL=https://tookbot.click dans .env pour des URLs completes.\n")

    stats = bake_all_cards(force=args.force,
                           public_base_url=public_base or None,
                           workers=max(1, args.workers))
    print(f"\nFini : {stats}")


if __name__ == "__main__":
    main()
