"""Regression checks for SharkBot 2.15 natural-language actions and follow-ups."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import shark_commands as commands
import shark_stats_query as stats


class Release215Tests(unittest.TestCase):
    def setUp(self):
        stats._CONTEXT.clear()

    def test_free_claim_is_explicit_and_safe(self):
        for phrase in ("open box", "please open my daily box", "claim box",
                       "pak mijn box", "claim my free daily mystery box"):
            self.assertTrue(commands.free_box_claim_request(phrase), phrase)
        for phrase in ("do not open box", "open box for Thijs",
                       "how do I open box?", "buy paid box", "open box?"):
            self.assertFalse(commands.free_box_claim_request(phrase), phrase)

    def test_trade_is_direct_but_purchases_are_not(self):
        self.assertFalse(commands._needs_confirmation("!accepttrade"))
        self.assertFalse(commands._needs_confirmation("!declinetrade"))
        for cmd in ("!donate Thijs 10", "!play Thijs", "!resign"):
            self.assertTrue(commands._needs_confirmation(cmd))

    def test_unique_badges_followup(self):
        me = SimpleNamespace(id=10, display_name="Tester", name="Tester", bot=False)
        thijs = SimpleNamespace(id=20, display_name="Thijs", name="Thijs", bot=False)
        guild = SimpleNamespace(id=99, members=[me, thijs],
                                get_member=lambda uid: {10: me, 20: thijs}.get(uid))
        with patch("shared_leaderboard.get_cosmetic_profile",
                   return_value={"badges": ["A", "B", "A"]}):
            first = stats.answer("How many badges does Thijs have?", guild, me, 123)
            second = stats.answer("en hoeveel unieke?", guild, me, 123)
        self.assertIn("3", first)
        self.assertIn("2", second)
        self.assertIn("Thijs", second)
        self.assertIsNone(stats.answer("en hoeveel unieke?", guild, me, 456))

    def test_memorial_followup(self):
        me = SimpleNamespace(id=10, display_name="Tester", name="Tester", bot=False)
        thijs = SimpleNamespace(id=20, display_name="Thijs", name="Thijs", bot=False)
        guild = SimpleNamespace(id=99, members=[me, thijs],
                                get_member=lambda uid: {10: me, 20: thijs}.get(uid))
        with patch.object(stats, "_pet_totals", return_value={
            "pets": 5, "dead": 3, "starved": 2, "killed": 1,
            "rarest": None, "strongest": None
        }):
            first = stats.answer("How many pets does Thijs have?", guild, me, 123)
            second = stats.answer("en hoeveel dode?", guild, me, 123)
        self.assertIn("5", first)
        self.assertIn("3", second)


if __name__ == "__main__":
    unittest.main()
