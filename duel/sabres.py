# duel_sabres.py
#
# The source of truth for sabers is now the `sabres` table in the DB.
# SABRES_DEFAULT is used ONLY for the initial seed (first DB creation).
# Every read/write goes through the DB helpers (db_get_sabre, etc.).
#
# i18n note: the DB stores one single label per saber (whatever language it was
# seeded/edited in). Display helpers below (sabre_nom, sabre_description, ...)
# look the translation up by saber id in locales/<lang>/duel.json and fall back
# to the stored value when there is no entry (e.g. seasonal sabers created from
# the dashboard). The dict keys / ids / rarity codes are NEVER translated.

from services.i18n import DEFAULT_LOCALE, get_catalog

RARETES = {
    "C": {"label": "Common", "emoji": "⚪", "bonus_stats": 0},
    "UC": {"label": "Uncommon", "emoji": "🟢", "bonus_stats": 1},
    "R": {"label": "Rare", "emoji": "🔵", "bonus_stats": 2},
    "SR": {"label": "Super Rare", "emoji": "🟣", "bonus_stats": 3},
    "SSR": {"label": "Super Super Rare", "emoji": "🟡", "bonus_stats": 5},
}

SABRES_DEFAULT = {
    "bleu": {
        "id": "bleu",
        "nom": "Blue Lightsaber",
        "emoji": "🔵",
        "rarete": "C",
        "prix": 0,
        "description": "The blade of the Peacekeepers.",
        "speciale": {
            "nom": "Force Shield",
            "description": "Absorbs 100% of the opponent's next hit.",
            "emoji": "🛡️",
            "effet": "absorb_next",
        }
    },

    "vert": {
        "id": "vert",
        "nom": "Green Lightsaber",
        "emoji": "🟢",
        "rarete": "C",
        "prix": 0,
        "description": "The blade of the Jedi Consulars.",
        "speciale": {
            "nom": "Force Balance",
            "description": "Recovers 50% of the damage dealt as HP.",
            "emoji": "⚖️",
            "effet": "lifesteal_50",
        }
    },

    "rouge": {
        "id": "rouge",
        "nom": "Red Lightsaber",
        "emoji": "🔴",
        "rarete": "C",
        "prix": 0,
        "description": "The blade of the Sith.",
        "speciale": {
            "nom": "Sith Rage",
            "description": "Doubles the damage of your next hit.",
            "emoji": "😡",
            "effet": "rage_next",
        }
    },

    "blanc": {
        "id": "blanc",
        "nom": "White Lightsaber",
        "emoji": "⚪",
        "rarete": "UC",
        "prix": 100,
        "description": "A unique blade found by Ahsoka.",
        "speciale": {
            "nom": "Dualist",
            "description": "Strikes twice in a row (2 hits).",
            "emoji": "⚪⚪",
            "effet": "double_attaque",
        }
    },

    "violet": {
        "id": "violet",
        "nom": "Purple Lightsaber",
        "emoji": "🟣",
        "rarete": "UC",
        "prix": 100,
        "description": "Mace Windu's balanced blade.",
        "speciale": {
            "nom": "Force Vortex",
            "description": "Cancels the opponent's parry and deals full damage.",
            "emoji": "🌀",
            "effet": "ignore_defense",
        }
    },

    "orange": {
        "id": "orange",
        "nom": "Orange Lightsaber",
        "emoji": "🟠",
        "rarete": "UC",
        "prix": 100,
        "description": "Depa Billaba's warm blade.",
        "speciale": {
            "nom": "Charged Strike",
            "description": "Deals 50% extra damage.",
            "emoji": "⚡",
            "effet": "charged_attack",
        }
    },

    "jaune": {
        "id": "jaune",
        "nom": "Yellow Lightsaber",
        "emoji": "🟡",
        "rarete": "R",
        "prix": 500,
        "description": "The blade of the Jedi Temple Guards.",
        "speciale": {
            "nom": "Absolute Precision",
            "description": "The next 2 attacks ignore defense.",
            "emoji": "🎯",
            "effet": "precision_x2",
        }
    },

    "cyan": {
        "id": "cyan",
        "nom": "Cyan Lightsaber",
        "emoji": "🔷",
        "rarete": "R",
        "prix": 500,
        "description": "A technologically advanced blade.",
        "speciale": {
            "nom": "Energy Overload",
            "description": "Deals 75% extra damage and ignores defense.",
            "emoji": "⚡⚡",
            "effet": "overcharge",
        }
    },

    "rose": {
        "id": "rose",
        "nom": "Pink Lightsaber",
        "emoji": "🩷",
        "rarete": "R",
        "prix": 500,
        "description": "A rare and powerful blade.",
        "speciale": {
            "nom": "Force Drain",
            "description": "Recovers 75% of the damage dealt as HP.",
            "emoji": "💗",
            "effet": "lifesteal_75",
        }
    },

    "noir": {
        "id": "noir",
        "nom": "Black Lightsaber",
        "emoji": "⚫",
        "rarete": "SR",
        "prix": 1500,
        "description": "The legendary Darksaber.",
        "speciale": {
            "nom": "Absolute Domination",
            "description": "Stuns the opponent for 1 turn (they can only defend).",
            "emoji": "⛓️",
            "effet": "paralyze_next",
        }
    },

    "argent": {
        "id": "argent",
        "nom": "Silver Lightsaber",
        "emoji": "🩶",
        "rarete": "SR",
        "prix": 1500,
        "description": "A legendary blade forged among the stars.",
        "speciale": {
            "nom": "Perfect Reflection",
            "description": "Reflects 100% of the damage from the opponent's next hit.",
            "emoji": "🪞",
            "effet": "reflect_100",
        }
    },

    "arc_en_ciel": {
        "id": "arc_en_ciel",
        "nom": "Rainbow Lightsaber",
        "emoji": "🌈",
        "rarete": "SSR",
        "prix": 5000,
        "description": "The mightiest blade of all, with boundless power.",
        "speciale": {
            "nom": "Supreme Power",
            "description": "Stacks every effect: 100% damage + ignores defense + 100% lifesteal.",
            "emoji": "👑",
            "effet": "ultimate",
        }
    },

    "obsidienne": {
        "id": "obsidienne",
        "nom": "Obsidian Lightsaber",
        "emoji": "⬛",
        "rarete": "SSR",
        "prix": 5000,
        "description": "A blade forged in absolute nothingness.",
        "speciale": {
            "nom": "Void Strike",
            "description": "Deals at least 50% of the target's max HP + full drain. Goes through shields and reflection.",
            "emoji": "🕳️",
            "effet": "void_strike",
        }
    },

    "celeste": {
        "id": "celeste",
        "nom": "Celestial Lightsaber",
        "emoji": "🌠",
        "rarete": "SSR",
        "prix": 5000,
        "description": "A blade woven into the fabric of the stars.",
        "speciale": {
            "nom": "Stellar Burst",
            "description": "Deals 250% raw damage (still stopped by parry and reflection).",
            "emoji": "✨",
            "effet": "stellar_burst",
        }
    },
}


