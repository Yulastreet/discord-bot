import asyncio


async def _cleanup_voice_client(bot, guild, voice_client):
    if voice_client:
        try:
            await voice_client.disconnect(force=True)
        except Exception:
            pass

    state = getattr(bot, "_connection", None)
    remove_voice_client = getattr(state, "_remove_voice_client", None)
    if callable(remove_voice_client):
        try:
            remove_voice_client(guild.id)
        except Exception:
            pass


async def connect_to_voice(
    bot,
    guild,
    channel,
    *,
    attempts=2,
    timeout=12,
    reconnect=False,
    sleep=asyncio.sleep,
):
    last_error = None

    for attempt in range(1, attempts + 1):
        voice_client = guild.voice_client
        if voice_client and voice_client.is_connected():
            if voice_client.channel and voice_client.channel.id != channel.id:
                await voice_client.move_to(channel)
            return voice_client

        if voice_client:
            await _cleanup_voice_client(bot, guild, voice_client)

        try:
            return await channel.connect(timeout=timeout, reconnect=reconnect)
        except Exception as exc:
            last_error = exc
            await _cleanup_voice_client(bot, guild, guild.voice_client)
            print(f"[music voice] connect attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}")
            if attempt < attempts:
                await sleep(2)

    raise last_error
