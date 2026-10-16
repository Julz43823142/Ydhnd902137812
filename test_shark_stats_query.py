"""Read-only Discord gameplay statistics: identity and data integrity."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from shark_stats_query import answer, _resolve, _metric, _target


def member(uid, name):
    return SimpleNamespace(id=uid, display_name=name, name=name, bot=False)


class FakeGuild:
    def __init__(self):
        self.members = [member(123, "Thijs"), member(456, "Julie")]
    def get_member(self, uid):
        return next((m for m in self.members if m.id == uid), None)


class PublicStatsTests(unittest.TestCase):
    def setUp(self):
        self.guild = FakeGuild()
        self.author = self.guild.members[1]

    def test_name_lookup_no_id_in_reply(self):
        with patch("pets.get_owner", return_value={"pets": [
            {"id": "a", "death_cause": "murder", "killed_by": "123", "died_at": 1},
            {"id": "b", "death_cause": "murder", "killed_by": "456", "died_at": 2},
            {"id": "c", "died_at": 3},
            {"id": "d", "species": "Cat"},
        ]}):
            response = answer("How many animals did Thijs kill?", self.guild, self.author)
        self.assertIn("Thijs", response)
        self.assertIn("**1**", response)
        self.assertNotIn("123", response)

    def test_dutch_pet_kills(self):
        with patch("pets.get_owner", return_value={"pets": [
            {"death_cause": "murder", "killed_by": "123", "died_at": 1},
        ]}):
            self.assertIn("**1**", answer("Hoeveel dieren heeft Thijs gekild?", self.guild, self.author))

    def test_coins_are_authoritative(self):
        with patch("shared_leaderboard.get_cosmetic_profile", return_value={"coins": 19.0}):
            response = answer("How many coins does Thijs have?", self.guild, self.author)
        self.assertIn("Thijs", response)
        self.assertIn("**19**", response)

    def test_unknown_member_not_fabricated(self):
        with patch("shared_leaderboard.resolve_cosmetic_profile", side_effect=ValueError("Missing")):
            response = answer("How many coins does Stranger have?", self.guild, self.author)
        self.assertIn("couldn't find", response)

    def test_duplicate_names_are_rejected(self):
        self.guild.members.append(member(789, "Thijs"))
        result = _resolve(self.guild, self.author, "Thijs")
        self.assertIn("Multiple players", result)

    def test_unrelated_is_not_intercepted(self):
        self.assertIsNone(answer("Teach me a chess opening", self.guild, self.author))
        self.assertEqual(_metric("How many animals did Thijs kill?"), "killed")
        self.assertEqual(_target("How many animals did Thijs kill?", "killed"), "Thijs")


if __name__ == "__main__":
    unittest.main()
