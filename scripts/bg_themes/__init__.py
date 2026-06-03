"""Generateurs BG saisonniers thematiques.

Chaque module (un par mois) expose une fonction `generate(out_dir, palette)` qui
ecrit 5 fichiers PNG dans out_dir, chacun correspondant a une variante visuelle
du theme du mois (style_id different : ex `crystal_cave`, `liquid_chrome`...).

Le dispatcher `get_generator(month_key)` recupere le generator approprie. Si le
mois n'a pas encore son module thematique, le fallback est `generic` (les 5
generateurs abstraits parametres par palette).
"""
from __future__ import annotations
from importlib import import_module
from typing import Callable

# Mapping clef MM -> nom de module
_MONTH_TO_MODULE: dict[str, str] = {
    "06": "solaire",
    # Les autres mois utilisent encore "generic" (paramerage par palette).
    # Ils seront migres un par un vers des modules thematiques.
}


def get_generator(month_key: str) -> Callable:
    """Retourne la fonction generate(out_dir, palette, seed_base) du theme.

    Fallback : module 'generic' qui utilise les 5 generateurs abstraits.
    """
    mm = month_key[5:7] if month_key and len(month_key) >= 7 else ""
    mod_name = _MONTH_TO_MODULE.get(mm, "generic")
    mod = import_module(f"bg_themes.{mod_name}")
    return mod.generate
