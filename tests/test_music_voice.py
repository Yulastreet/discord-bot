import unittest

from commandes.music_voice import connect_to_voice


class FakeState:
    def __init__(self):
        self.removed = []

    def _remove_voice_client(self, guild_id):
        self.removed.append(guild_id)


class FakeBot:
    def __init__(self):
        self._connection = FakeState()


class FakeVoiceClient:
    def __init__(self, channel=None, connected=False):
        self.channel = channel
        self._connected = connected
        self.disconnect_calls = 0
        self.move_to_calls = []

    def is_connected(self):
        return self._connected

    async def disconnect(self, force=False):
        self.disconnect_calls += 1
        self._connected = False

    async def move_to(self, channel):
        self.move_to_calls.append(channel)
        self.channel = channel


class FakeGuild:
    id = 123

    def __init__(self, voice_client=None):
        self.voice_client = voice_client


class FakeChannel:
    id = 456

    def __init__(self, guild, failures_before_success=0):
        self.guild = guild
        self.connect_calls = 0
        self.failures_before_success = failures_before_success

    async def connect(self, timeout=20, reconnect=True):
        self.connect_calls += 1
        if self.connect_calls <= self.failures_before_success:
            stale = FakeVoiceClient(channel=self, connected=False)
            self.guild.voice_client = stale
            raise RuntimeError("voice handshake failed")
        vc = FakeVoiceClient(channel=self, connected=True)
        self.guild.voice_client = vc
        return vc


async def no_sleep(_seconds):
    return None


class MusicVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_after_failed_handshake_and_clears_stale_client(self):
        bot = FakeBot()
        guild = FakeGuild()
        channel = FakeChannel(guild, failures_before_success=1)

        vc = await connect_to_voice(
            bot,
            guild,
            channel,
            attempts=2,
            sleep=no_sleep,
        )

        self.assertTrue(vc.is_connected())
        self.assertEqual(channel.connect_calls, 2)
        self.assertIn(guild.id, bot._connection.removed)

    async def test_moves_existing_connected_client(self):
        bot = FakeBot()
        old_channel = type("OldChannel", (), {"id": 1})()
        guild = FakeGuild(FakeVoiceClient(channel=old_channel, connected=True))
        new_channel = FakeChannel(guild)

        vc = await connect_to_voice(bot, guild, new_channel, sleep=no_sleep)

        self.assertIs(vc, guild.voice_client)
        self.assertEqual(vc.move_to_calls, [new_channel])
        self.assertEqual(new_channel.connect_calls, 0)


if __name__ == "__main__":
    unittest.main()
