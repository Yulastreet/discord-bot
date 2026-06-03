"""Theme saisonnier du Battle Pass.

Source unique de verite pour :
- Nom thematique de la saison (palette + ambiance)
- Noms+effets sabres saisonniers (R/SR/SSR) — l'effet COPIE celui d'un sabre
  f2p existant (anti-P2W) MAIS le sabre f2p source change chaque mois pour
  apporter variete de gameplay au Pass.
- Palette de couleurs pour les BG saisonniers (parametrage generateurs)
- Libelle d'affichage des BG (UI dashboard)

Chaque mois a son theme. Un fallback est applique pour les mois absents.

Pool d'effets f2p source :
- R   : cyan (overcharge) / rose (lifesteal_75) / jaune (precision_x2)        -> cycle 3
- SR  : noir (paralyze_next) / argent (reflect_100)                            -> cycle 2
- SSR : arc_en_ciel (ultimate) / obsidienne (void_strike) / celeste (stellar_burst) -> cycle 3
"""
from __future__ import annotations

# ============================================================
# DEFINITION DES THEMES PAR MOIS (clef = "MM")
# Chaque entree :
#   - name        : nom thematique (FR)
#   - sabres      : dict rarete -> {"source_id", "nom", "emoji_sabre", "nom_special", "emoji_special"}
#       L'effet (mecanique) ET la description detaillee viennent du sabre f2p
#       source. Le nom + emoji + nom_special sont reskins thematiques.
#   - bg_palette  : (color_primary, color_secondary, color_accent)
#   - bg_labels   : dict style_id -> nom_convivial du BG
#   - seed_offset : decale les seeds des generateurs pour variation
# ============================================================

