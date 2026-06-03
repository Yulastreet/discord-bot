# duel_sabres.py
#
# La source de vérité des sabres est désormais la table `sabres` en DB.
# SABRES_DEFAULT sert UNIQUEMENT au seed initial (première création de la DB).
# Toute lecture/écriture passe par les fonctions DB (db_get_sabre, etc.).

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
        "nom": "Sabre Laser Bleu",
        "emoji": "🔵",
        "rarete": "C",
        "prix": 0,
        "description": "Le sabre des Gardiens de la Paix.",
        "speciale": {
            "nom": "Bouclier de Force",
            "description": "Absorbe 100% des dégâts du prochain coup adverse.",
            "emoji": "🛡️",
            "effet": "absorb_next",
        }
    },
    
    "vert": {
        "id": "vert",
        "nom": "Sabre Laser Vert",
        "emoji": "🟢",
        "rarete": "C",
        "prix": 0,
        "description": "Le sabre des Consulaires Jedi.",
        "speciale": {
            "nom": "Équilibre de la Force",
            "description": "Récupère 50% des dégâts infligés en HP.",
            "emoji": "⚖️",
            "effet": "lifesteal_50",
        }
    },
    
    "rouge": {
        "id": "rouge",
        "nom": "Sabre Laser Rouge",
        "emoji": "🔴",
        "rarete": "C",
        "prix": 0,
        "description": "Le sabre des Sith.",
        "speciale": {
            "nom": "Rage Sith",
            "description": "Double les dégâts du prochain coup.",
            "emoji": "😡",
            "effet": "rage_next",
        }
    },
    
    "blanc": {
        "id": "blanc",
        "nom": "Sabre Laser Blanc",
        "emoji": "⚪",
        "rarete": "UC",
        "prix": 100,
        "description": "Un sabre unique trouvé par Ahsoka.",
        "speciale": {
            "nom": "Dualiste",
            "description": "Attaque 2 fois d'affilée (2 coups).",
            "emoji": "⚪⚪",
            "effet": "double_attaque",
        }
    },
    
    "violet": {
        "id": "violet",
        "nom": "Sabre Laser Violet",
        "emoji": "🟣",
        "rarete": "UC",
        "prix": 100,
        "description": "Le sabre équilibré de Mace Windu.",
        "speciale": {
            "nom": "Vortex de la Force",
            "description": "Annule la parade adverse et inflige les dégâts.",
            "emoji": "🌀",
            "effet": "ignore_defense",
        }
    },
    
    "orange": {
        "id": "orange",
        "nom": "Sabre Laser Orange",
        "emoji": "🟠",
        "rarete": "UC",
        "prix": 100,
        "description": "Le sabre chaleureux de Depa Billaba.",
        "speciale": {
            "nom": "Coup Chargé",
            "description": "Inflige 50% de dégâts supplémentaires.",
            "emoji": "⚡",
            "effet": "charged_attack",
        }
    },
    
    "jaune": {
        "id": "jaune",
        "nom": "Sabre Laser Jaune",
        "emoji": "🟡",
        "rarete": "R",
        "prix": 500,
        "description": "Le sabre des Gardiens du Temple Jedi.",
        "speciale": {
            "nom": "Precision Absolue",
            "description": "Les 2 prochaines attaques ignoreront la défense.",
            "emoji": "🎯",
            "effet": "precision_x2",
        }
    },
    
    "cyan": {
        "id": "cyan",
        "nom": "Sabre Laser Cyan",
        "emoji": "🔷",
        "rarete": "R",
        "prix": 500,
        "description": "Un sabre technologiquement avancé.",
        "speciale": {
            "nom": "Surcharge Énergétique",
            "description": "Inflige 75% de dégâts supplémentaires et ignore la défense.",
            "emoji": "⚡⚡",
            "effet": "overcharge",
        }
    },
    
    "rose": {
        "id": "rose",
        "nom": "Sabre Laser Rose",
        "emoji": "🩷",
        "rarete": "R",
        "prix": 500,
        "description": "Un sabre rare et puissant.",
        "speciale": {
            "nom": "Drain de Force",
            "description": "Récupère 75% des dégâts infligés en HP.",
            "emoji": "💗",
            "effet": "lifesteal_75",
        }
    },
    
    "noir": {
        "id": "noir",
        "nom": "Sabre Laser Noir",
        "emoji": "⚫",
        "rarete": "SR",
        "prix": 1500,
        "description": "Le Darksaber légendaire.",
        "speciale": {
            "nom": "Domination Absolue",
            "description": "Paralyse l'adversaire pour 1 tour (il ne peut que se défendre).",
            "emoji": "⛓️",
            "effet": "paralyze_next",
        }
    },
    
    "argent": {
        "id": "argent",
        "nom": "Sabre Laser Argent",
        "emoji": "🩶",
        "rarete": "SR",
        "prix": 1500,
        "description": "Un sabre légendaire forgé dans les étoiles.",
        "speciale": {
            "nom": "Réflexion Parfaite",
            "description": "Renvoie 100% des dégâts au prochain coup adverse.",
            "emoji": "🪞",
            "effet": "reflect_100",
        }
    },
    
    "arc_en_ciel": {
        "id": "arc_en_ciel",
        "nom": "Sabre Laser Arc-en-Ciel",
        "emoji": "🌈",
        "rarete": "SSR",
        "prix": 5000,
        "description": "Le plus puissant des sabres, aux pouvoirs infinis.",
        "speciale": {
            "nom": "Pouvoir Suprême",
            "description": "Cumule les effets : 100% de dégâts + ignore défense + lifesteal 100%.",
            "emoji": "👑",
            "effet": "ultimate",
        }
    },

    "obsidienne": {
        "id": "obsidienne",
        "nom": "Sabre Laser Obsidienne",
        "emoji": "⬛",
        "rarete": "SSR",
        "prix": 5000,
        "description": "Lame forgée dans le néant absolu.",
        "speciale": {
            "nom": "Frappe du Vide",
            "description": "Inflige au moins 50% des HP max de la cible + drain total. Ignore boucliers et réflexion.",
            "emoji": "🕳️",
            "effet": "void_strike",
        }
    },

    "celeste": {
        "id": "celeste",
        "nom": "Sabre Laser Céleste",
        "emoji": "🌠",
        "rarete": "SSR",
        "prix": 5000,
        "description": "Lame tissée dans la trame des étoiles.",
        "speciale": {
            "nom": "Salve Stellaire",
            "description": "Inflige 250% de dégâts brut (touchable par parade et réflexion).",
            "emoji": "✨",
            "effet": "stellar_burst",
        }
    },
}

def get_sabre(sabre_id):
    """Récupère un sabre par ID (DB)."""
    from database import db_get_sabre
    sabre = db_get_sabre(sabre_id)
    if sabre is None:
        # Fallback sur le defaut pour ne pas casser un duel si le sabre fut supprimé
        sabre = SABRES_DEFAULT.get(sabre_id) or SABRES_DEFAULT["bleu"]
    return sabre

def get_tous_les_sabres():
    """Retourne tous les sabres (DB)."""
    from database import db_get_tous_sabres
    res = db_get_tous_sabres()
    return res if res else SABRES_DEFAULT

def get_sabres_par_rarete(rarete):
    """Récupère tous les sabres d'une rareté (DB)."""
    from database import db_get_sabres_par_rarete
    return db_get_sabres_par_rarete(rarete)

def get_prix_sabre(sabre_id):
    """Récupère le prix d'un sabre."""
    sabre = get_sabre(sabre_id)
    return sabre["prix"] if sabre else 0

# Compatibilité ascendante : ancien nom utilisé ailleurs
SABRES = SABRES_DEFAULT