# ===== Display helpers (translation only, stored values untouched) =====

def _catalog_get(key, locale=DEFAULT_LOCALE):
    """Catalog lookup with EN fallback, without logging a MISSING warning.

    Dynamic keys (saber ids, rarity codes) may legitimately be absent when the
    row was created from the dashboard, so a silent None is the expected path.
    """
    val = get_catalog(locale).get(key)
    if val is None and (locale or DEFAULT_LOCALE) != DEFAULT_LOCALE:
        val = get_catalog(DEFAULT_LOCALE).get(key)
    return val


def rarete_label(rarete_id, locale=DEFAULT_LOCALE):
    """Translated rarity label. RARETES keys stay untouched (used by the DB + dashboard)."""
    val = _catalog_get(f"duel.rarity.{str(rarete_id).lower()}", locale)
    if val:
        return val
    return RARETES.get(rarete_id, {}).get("label", rarete_id)


def rarete_emoji(rarete_id):
    return RARETES.get(rarete_id, {}).get("emoji", "")


def _sabre_field(sabre, field, fallback_keys, locale=DEFAULT_LOCALE):
    sid = (sabre or {}).get("id", "")
    val = _catalog_get(f"duel.sabres.{sid}.{field}", locale) if sid else None
    if val:
        return val
    node = sabre or {}
    for k in fallback_keys[:-1]:
        node = node.get(k) or {}
    return node.get(fallback_keys[-1], "")


def sabre_nom(sabre, locale=DEFAULT_LOCALE):
    """Translated saber name, falls back to the value stored in DB."""
    return _sabre_field(sabre, "name", ["nom"], locale)


def sabre_description(sabre, locale=DEFAULT_LOCALE):
    """Translated saber description, falls back to the value stored in DB."""
    return _sabre_field(sabre, "description", ["description"], locale)


def sabre_speciale_nom(sabre, locale=DEFAULT_LOCALE):
    """Translated special-ability name, falls back to the value stored in DB."""
    return _sabre_field(sabre, "special_name", ["speciale", "nom"], locale)


def sabre_speciale_description(sabre, locale=DEFAULT_LOCALE):
    """Translated special-ability description, falls back to the value stored in DB."""
    return _sabre_field(sabre, "special_description", ["speciale", "description"], locale)


def get_sabre(sabre_id):
    """Fetch a saber by id (DB)."""
    from database import db_get_sabre
    sabre = db_get_sabre(sabre_id)
    if sabre is None:
        # Fall back on the default so a duel does not break if the saber was deleted
        sabre = SABRES_DEFAULT.get(sabre_id) or SABRES_DEFAULT["bleu"]
    return sabre

def get_tous_les_sabres():
    """Return every saber (DB)."""
    from database import db_get_tous_sabres
    res = db_get_tous_sabres()
    return res if res else SABRES_DEFAULT

def get_sabres_par_rarete(rarete):
    """Fetch every saber of a given rarity (DB)."""
    from database import db_get_sabres_par_rarete
    return db_get_sabres_par_rarete(rarete)

def get_prix_sabre(sabre_id):
    """Fetch the price of a saber."""
    sabre = get_sabre(sabre_id)
    return sabre["prix"] if sabre else 0

# Backward compatibility: old name used elsewhere
SABRES = SABRES_DEFAULT
