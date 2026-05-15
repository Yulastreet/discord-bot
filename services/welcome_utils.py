DEFAULT_WELCOME_MESSAGE = (
    "Bienvenue {user} !\n"
    "Bienvenue sur **{guild}** ! Tu es le membre numero **{count}**."
)


def format_welcome_message(template, member):
    """Render a guild welcome message for a Discord member."""
    text = (template or DEFAULT_WELCOME_MESSAGE).replace("@pseudo", "{user}")
    return text.format(
        user=member.mention,
        username=member.name,
        pseudo=getattr(member, "display_name", member.name),
        guild=member.guild.name,
        count=member.guild.member_count or 0,
    )


def build_welcome_send_kwargs(template, member):
    return {"content": format_welcome_message(template, member)}