MONTH_THEMES: dict[str, dict] = {
    "01": {
        "name": "Polaire",
        "sabres": {
            "R":   {"source_id": "jaune", "nom": "Eclat Polaire",     "emoji_sabre": "❄️", "nom_special": "Precision Polaire", "emoji_special": "🎯"},
            "SR":  {"source_id": "argent","nom": "Mirage Boreal",     "emoji_sabre": "🌌", "nom_special": "Mirage Boreal",     "emoji_special": "🪞"},
            "SSR": {"source_id": "arc_en_ciel","nom": "Etoile Polaire","emoji_sabre": "🌠", "nom_special": "Apotheose Polaire", "emoji_special": "👑"},
        },
        "bg_palette":  ((200, 230, 255), (60, 100, 160),  (180, 220, 255)),
        "bg_labels": {
            "crystal_cave":  "Caverne de Glace",
            "liquid_chrome": "Chrome Glacial",
            "neon_tokyo":    "Tokyo Givre",
            "stained_glass": "Vitrail Polaire",
            "cosmic_vortex": "Vortex Polaire",
        },
        "seed_offset": 100,
    },
    "02": {
        "name": "Auroral",
        "sabres": {
            "R":   {"source_id": "rose",  "nom": "Souffle Auroral",   "emoji_sabre": "💚", "nom_special": "Drain Auroral",     "emoji_special": "💗"},
            "SR":  {"source_id": "noir",  "nom": "Sceau Auroral",     "emoji_sabre": "🟢", "nom_special": "Sceau Auroral",     "emoji_special": "⛓️"},
            "SSR": {"source_id": "celeste","nom": "Aurore Stellaire", "emoji_sabre": "💫", "nom_special": "Symphonie Aurorale","emoji_special": "✨"},
        },
        "bg_palette":  ((140, 240, 200), (40, 80, 120),   (200, 255, 180)),
        "bg_labels": {
            "crystal_cave":  "Caverne Auroreale",
            "liquid_chrome": "Chrome Auroreal",
            "neon_tokyo":    "Tokyo Aurore",
            "stained_glass": "Vitrail Auroreal",
            "cosmic_vortex": "Vortex Auroral",
        },
        "seed_offset": 200,
    },
    "03": {
        "name": "Eveil",
        "sabres": {
            "R":   {"source_id": "cyan",  "nom": "Eveil Sylvestre",   "emoji_sabre": "🌱", "nom_special": "Surcharge Sylvestre","emoji_special": "🌿"},
            "SR":  {"source_id": "argent","nom": "Echo Sylvestre",    "emoji_sabre": "🍃", "nom_special": "Echo Sylvestre",    "emoji_special": "🪞"},
            "SSR": {"source_id": "obsidienne","nom": "Vide Florissant","emoji_sabre": "🌸", "nom_special": "Brisure Florale",   "emoji_special": "🕳️"},
        },
        "bg_palette":  ((180, 240, 130), (60, 140, 80),   (255, 200, 230)),
        "bg_labels": {
            "crystal_cave":  "Caverne Verdoyante",
            "liquid_chrome": "Chrome Sylvestre",
            "neon_tokyo":    "Tokyo Floral",
            "stained_glass": "Vitrail Printanier",
            "cosmic_vortex": "Vortex Floral",
        },
        "seed_offset": 300,
    },
    "04": {
        "name": "Floral",
        "sabres": {
            "R":   {"source_id": "rose",  "nom": "Petale Drainante",  "emoji_sabre": "🌸", "nom_special": "Drain Floral",      "emoji_special": "💗"},
            "SR":  {"source_id": "noir",  "nom": "Sceau Cerisier",    "emoji_sabre": "🌺", "nom_special": "Sceau Cerisier",    "emoji_special": "⛓️"},
            "SSR": {"source_id": "arc_en_ciel","nom": "Hanami Supreme","emoji_sabre": "💮", "nom_special": "Apotheose Hanami",  "emoji_special": "👑"},
        },
        "bg_palette":  ((255, 200, 220), (180, 80, 140),  (255, 230, 180)),
        "bg_labels": {
            "crystal_cave":  "Caverne Florale",
            "liquid_chrome": "Chrome Petale",
            "neon_tokyo":    "Tokyo Hanami",
            "stained_glass": "Vitrail Cerisier",
            "cosmic_vortex": "Vortex Petale",
        },
        "seed_offset": 400,
    },
    "05": {
        "name": "Lunaire",
        "sabres": {
            "R":   {"source_id": "jaune", "nom": "Eclat Lunaire",     "emoji_sabre": "🌒", "nom_special": "Visee Lunaire",     "emoji_special": "🎯"},
            "SR":  {"source_id": "argent","nom": "Croissant De Lune", "emoji_sabre": "🌘", "nom_special": "Reflet Lunaire",    "emoji_special": "🪞"},
            "SSR": {"source_id": "celeste","nom": "Etoile Du Matin",  "emoji_sabre": "🌟", "nom_special": "Salve Stellaire",   "emoji_special": "✨"},
        },
        "bg_palette":  ((180, 200, 255), (40, 50, 90),    (220, 220, 240)),
        "bg_labels": {
            "crystal_cave":  "Caverne Lunaire",
            "liquid_chrome": "Chrome Lunaire",
            "neon_tokyo":    "Tokyo Nocturne",
            "stained_glass": "Vitrail Stellaire",
            "cosmic_vortex": "Vortex Lunaire",
        },
        "seed_offset": 500,
    },
    "06": {
        "name": "Solaire",
        "sabres": {
            "R":   {"source_id": "cyan",  "nom": "Brulot Solaire",    "emoji_sabre": "🔥", "nom_special": "Surcharge Solaire", "emoji_special": "☀️"},
            "SR":  {"source_id": "noir",  "nom": "Sceau Ardent",      "emoji_sabre": "🟠", "nom_special": "Sceau Ardent",      "emoji_special": "⛓️"},
            "SSR": {"source_id": "obsidienne","nom": "Eclipse Solaire","emoji_sabre": "🌞", "nom_special": "Vide Solaire",     "emoji_special": "🕳️"},
        },
        "bg_palette":  ((255, 200, 100), (200, 80, 30),   (255, 240, 180)),
        "bg_labels": {
            "crystal_cave":  "Caverne Solaire",
            "liquid_chrome": "Chrome Ardent",
            "neon_tokyo":    "Tokyo Solaire",
            "stained_glass": "Vitrail Solaire",
            "cosmic_vortex": "Vortex Solaire",
        },
        "seed_offset": 600,
    },
    "07": {
        "name": "Cramoisi",
        "sabres": {
            "R":   {"source_id": "rose",  "nom": "Drain Cramoisi",    "emoji_sabre": "🟥", "nom_special": "Drain Cramoisi",    "emoji_special": "💗"},
            "SR":  {"source_id": "argent","nom": "Miroir Brasier",    "emoji_sabre": "🟧", "nom_special": "Miroir Brasier",    "emoji_special": "🪞"},
            "SSR": {"source_id": "arc_en_ciel","nom": "Pyrosphere Supreme","emoji_sabre": "💥", "nom_special": "Apotheose Pyrosphere","emoji_special": "👑"},
        },
        "bg_palette":  ((255, 100, 60),  (140, 30, 30),   (255, 180, 80)),
        "bg_labels": {
            "crystal_cave":  "Caverne Cramoisie",
            "liquid_chrome": "Chrome Brasier",
            "neon_tokyo":    "Tokyo Brasier",
            "stained_glass": "Vitrail Cramoisi",
            "cosmic_vortex": "Vortex Cramoisi",
        },
        "seed_offset": 700,
    },
    "08": {
        "name": "Saharien",
        "sabres": {
            "R":   {"source_id": "jaune", "nom": "Visee Doree",       "emoji_sabre": "🟫", "nom_special": "Visee Doree",       "emoji_special": "🎯"},
            "SR":  {"source_id": "noir",  "nom": "Sceau du Mirage",   "emoji_sabre": "🟡", "nom_special": "Sceau du Mirage",   "emoji_special": "⛓️"},
            "SSR": {"source_id": "celeste","nom": "Mirage Stellaire", "emoji_sabre": "✨", "nom_special": "Salve du Mirage",  "emoji_special": "✨"},
        },
        "bg_palette":  ((230, 200, 120), (160, 100, 60),  (255, 240, 200)),
        "bg_labels": {
            "crystal_cave":  "Caverne Saharienne",
            "liquid_chrome": "Chrome Dore",
            "neon_tokyo":    "Tokyo Saharien",
            "stained_glass": "Vitrail Saharien",
            "cosmic_vortex": "Vortex Mirage",
        },
        "seed_offset": 800,
    },
    "09": {
        "name": "Automnal",
        "sabres": {
            "R":   {"source_id": "cyan",  "nom": "Surcharge Rouille", "emoji_sabre": "🍂", "nom_special": "Surcharge Rouille", "emoji_special": "🍁"},
            "SR":  {"source_id": "argent","nom": "Reflet Automnal",   "emoji_sabre": "🍁", "nom_special": "Reflet Automnal",   "emoji_special": "🪞"},
            "SSR": {"source_id": "obsidienne","nom": "Vide des Vendanges","emoji_sabre": "🍇", "nom_special": "Brisure Vendange","emoji_special": "🕳️"},
        },
        "bg_palette":  ((220, 130, 60),  (120, 50, 30),   (240, 200, 100)),
        "bg_labels": {
            "crystal_cave":  "Caverne Automnale",
            "liquid_chrome": "Chrome Rouille",
            "neon_tokyo":    "Tokyo Automnal",
            "stained_glass": "Vitrail Automnal",
            "cosmic_vortex": "Vortex Automnal",
        },
        "seed_offset": 900,
    },
    "10": {
        "name": "Spectral",
        "sabres": {
            "R":   {"source_id": "rose",  "nom": "Drain Spectral",    "emoji_sabre": "👻", "nom_special": "Drain Spectral",    "emoji_special": "💗"},
            "SR":  {"source_id": "noir",  "nom": "Sceau Maudit",      "emoji_sabre": "🦇", "nom_special": "Sceau Maudit",      "emoji_special": "⛓️"},
            "SSR": {"source_id": "arc_en_ciel","nom": "Lamentation Supreme","emoji_sabre": "💀", "nom_special": "Apotheose Maudite","emoji_special": "👑"},
        },
        "bg_palette":  ((180, 80, 220),  (40, 20, 60),    (255, 150, 80)),
        "bg_labels": {
            "crystal_cave":  "Caverne Spectrale",
            "liquid_chrome": "Chrome Spectral",
            "neon_tokyo":    "Tokyo Spectre",
            "stained_glass": "Vitrail Maudit",
            "cosmic_vortex": "Vortex Spectral",
        },
        "seed_offset": 1000,
    },
    "11": {
        "name": "Crepusculaire",
        "sabres": {
            "R":   {"source_id": "jaune", "nom": "Visee Vesperale",   "emoji_sabre": "🌆", "nom_special": "Visee Vesperale",   "emoji_special": "🎯"},
            "SR":  {"source_id": "argent","nom": "Reflet Vesperal",   "emoji_sabre": "🌃", "nom_special": "Reflet Vesperal",   "emoji_special": "🪞"},
            "SSR": {"source_id": "celeste","nom": "Etoile Vesperale", "emoji_sabre": "🌌", "nom_special": "Symphonie Vesperale","emoji_special": "✨"},
        },
        "bg_palette":  ((140, 80, 160),  (60, 30, 80),    (200, 140, 200)),
        "bg_labels": {
            "crystal_cave":  "Caverne Crepusculaire",
            "liquid_chrome": "Chrome Vesperal",
            "neon_tokyo":    "Tokyo Crepuscule",
            "stained_glass": "Vitrail Crepusculaire",
            "cosmic_vortex": "Vortex Vesperal",
        },
        "seed_offset": 1100,
    },
    "12": {
        "name": "Hivernal",
        "sabres": {
            "R":   {"source_id": "cyan",  "nom": "Surcharge Givre",   "emoji_sabre": "⛄", "nom_special": "Surcharge Givree",  "emoji_special": "🎄"},
            "SR":  {"source_id": "noir",  "nom": "Sceau Boreal",      "emoji_sabre": "🎁", "nom_special": "Sceau Boreal",      "emoji_special": "⛓️"},
            "SSR": {"source_id": "obsidienne","nom": "Etoile De Noel","emoji_sabre": "⭐", "nom_special": "Vide Noelique",    "emoji_special": "🕳️"},
        },
        "bg_palette":  ((230, 230, 255), (50, 80, 120),   (220, 80, 80)),
        "bg_labels": {
            "crystal_cave":  "Caverne Hivernale",
            "liquid_chrome": "Chrome Givre",
            "neon_tokyo":    "Tokyo Hivernal",
            "stained_glass": "Vitrail Noelique",
            "cosmic_vortex": "Vortex Hivernal",
        },
        "seed_offset": 1200,
    },
}


