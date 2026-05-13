import unicodedata


def parse_emoji_input(value, guild):
    """Return canonical unicode/custom emoji key for reaction-role mappings."""
    if not value:
        return None
    text = unicodedata.normalize("NFC", value.strip())
    for zw in ("\u200b", "\u200c", "\u2060", "\ufeff"):
        text = text.replace(zw, "")
    text = text.strip()
    if not text:
        return None
    if text.startswith("<") and text.endswith(">"):
        return text
    if text.startswith(":") and text.endswith(":") and len(text) > 2:
        name = text[1:-1]
        for emoji in guild.emojis:
            if emoji.name == name:
                return f"<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>"
        return None
    return text
