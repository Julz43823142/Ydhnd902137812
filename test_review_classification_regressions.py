"""Regression checks for honest local review classifications and provenance."""
import unittest
from unittest.mock import patch

import chess_play
from test_game_review import ReviewEngine


class ClassificationProvenance(unittest.TestCase):
    def test_engine_opening_move_is_not_claimed_as_theory(self):
        label = chess_play._detailed_move_classification(
            0.0, True, 0, 30, 15, best_score=30,
            ply_index=0, allow_book=True,
        )
        self.assertEqual(label, "best")

    def test_sound_sacrifice_requires_best_move(self):
        best = chess_play._detailed_move_classification(
            0.0, True, 330, 80, 0, best_score=80,
        )
        not_best = chess_play._detailed_move_classification(
            0.0, False, 330, 80, 0, best_score=80,
        )
        self.assertEqual(best, "brilliant")
        self.assertNotEqual(not_best, "brilliant")

    def test_analysis_metadata_reports_actual_coverage(self):
        engine = ReviewEngine(["e4", "e5", "Nf3"])
        with patch.object(chess_play, "_get_analysis_engine", return_value=engine):
            result = chess_play.analyse_game_moves(["e4", "e5", "Nf3"])
        self.assertEqual(result["requested_plies"], 3)
        self.assertEqual(result["skipped_plies"], 0)
        self.assertEqual(result["analysed_plies"], 3)
        self.assertEqual(result["engine_configuration"]["depth"], 18)
        self.assertIn("not Chess.com", result["accuracy_provenance"])
        self.assertTrue(all(row["classification"] != "book" for row in result["moves"]))


if __name__ == "__main__":
    unittest.main()
