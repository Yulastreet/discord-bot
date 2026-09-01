import unittest

from duel.commands import _build_duel_info_panels


def _texts(component):
    """Collect every text block of a rendered Components V2 payload."""
    out = []
    if isinstance(component, dict):
        if component.get("type") == 10:          # TextDisplay
            out.append(component.get("content", ""))
        for value in component.values():
            out.extend(_texts(value))
    elif isinstance(component, list):
        for value in component:
            out.extend(_texts(value))
    return out


class DuelInfoTests(unittest.TestCase):
    def test_duel_info_explains_core_systems(self):
        panels = _build_duel_info_panels()

        self.assertGreaterEqual(len(panels), 2)
        chunks = []
        for panel in panels:
            chunks.extend(_texts(panel.container().to_component_dict()))
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
