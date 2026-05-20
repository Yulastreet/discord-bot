from .fun import setup_fun_commands
from .music import setup_music_commands
from .niveaux import setup_niveau_commands
from .pass_cmd import setup_pass_commands
from .rolereaction import setup_rolereaction_commands
from .socialalert import setup_socialalert_commands
from .tickets import setup_ticket_commands
from .moderation import setup_moderation_commands
from .reactions import setup_reaction_commands
from .utilitaires import setup_utility_commands
from .welcome import setup_welcome_commands
from .setup_cmd import setup_setup_commands


def setup_commands(bot, user_reactions, deps):
    setup_reaction_commands(bot, user_reactions)
    setup_utility_commands(bot)
    setup_fun_commands(bot)
    setup_moderation_commands(bot)
    setup_welcome_commands(bot)
    setup_setup_commands(bot)
    setup_niveau_commands(bot, deps)
    setup_rolereaction_commands(bot, deps)
    setup_ticket_commands(bot, deps)
    setup_socialalert_commands(bot, deps)
    setup_pass_commands(bot, deps)
    resume_music = setup_music_commands(bot, deps)
    return {"resume_music": resume_music}
