"""Offline bot integration checks for ordinary-game PGN export controls."""
from types import SimpleNamespace
import unittest

import bot
from review_pgn_exports import PgnExportMode


class ReviewExportIntegration(unittest.TestCase):
    def test_imported_pgn_keeps_genuine_clocks_and_ignores_nags(self):
        game, moves = bot._parse_review_pgn(
            '[White "Synthetic"]\n[Black "Example"]\n[Result "*"]\n\n'
            '1. e4 $4 {Untrusted comment [%clk 0:05:00]} e5 *',
            SimpleNamespace(id=1, display_name="Synthetic"),
        )
        self.assertEqual(moves, ["e4", "e5"])
        plain, _ = bot._make_review_pgn_export(game, None, PgnExportMode.MOVES_ONLY)
        self.assertEqual(plain, "1. e4 e5 *")
        annotated, clocks = bot._make_review_pgn_export(
            game, {"moves": [{"classification": "best"}, {"classification": "good"}]},
            PgnExportMode.CLOCKS_AND_ANALYSIS,
        )
        self.assertTrue(clocks)
        self.assertIn("[%clk 0:05:00]", annotated)
        self.assertIn("Best", annotated)
        self.assertNotIn("Untrusted comment", annotated)
        self.assertNotIn("$4", annotated)

    def test_bot_game_does_not_fabricate_clocks(self):
        game = {
            "mode": "bot", "result": "1-0", "moves": ["e4", "e5"],
            "game_id": "synthetic", "white_name": "White", "black_name": "Black",
        }
        text, has_clocks = bot._make_review_pgn_export(
            game, None, PgnExportMode.WITH_CLOCKS,
        )
        self.assertFalse(has_clocks)
        self.assertNotIn("[%clk", text)

    def test_review_view_has_export_buttons_without_reanalysis(self):
        game = {"moves": ["e4", "e5"]}
        analysis = {"moves": [
            {"move": "1.e4", "classification": "best"},
            {"move": "1...e5", "classification": "good"},
        ]}
        view = bot.ChessGameReviewView(game, analysis)
        try:
            self.assertLessEqual(len(view.children), 25)
            labels = [item.label for item in view.children if hasattr(item, "label")]
            self.assertTrue({"Moves only", "With clocks", "Clocks + analysis"}.issubset(labels))
        finally:
            view.stop()

    def test_pre_analysis_controls_disable_annotations(self):
        view = bot.PGNExportView({"moves": ["e4", "e5"]})
        try:
            buttons = {item.label: item for item in view.children}
            self.assertFalse(buttons["Moves only"].disabled)
            self.assertFalse(buttons["With clocks"].disabled)
            self.assertTrue(buttons["Clocks + analysis"].disabled)
        finally:
            view.stop()


if __name__ == "__main__":
    unittest.main()
