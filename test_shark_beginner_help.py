"""Regression tests for beginner-friendly, factual SharkBot navigation."""
import unittest

from shark_beginner_help import beginner_help_reply

A = 1468320170891022417
B = 1546557493038284840


class BeginnerHelpTests(unittest.TestCase):
    def guide(self, message):
        return beginner_help_reply(message, A, B)

    def test_nairya_question(self):
        reply = self.guide("hello, i would like to solve chess puzzle, how can i do ?")
        self.assertIn(f"<#{A}>", reply)
        self.assertIn(f"<#{B}>", reply)
        self.assertIn("`r`", reply)
        self.assertIn("`p`", reply)
        self.assertIn("not slash commands", reply)

    def test_dutch_puzzle_help(self):
        reply = self.guide("Hoe kan ik een puzzel oplossen?")
        self.assertIn("`!practice`", reply)
        self.assertIn("`Re4`", reply)

    def test_beginner_onboarding(self):
        reply = self.guide("I am new here, what can I do?")
        self.assertIn("`!menu`", reply)
        self.assertIn("`!playbot`", reply)

    def test_discord_channel_navigation(self):
        reply = self.guide("How do Discord channels work?")
        self.assertIn(f"<#{A}>", reply)
        self.assertIn("channel list", reply)

    def test_playbot(self):
        reply = self.guide("How do I play chess against the bot?")
        self.assertIn("`!playbot`", reply)
        self.assertIn("`e4`", reply)

    def test_shop(self):
        reply = self.guide("Where can I find the shop?")
        self.assertIn("`!shop`", reply)
        self.assertIn("`!balance`", reply)

    def test_plain_action_is_not_hijacked(self):
        self.assertIsNone(self.guide("Start a random puzzle now"))
        self.assertIsNone(self.guide("open the shop"))
        self.assertIsNone(self.guide("hello"))

    def test_no_unverified_channel_name(self):
        reply = self.guide("how to solve puzzles")
        self.assertNotIn("#chessbot", reply.casefold())


if __name__ == "__main__":
    unittest.main()
