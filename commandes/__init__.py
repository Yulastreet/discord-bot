from .fun import setup_fun_commands
from .moderation import setup_moderation_commands
from .reactions import setup_reaction_commands
from .utilitaires import setup_utility_commands
from .welcome import setup_welcome_commands


def setup_commands(bot, user_reactions):
    setup_reaction_commands(bot, user_reactions)
    setup_utility_commands(bot)
    setup_fun_commands(bot)
    setup_moderation_commands(bot)
    setup_welcome_commands(bot)
