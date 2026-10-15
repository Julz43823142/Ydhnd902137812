"""Regression tests for ordinary review score POV, mates and game accuracy."""
import math
import unittest
from unittest.mock import patch

import chess
import chess.engine
import chess_play
from test_game_review import ReviewEngine


class GameReviewEvaluationAudit(unittest.TestCase):
    def test_score_perspective_is_consistent_for_both_sides(self):
        info = {"score": chess.engine.PovScore(chess.engine.Cp(230), chess.WHITE)}
        self.assertEqual(chess_play._engine_score_cp(info, chess.WHITE), 230)
        self.assertEqual(chess_play._engine_score_cp(info, chess.BLACK), -230)
        black_mate = {"score": chess.engine.PovScore(chess.engine.Mate(3), chess.BLACK)}
        self.assertGreater(chess_play._engine_score_cp(black_mate, chess.BLACK), 90000)
        self.assertLess(chess_play._engine_score_cp(black_mate, chess.WHITE), -90000)

    def test_game_accuracy_all_best_is_perfect_for_white_and_black(self):
        moves = ["e4", "e5", "Nf3", "Nc6"]
        fake_engine = ReviewEngine(moves)
        with patch.object(chess_play, "_get_analysis_engine", return_value=fake_engine):
            result = chess_play.analyse_game_moves(moves)
        self.assertEqual(result["white"]["accuracy"], 100)
        self.assertEqual(result["black"]["accuracy"], 100)
        self.assertEqual(result["skipped_plies"], 0)
        self.assertEqual(result["analysed_plies"], 4)
        self.assertEqual(result["analysis_version"], "ordinary-review-v7")
        self.assertGreaterEqual(result["analysis_elapsed_seconds"], 0)

    def test_checkmate_has_sensible_terminal_metrics(self):
        moves = ["f3", "e5", "g4", "Qh4#"]
        fake_engine = ReviewEngine(moves)
        with patch.object(chess_play, "_get_analysis_engine", return_value=fake_engine):
            result = chess_play.analyse_game_moves(moves)
        terminal = result["moves"][-1]
        self.assertEqual(terminal["eval_white_mate"], 0)
        self.assertLess(terminal["eval_white_cp"], 0)
        self.assertTrue(0 <= result["white"]["accuracy"] <= 100)
        self.assertTrue(0 <= result["black"]["accuracy"] <= 100)
        self.assertTrue(math.isfinite(result["white"]["accuracy"]))

    def test_winning_position_cp_loss_changes_chances_less_than_equal_position(self):
        early = (chess_play._win_percent_from_cp(0) -
                 chess_play._win_percent_from_cp(-150))
        decided = (chess_play._win_percent_from_cp(850) -
                   chess_play._win_percent_from_cp(700))
        self.assertLess(decided, early)

    def test_forced_moves_and_uncertain_sacrifice_not_brilliant(self):
        forced = chess_play._detailed_move_classification(
            0, True, 0, 50, None, best_score=50, allow_book=False,
        )
        unsound = chess_play._detailed_move_classification(
            25, False, 500, -500, 0, best_score=200, allow_book=False,
        )
        self.assertEqual(forced, "best")
        self.assertNotEqual(unsound, "brilliant")


if __name__ == "__main__":
    unittest.main()
