import sqlite3
import unittest

from owner_settings_utils import update_seasonal_sabre_name
from web_profile import build_user_profile_payload


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE users (
            guild_id TEXT,
            user_id TEXT,
            username TEXT,
            level INTEGER,
            xp INTEGER
        );
        CREATE TABLE guilds (
            guild_id TEXT,
            name TEXT
        );
        CREATE TABLE guild_members (
            guild_id TEXT,
            user_id TEXT,
            username TEXT,
            avatar_url TEXT,
            updated_at TEXT
        );
        CREATE TABLE dm_messages (
            user_id TEXT,
            username TEXT,
            avatar_url TEXT,
            ts TEXT
        );
        CREATE TABLE logs (
            guild_id TEXT,
            type TEXT,
            ts TEXT,
            user_id TEXT,
            channel_id TEXT,
            channel_name TEXT
        );
        CREATE TABLE duel_profil (
            user_id TEXT,
            username TEXT,
            combat_level INTEGER,
            tookcoins INTEGER,
            victoires INTEGER,
            defaites INTEGER,
            sabre_equipe TEXT
        );
        """
    )
    return db


class WebProfileTests(unittest.TestCase):
    def test_owner_profile_falls_back_to_global_user_outside_selected_guild(self):
        db = make_db()
        db.execute("INSERT INTO guilds VALUES ('g2', 'Other')")
        db.execute("INSERT INTO users VALUES ('g2', '42', 'Tookyn', 7, 1234)")
        db.execute(
            "INSERT INTO guild_members VALUES ('g2', '42', 'Tookyn', 'https://cdn/avatar.png', '2026-05-14')"
        )
        db.execute("INSERT INTO logs VALUES ('g2', 'command', datetime('now'), '42', 'c1', 'general')")
        db.commit()

        payload = build_user_profile_payload(db, "42", guild_id="selected", is_owner=True)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["scope"], "global")
        self.assertEqual(payload["user"]["username"], "Tookyn")
        self.assertEqual(payload["user"]["avatar_url"], "https://cdn/avatar.png")
        self.assertEqual(payload["user"]["xp"], 1234)
        self.assertEqual(payload["guilds"][0]["guild_name"], "Other")
        self.assertEqual(payload["type_counts"][0]["type"], "command")

    def test_non_owner_profile_remains_limited_to_selected_guild(self):
        db = make_db()
        db.execute("INSERT INTO users VALUES ('g2', '42', 'Tookyn', 7, 1234)")
        db.commit()

        payload = build_user_profile_payload(db, "42", guild_id="selected", is_owner=False)

        self.assertIsNone(payload)

    def test_seasonal_sabre_name_update_only_allows_seasonal_ids(self):
        calls = []

        def get_sabre(sabre_id):
            return {"id": sabre_id, "nom": "Old"} if sabre_id == "season_2026-05_R" else None

        def update_sabre(sabre_id, data):
            calls.append((sabre_id, data))
            return True

        ok = update_seasonal_sabre_name(get_sabre, update_sabre, "season_2026-05_R", "  New Name  ")

        self.assertTrue(ok)
        self.assertEqual(calls, [("season_2026-05_R", {"nom": "New Name"})])
        with self.assertRaises(ValueError):
            update_seasonal_sabre_name(get_sabre, update_sabre, "bleu", "Nope")


if __name__ == "__main__":
    unittest.main()