def _theme_for(month_key: str) -> dict:
    """Recupere le theme pour 'YYYY-MM'. Fallback theme 06 (solaire) si absent."""
    if not month_key or len(month_key) < 7:
        return MONTH_THEMES["06"]
    mm = month_key[5:7]
    return MONTH_THEMES.get(mm, MONTH_THEMES["06"])


def sabre_skin(month_key: str, rarete: str) -> dict:
    """Renvoie {source_id, nom, emoji_sabre, nom_special, emoji_special} du theme.

    `rarete` doit etre 'R', 'SR' ou 'SSR'.
    """
    return _theme_for(month_key)["sabres"][rarete]


def bg_palette(month_key: str) -> tuple:
    """(color_primary, color_secondary, color_accent) pour les generateurs BG."""
    return _theme_for(month_key)["bg_palette"]


def bg_seed_offset(month_key: str) -> int:
    return _theme_for(month_key)["seed_offset"]


def theme_name(month_key: str) -> str:
    return _theme_for(month_key)["name"]


def bg_display_name(bg_id: str) -> str:
    """Convertit un bg_id technique en libelle utilisateur lisible.

    Exemples :
      - "default"                         -> "Par defaut"
      - "owner:222737..."                 -> "Mon BG personnel"
      - "seasonal:2026-06:liquid_chrome"  -> "Chrome Ardent (juin 2026)"
      - "neon_grid"                       -> "Neon Grid" (BG permanent : titre case)
    """
    if not bg_id:
        return "Par defaut"
    if bg_id == "default":
        return "Par defaut"
    if bg_id.startswith("owner:"):
        return "Mon BG personnel"
    if bg_id.startswith("seasonal:"):
        parts = bg_id.split(":", 2)
        if len(parts) == 3:
            mk, style = parts[1], parts[2]
            theme = _theme_for(mk)
            label = theme["bg_labels"].get(style, style.replace("_", " ").title())
            month_label = _month_label_fr(mk)
            return f"{label} ({month_label})"
    return bg_id.replace("_", " ").title()


_MONTHS_FR = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]


def _month_label_fr(month_key: str) -> str:
    """'2026-06' -> 'juin 2026'."""
    if not month_key or len(month_key) < 7:
        return month_key or ""
    try:
        y = int(month_key[:4])
        m = int(month_key[5:7])
        if 1 <= m <= 12:
            return f"{_MONTHS_FR[m - 1]} {y}"
    except Exception:
        pass
    return month_key
