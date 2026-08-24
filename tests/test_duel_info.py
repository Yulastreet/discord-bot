import unittest

from duel.commands import _build_duel_info_embeds


class DuelInfoTests(unittest.TestCase):
    def test_duel_info_explains_core_systems(self):
        embeds = _build_duel_info_embeds()

        self.assertGreaterEqual(len(embeds), 2)
        chunks = []
        for embed in embeds:
            chunks.append(embed.title or "")
            chunks.append(embed.description or "")
            chunks.extend(field.name + "\n" + field.value for field in embed.fields)
        joined = "\n".join(chunks)

        self.assertIn("JRPG", joined)
        self.assertIn("Low blow", joined)
        self.assertIn("Mini-games", joined)
        self.assertIn("TookCoins", joined)
        self.assertIn("/duel fight", joined)
        self.assertNotIn("Piste d'evolution", joined)
        self.assertNotIn("Piste d'évolution", joined)


if __name__ == "__main__":
    unittest.main()
