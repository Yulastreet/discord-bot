"""Registre de handlers pour la queue bot_commands (web -> bot).

Permet a un cog (ex: cards.py) d'enregistrer un handler pour une commande web
sans toucher au dispatcher central de tasks/runtime.py. Le dispatcher consulte
ce registre en premier. Un handler a la signature async (bot, guild_id, payload).
"""

_HANDLERS = {}


def register(name, fn):
    _HANDLERS[name] = fn


def get(name):
    return _HANDLERS.get(name)
