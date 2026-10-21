import unittest
from shark_public_help import public_help_reply, CHESS_PUZZLE_HELP, MINIGAMES_HELP
class PublicHelpTests(unittest.TestCase):
    def test_chess_channels_and_real_commands(self):
        r=public_help_reply("How do I start chess puzzles?")
        self.assertEqual(r,CHESS_PUZZLE_HELP)
        self.assertIn("<#1468320170891022417>",r)
        self.assertIn("<#1546557493038284840>",r)
        self.assertIn("!practice",r)
    def test_dutch_and_minigame(self):
        self.assertEqual(public_help_reply("Waar kan ik schaakpuzzels starten?"),CHESS_PUZZLE_HELP)
        blackjack=public_help_reply("How to play blackjack?")
        trivia=public_help_reply("Hoe start ik trivia?")
        self.assertIn("!blackjack",blackjack)
        self.assertIn("Hit/Stand/Double/Split",blackjack)
        self.assertIn("!trivia",trivia)
        self.assertIn("one question",trivia.lower())
        self.assertIn("<#1546155761405788230>",trivia)
        self.assertEqual(public_help_reply("How do I start minigames?"),MINIGAMES_HELP)
        self.assertIn("!poker",public_help_reply("How do I play poker?"))
    def test_action_not_hijacked(self):
        self.assertIsNone(public_help_reply("Give me a chess puzzle"))
        self.assertIsNone(public_help_reply("17...Be6!!"))
        self.assertIsNone(public_help_reply("Start a chess puzzle."))
        self.assertIsNone(public_help_reply("Speel een schaakpuzzel"))
    def test_chessle_is_not_regular_chess(self):
        self.assertIn("!chessle",public_help_reply("Where to start chessle?"))
if __name__=="__main__":unittest.main()
