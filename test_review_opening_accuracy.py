"""Isolated ordinary-review opening labels and CAPS2-inspired aggregation tests."""
import unittest
from unittest.mock import patch

import chess
import chess_play
from ordinary_review_opening import _known_opening_prefixes, is_known_opening_move
from test_game_review import ReviewEngine


class OpeningBookAndAccuracyTests(unittest.TestCase):
    def review(self, moves, **options):
        with patch.object(chess_play, "_get_analysis_engine", return_value=ReviewEngine(moves)):
            return chess_play.analyse_game_moves(moves, **options)

    def test_common_standard_opening_uses_real_public_theory(self):
        self.assertTrue(is_known_opening_move((), "e2e4"))
        self.assertTrue(is_known_opening_move(("e2e4",), "e7e5"))
        result = self.review(["e4", "e5", "Nf3", "Nc6"])
        self.assertEqual([row["classification"] for row in result["moves"]],
                         ["book"] * 4)
        self.assertEqual(result["white"]["books"], 2)
        self.assertEqual(result["black"]["books"], 2)
        self.assertEqual(result["white"]["accuracy"], 100.0)
        self.assertEqual(result["black"]["accuracy"], 100.0)

    def test_missing_reference_does_not_invent_book(self):
        with patch("ordinary_review_opening._known_opening_prefixes", return_value=frozenset()):
            result = self.review(["e4", "e5"])
        self.assertEqual([r["classification"] for r in result["moves"]], ["best", "best"])

    def test_theory_never_resumes_after_out_of_book_move(self):
        moves = ["e4", "e5", "Ke2", "Nc6"]
        reference = frozenset({
            ("e2e4",), ("e2e4", "e7e5"),
            ("e2e4", "e7e5", "g1f3"),
            ("e2e4", "e7e5", "g1f3", "b8c6"),
        })
        with patch("ordinary_review_opening._known_opening_prefixes", return_value=reference):
            result = self.review(moves)
        self.assertEqual([r["classification"] for r in result["moves"]],
                         ["book", "book", "best", "best"])

    def test_custom_start_and_chess960_have_no_book(self):
        custom = self.review(["Kd7", "Kf2"],
                             start_fen="4k3/4p3/8/8/8/8/4P3/4K3 b - - 0 37")
        self.assertEqual(custom["black"]["books"], 0)
        chess960 = self.review(["e4", "e5"], chess960=True)
        self.assertTrue(all(row["classification"] != "book" for row in chess960["moves"]))

    def test_a_single_zero_move_does_not_zero_harmonic_half(self):
        rows = [{"mover_color": chess.WHITE, "move_accuracy": v,
                 "classification": "best"} for v in [100.0] * 9 + [0.0]]
        score = chess_play._game_accuracy_from_moves(rows, [50.0] * 11, chess.WHITE)
        self.assertEqual(score, 90.0)

    def test_theory_moves_excluded_from_out_of_book_accuracy(self):
        rows = [
            {"mover_color": chess.BLACK, "move_accuracy": 5.0, "classification": "book"},
            {"mover_color": chess.BLACK, "move_accuracy": 80.0, "classification": "good"},
        ]
        score = chess_play._game_accuracy_from_moves(rows, [50.0, 50.0, 50.0], chess.BLACK)
        self.assertEqual(score, 80.0)
        self.assertEqual(chess_play._game_accuracy_from_moves(rows[:1], [50.0, 50.0], chess.BLACK), 100.0)

    def test_missing_eval_positions_have_bounded_arithmetic_fallback(self):
        rows = [{"mover_color": chess.WHITE, "move_accuracy": 200.0,
                 "classification": "best"}]
        self.assertEqual(chess_play._game_accuracy_from_moves(rows, [], chess.WHITE), 100.0)


if __name__ == "__main__":
    unittest.main()
