"""
Feature guard: registry of per-guild toggleable features.
Used by the dashboard (/features) and the tree's interaction_check hook.
"""

FEATURE_REGISTRY = [
    # --- Engagement ---
    {
        "key":   "xp_enabled",
        "label": "XP & Levels",
        "desc":  "XP gain per message, levels, leaderboard.",
        "cat":   "Engagement",
    },
    {
        "key":   "duels",
        "label": "Lightsaber Duels",
        "desc":  "TookCoins duels, saber collection, combat profile.",
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
        "label":   "Cards Events (auto drops)",
        "desc":    "Automatic card drops in a channel (first person to type the code wins). Timing and rarity are handled by the TookBot team.",
        "cat":     "Engagement",
        "default": "0",   # opt-in: disabled by default
    },
    # --- Fun ---
    {
        "key":   "fun",
        "label": "Fun",
        "desc":  "/8ball, /dice, /coinflip, /ship, /who, /joke, /rate...",
        "cat":   "Fun",
    },
    {
        "key":   "music",
        "label": "Music",
        "desc":  "/play, /skip, /queue, /stop, /join, /leave.",
        "cat":   "Fun",
    },
    # --- Moderation ---
    {
        "key":   "moderation_cmds",
        "label": "Moderation (slash)",
        "desc":  "/kick, /ban, /clear - direct moderation commands.",
        "cat":   "Moderation",
    },
    {
        "key":   "tickets",
        "label": "Tickets",
        "desc":  "Ticket system with configurable panels.",
        "cat":   "Moderation",
    },
    {
        "key":   "welcome",
        "label": "Welcome",
        "desc":  "Custom welcome message when a member joins.",
        "cat":   "Moderation",
    },
    {
        "key":   "rolereaction",
        "label": "Reaction Roles",
        "desc":  "Assign roles by clicking an emoji.",
        "cat":   "Moderation",
    },
    # --- Tools ---
    {
        "key":   "reactions",
        "label": "Auto-reactions",
        "desc":  "Automatic reactions configured for specific members.",
        "cat":   "Tools",
    },
    {
        "key":   "social_alerts",
        "label": "Twitch/YT Alerts",
        "desc":  "Notifications when a Twitch stream goes live or a YouTube video drops.",
        "cat":   "Tools",
    },
    {
        "key":   "custom_commands",
        "label": "Custom Commands",
        "desc":  "/cmd - custom commands created on this server.",
        "cat":   "Tools",
    },
    {
        "key":   "poll",
        "label": "Polls",
        "desc":  "/poll to create a poll in a channel.",
        "cat":   "Tools",
    },
    # --- Games ---
    {
        "key":   "cs2",
        "label": "Counter-Strike 2",
        "desc":  "Stats, rank, inventory, prices, queue, loadout...",
        "cat":   "Games",
    },
    {
        "key":   "lol",
        "label": "League of Legends",
        "desc":  "Stats, rank, history, live, build, scout, skin, mastery...",
        "cat":   "Games",
    },
]

# Valid keys (for API-side validation)
FEATURE_KEYS = {f["key"] for f in FEATURE_REGISTRY}

# Mapping: Discord root command name -> feature key
# Root command = what Discord sends in interaction.data["name"]
COMMAND_FEATURE_MAP: dict[str, str] = {
    # XP / Niveaux
    # Note: "xp" (the /xp on/off group) is intentionally ABSENT so admins can
    # re-enable XP via slash even when the feature is disabled.
    "level":       "xp_enabled",
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
    "dice":     "fun",
    "coinflip": "fun",
    "joke":     "fun",
    "ship":     "fun",
    "choice":   "fun",
    "random":   "fun",
    "who":      "fun",
    "clap":     "fun",
    "rate":     "fun",
    "quote":    "fun",
    "pp":       "fun",

    # Moderation
    "kick":  "moderation_cmds",
    "ban":   "moderation_cmds",
    "clear": "moderation_cmds",

    # Tickets
    "ticket": "tickets",

    # Bienvenue
    "setwelcome": "welcome",

    # Reaction roles
    "rolereaction": "rolereaction",

    # Auto-reactions
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
    """Return the human-readable label for a feature key."""
    for f in FEATURE_REGISTRY:
        if f["key"] == key:
            return f["label"]
    return key
