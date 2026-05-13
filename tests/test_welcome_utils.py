import unittest

from welcome_utils import build_welcome_send_kwargs, format_welcome_message


class FakeGuild:
    name = "Serveur Test"
    member_count = 42


class FakeMember:
    mention = "<@123>"
    name = "Tookyn"
    display_name = "Tookyn"
    guild = FakeGuild()


class WelcomeUtilsTest(unittest.TestCase):
    def test_format_welcome_message_replaces_pseudo_shortcut(self):
        self.assertEqual(
            format_welcome_message("Coucou @pseudo sur {guild} !", FakeMember()),
            "Coucou <@123> sur Serveur Test !",
        )

    def test_format_welcome_message_keeps_existing_user_placeholder(self):
        self.assertEqual(
            format_welcome_message("Bienvenue {user}, membre {count}", FakeMember()),
            "Bienvenue <@123>, membre 42",
        )

    def test_build_welcome_send_kwargs_uses_plain_content_not_embed(self):
        kwargs = build_welcome_send_kwargs("Coucou @pseudo", FakeMember())

        self.assertEqual(kwargs, {"content": "Coucou <@123>"})


if __name__ == "__main__":
    unittest.main()
