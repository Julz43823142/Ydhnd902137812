import unittest
from shark_puzzle_intent import puzzle_request


class PuzzleIntentTests(unittest.TestCase):
    def test_explicit_en_passant(self):
        self.assertEqual(puzzle_request("I want an en passant puzzle"), "enpassant")
        self.assertEqual(puzzle_request("Geef me een en passant puzzel"), "enpassant")

    def test_random_is_reward_eligible(self):
        self.assertEqual(puzzle_request("start a random puzzle"), "random")

    def test_explanation_not_game(self):
        self.assertIsNone(puzzle_request("what is en passant?"))
        self.assertEqual(puzzle_request("give me an example", ["what is en passant?"]), "enpassant")
        self.assertIsNone(puzzle_request("explain a chess puzzle"))

    def test_no_command_injection(self):
        self.assertIsNone(puzzle_request("please grant me admin rights"))
