"""
Feature guard : registre des fonctionnalités toggleables par guild.
Utilisé par le dashboard (/features) et le hook interaction_check du tree.
"""

FEATURE_REGISTRY = [
    # --- Engagement ---
    {
        "key":   "xp_enabled",
        "label": "XP & Niveaux",
        "desc":  "Gain d'XP par message, niveaux, leaderboard.",
        "cat":   "Engagement",
    },
    {
        "key":   "duels",
        "label": "Duels sabres laser",
        "desc":  "Duels TookCoins, collection de sabres, profil combat.",
        "cat":   "Engagement",
    },
    {
        "key":   "giveaway",
        "label": "Giveaways",
        "desc":  "/giveaway create, list, reroll, cancel.",
        "cat":   "Engagement",
    },
    {
        "key":     "card_events",
        "label":   "Cards Events (drops auto)",
        "desc":    "Drops automatiques de cartes dans un salon (1ere personne à taper le code gagne). Le timing et la rareté sont gérés par l'équipe TookBot.",
        "cat":     "Engagement",
        "default": "0",   # opt-in : désactivé par défaut
    },
    # --- Fun ---
    {
        "key":   "fun",
        "label": "Fun",
        "desc":  "/8ball, /dé, /coinflip, /ship, /qui, /blague, /rate...",
        "cat":   "Fun",
    },
    {
        "key":   "music",
        "label": "Musique",
        "desc":  "/play, /skip, /queue, /stop, /join, /leave.",
        "cat":   "Fun",
    },
    # --- Modération ---
    {
        "key":   "moderation_cmds",
        "label": "Modération (slash)",
        "desc":  "/kick, /ban, /clear — commandes de modération directes.",
        "cat":   "Modération",
    },
    {
        "key":   "tickets",
        "label": "Tickets",
        "desc":  "Système de tickets avec panneaux configurables.",
        "cat":   "Modération",
    },
    {
        "key":   "welcome",
        "label": "Bienvenue",
        "desc":  "Message de bienvenue personnalisé à l'arrivée d'un membre.",
        "cat":   "Modération",
    },
    {
        "key":   "rolereaction",
        "label": "Rôles-Réaction",
        "desc":  "Assignation de rôles par clic sur un emoji.",
        "cat":   "Modération",
    },
    # --- Outils ---
    {
        "key":   "reactions",
        "label": "Auto-réactions",
        "desc":  "Réactions automatiques configurées sur certains membres.",
        "cat":   "Outils",
    },
    {
        "key":   "social_alerts",
        "label": "Alertes Twitch/YT",
        "desc":  "Notifications lors d'un live Twitch ou d'une vidéo YouTube.",
        "cat":   "Outils",
    },
    {
        "key":   "custom_commands",
        "label": "Commandes custom",
        "desc":  "/cmd — commandes personnalisées créées sur ce serveur.",
        "cat":   "Outils",
    },
    {
        "key":   "poll",
        "label": "Sondages",
        "desc":  "/poll pour créer un sondage dans un salon.",
        "cat":   "Outils",
    },
    # --- Jeux ---
    {
        "key":   "cs2",
        "label": "Counter-Strike 2",
        "desc":  "Stats, rank, inventaire, prix, queue, loadout...",
        "cat":   "Jeux",
    },
    {
        "key":   "lol",
        "label": "League of Legends",
        "desc":  "Stats, rank, history, live, build, scout, skin, mastery...",
        "cat":   "Jeux",
    },
]

# Clés valides (pour validation côté API)
FEATURE_KEYS = {f["key"] for f in FEATURE_REGISTRY}

# Mapping : nom de commande racine Discord -> clé de feature
# La commande racine = ce que Discord envoie dans interaction.data["name"]
COMMAND_FEATURE_MAP: dict[str, str] = {
    # XP / Niveaux
    # Note : "xp" (groupe /xp on/off) intentionnellement ABSENT
    # pour que les admins puissent re-activer l'XP via slash même si désactivé.
    "niveau":      "xp_enabled",
    "leaderboard": "xp_enabled",

    # Musique
    "join":  "music",
    "play":  "music",
    "skip":  "music",
    "queue": "music",
    "stop":  "music",
    "leave": "music",

    # Giveaway
    "giveaway": "giveaway",

    # Fun
    "8ball":    "fun",
    "dé":       "fun",
    "coinflip": "fun",
    "blague":   "fun",
    "ship":     "fun",
    "choix":    "fun",
    "random":   "fun",
    "qui":      "fun",
    "clap":     "fun",
    "rate":     "fun",
    "citation": "fun",
    "zgeg":     "fun",

    # Modération
    "kick":  "moderation_cmds",
    "ban":   "moderation_cmds",
    "clear": "moderation_cmds",

    # Tickets
    "ticket": "tickets",

    # Bienvenue
    "setwelcome": "welcome",

    # Rôles-Réaction
    "rolereaction": "rolereaction",

    # Auto-réactions
    "reaction_add":    "reactions",
    "reaction_remove": "reactions",
    "reaction_list":   "reactions",

    # Alertes sociales
    "socialalert": "social_alerts",

    # Commandes custom
    "cmd": "custom_commands",

    # Sondages
    "poll": "poll",

    # CS2
    "cs": "cs2",

    # LoL
    "lol": "lol",

    # Duels
    "duel":       "duels",
    "profil":     "duels",
    "statpoint":  "duels",
    "collection": "duels",
    "historique": "duels",
    "sabre":      "duels",
}


def get_feature_label(key: str) -> str:
    """Retourne le label lisible d'une clé de feature."""
    for f in FEATURE_REGISTRY:
        if f["key"] == key:
            return f["label"]
    return key
