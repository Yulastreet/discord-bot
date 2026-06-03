"""Theme saisonnier du Battle Pass.

Source unique de verite pour :
- Nom thematique de la saison (palette + ambiance)
- Noms+effets sabres saisonniers (R/SR/SSR) — effets restent identiques aux
  sabres f2p de meme rarete (anti-P2W), seul le visuel/nom change
- Palette de couleurs pour les BG saisonniers (parametrage generateurs)
- Libelle d'affichage des BG (UI dashboard)

Chaque mois a son theme. Un fallback est applique pour les mois absents.
"""
from __future__ import annotations

# ============================================================
# DEFINITION DES THEMES PAR MOIS (clef = "MM")
# Chaque entree :
#   - name        : nom thematique (FR)
#   - sabres      : dict rarete -> (nom_sabre, emoji_sabre, nom_special, desc_special, emoji_special)
#                   Mecaniques (champ technique) restent fixes : overcharge/reflect_100/ultimate
#   - bg_palette  : (color_primary, color_secondary, color_accent)
#                   Utilise par les generateurs BG pour teinter
#   - bg_labels   : dict style_id -> nom_convivial du BG
#   - seed_offset : decale les seeds des generateurs pour variation
# ============================================================

MONTH_THEMES: dict[str, dict] = {
    "01": {
        "name": "Polaire",
        "sabres": {
            "R":   ("Lame Polaire",      "❄️", "Surcharge Glaciale",  "Inflige 75% de degats supplementaires et ignore la defense.", "🧊"),
            "SR":  ("Croissant Boreal",  "🌌", "Reflexion Boreale",   "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Polaire",    "🌠", "Apotheose Polaire",   "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Aurore",        "💚", "Surcharge Auroreale", "Inflige 75% de degats supplementaires et ignore la defense.", "🌿"),
            "SR":  ("Croissant Aurore",   "🟢", "Reflexion Aurore",    "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Aurore",      "💫", "Apotheose Aurore",    "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Sylvestre",     "🌱", "Surcharge Sylvestre", "Inflige 75% de degats supplementaires et ignore la defense.", "🌿"),
            "SR":  ("Croissant Verdoyant","🍃", "Reflexion Verdoyante","Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Printaniere", "🌸", "Apotheose Florale",   "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Cerisier",      "🌸", "Surcharge Florale",   "Inflige 75% de degats supplementaires et ignore la defense.", "🌷"),
            "SR":  ("Croissant Petale",   "🌺", "Reflexion Petale",    "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Hanami",      "💮", "Apotheose Hanami",    "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Lunaire",       "🌒", "Surcharge Lunaire",   "Inflige 75% de degats supplementaires et ignore la defense.", "🌙"),
            "SR":  ("Croissant De Lune",  "🌘", "Reflexion Lunaire",   "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Du Matin",    "🌟", "Apotheose Stellaire", "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Solaire",       "🔥", "Surcharge Solaire",   "Inflige 75% de degats supplementaires et ignore la defense.", "☀️"),
            "SR":  ("Croissant Ardent",   "🟠", "Reflexion Ardente",   "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Solaire",     "🌞", "Apotheose Solaire",   "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Cramoisie",     "🟥", "Surcharge Cramoisie", "Inflige 75% de degats supplementaires et ignore la defense.", "🔻"),
            "SR":  ("Croissant Brasier",  "🟧", "Reflexion Brasier",   "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Pyrosphere",  "💥", "Apotheose Pyrosphere","Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Saharienne",    "🟫", "Surcharge Saharienne","Inflige 75% de degats supplementaires et ignore la defense.", "🏜️"),
            "SR":  ("Croissant Dore",     "🟡", "Reflexion Doree",     "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Mirage",      "✨", "Apotheose Mirage",    "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Automnale",     "🍂", "Surcharge Automnale", "Inflige 75% de degats supplementaires et ignore la defense.", "🍁"),
            "SR":  ("Croissant Rouille",  "🍁", "Reflexion Rouille",   "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Vendanges",   "🍇", "Apotheose Automnale", "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Spectrale",     "👻", "Surcharge Spectrale", "Inflige 75% de degats supplementaires et ignore la defense.", "🎃"),
            "SR":  ("Croissant Ombrage",  "🦇", "Reflexion Spectrale", "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Maudite",     "💀", "Apotheose Maudite",   "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Crepusculaire", "🌆", "Surcharge Crepusculaire","Inflige 75% de degats supplementaires et ignore la defense.", "🌫️"),
            "SR":  ("Croissant Vesperal", "🌃", "Reflexion Vesperale", "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile Vesperale",   "🌌", "Apotheose Vesperale", "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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
            "R":   ("Lame Hivernale",     "⛄", "Surcharge Hivernale", "Inflige 75% de degats supplementaires et ignore la defense.", "🎄"),
            "SR":  ("Croissant Boreal",   "🎁", "Reflexion Boreale",   "Renvoie 100% des degats au prochain coup adverse.",            "🪞"),
            "SSR": ("Etoile De Noel",     "⭐", "Apotheose Noelique",  "Cumule les effets : 100% degats + ignore defense + lifesteal 100%.", "👑"),
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


# ============================================================
# Sabres : mecanique technique reste anti-P2W (identique aux f2p de meme rarete)
# ============================================================
SABRE_MECHANIC: dict[str, str] = {
    "R":   "overcharge",
    "SR":  "reflect_100",
    "SSR": "ultimate",
}


def _theme_for(month_key: str) -> dict:
    """Recupere le theme pour 'YYYY-MM'. Fallback theme 06 (solaire) si absent."""
    if not month_key or len(month_key) < 7:
        return MONTH_THEMES["06"]
    mm = month_key[5:7]
    return MONTH_THEMES.get(mm, MONTH_THEMES["06"])


def sabre_data(month_key: str, rarete: str) -> tuple:
    """Renvoie tuple (nom, emoji, nom_special, desc_special, emoji_special, mecanique).

    `rarete` doit etre 'R', 'SR' ou 'SSR'.
    """
    theme = _theme_for(month_key)
    nom, emoji_sabre, nom_special, desc_special, emoji_special = theme["sabres"][rarete]
    mec = SABRE_MECHANIC[rarete]
    return (nom, emoji_sabre, nom_special, desc_special, emoji_special, mec)


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
