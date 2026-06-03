"""Genere 5 backgrounds saisonniers EXCENTRIQUES pour le Battle Pass.

Sortie : assets/niveau_bg/seasonal/<YYYY-MM>/<name>.png

Dispatch vers un module thematique du mois (scripts/bg_themes/<theme>.py)
selon `seasonal_themes._MONTH_TO_MODULE`. Fallback module "generic" pour les
mois qui n'ont pas encore leur theme dedie.

Style IDs canoniques : crystal_cave, liquid_chrome, neon_tokyo, stained_glass,
cosmic_vortex (memes pour tous les mois, ce qui preserve la validite des bg_id
existants 'seasonal:YYYY-MM:<style>' meme apres changement de theme).

Usage :
    python scripts/generate_seasonal_backgrounds.py            # mois courant
    python scripts/generate_seasonal_backgrounds.py 2026-06    # mois specifique
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

# Import des modules locaux : ajout du dossier scripts/ et du repo root
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _REPO_ROOT)   # pour seasonal_themes
sys.path.insert(0, _SCRIPT_DIR)  # pour bg_themes/

from seasonal_themes import bg_palette, bg_seed_offset, theme_name  # noqa: E402
from bg_themes import get_generator  # noqa: E402

ROOT_OUT = os.path.join(_REPO_ROOT, "assets", "niveau_bg", "seasonal")


def main():
    if len(sys.argv) > 1:
        month_key = sys.argv[1]
    else:
        month_key = datetime.utcnow().strftime("%Y-%m")
    out_dir = os.path.join(ROOT_OUT, month_key)
    os.makedirs(out_dir, exist_ok=True)
    palette = bg_palette(month_key)
    seed_base = bg_seed_offset(month_key)
    tname = theme_name(month_key)
    print(f"Generating 5 seasonal BGs for {month_key} (theme={tname}, palette={palette}, seed_base={seed_base}) -> {out_dir}")
    generate = get_generator(month_key)
    generate(out_dir, palette, seed_base)
    print("done.")


if __name__ == "__main__":
    main()
