"""Offline regression coverage for three ordinary-review PGN exports."""
import io
import unittest

import chess.pgn
from review_pgn_exports import PgnExportMode, export_review_pgn, has_move_clocks


def read_game(text):
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None or game.errors:
        raise AssertionError("Invalid test PGN")
    return game


class ReviewPgnExportTests(unittest.TestCase):
    def setUp(self):
        self.game = read_game(
            '[Event "Synthetic review fixture"]\n'
            '[Result "*"]\n\n'
            '1. e4 $4 {Imported accusation [%clk 0:03:00]} '
            '(1. d4 d5) e5 {[%clk 0:02:59]} 2. Nf3 Nc6 *'
        )

    def test_moves_only_strips_headers_clocks_annotations_and_variations(self):
        text = export_review_pgn(self.game, PgnExportMode.MOVES_ONLY)
        self.assertEqual(text, "1. e4 e5 2. Nf3 Nc6 *")
        parsed = read_game(text)
        self.assertEqual(len(list(parsed.mainline_moves())), 4)
        self.assertTrue(all(not node.nags for node in parsed.mainline()))

    def test_clocks_are_source_only(self):
        self.assertTrue(has_move_clocks(self.game))
        text = export_review_pgn(self.game, PgnExportMode.WITH_CLOCKS)
        nodes = list(read_game(text).mainline())
        self.assertEqual([node.clock() for node in nodes], [180.0, 179.0, None, None])
        self.assertNotIn("Imported accusation", text)
        self.assertTrue(all(not node.nags and len(node.variations) <= 1 for node in nodes))

    def test_analysis_uses_independent_labels(self):
        text = export_review_pgn(
            self.game, PgnExportMode.CLOCKS_AND_ANALYSIS,
            labels={1: "best", 2: "inaccuracy"},
        )
        nodes = list(read_game(text).mainline())
        self.assertIn("Best", nodes[0].comment)
        self.assertIn("Inaccuracy", nodes[1].comment)
        self.assertNotIn("Imported accusation", text)
        self.assertFalse(nodes[0].nags)

    def test_source_unchanged_after_all_exports(self):
        before = str(self.game)
        for mode in PgnExportMode:
            export_review_pgn(self.game, mode, labels={1: "good"})
        self.assertEqual(before, str(self.game))
        self.assertIn("Imported accusation", self.game.next().comment)

    def test_no_clocks_invented_from_time_control(self):
        game = read_game('[TimeControl "180+1"]\n[Result "*"]\n\n1. d4 d5 2. c4 *')
        self.assertFalse(has_move_clocks(game))
        text = export_review_pgn(
            game, PgnExportMode.CLOCKS_AND_ANALYSIS, labels={1: "good"}
        )
        self.assertNotIn("[%clk", text)
        self.assertIn("Good", text)

    def test_custom_black_to_move_start(self):
        game = read_game(
            '[SetUp "1"]\n'
            '[FEN "8/8/8/8/8/8/4k3/6K1 b - - 0 42"]\n'
            '[Result "*"]\n\n42... Kf3 *'
        )
        text = export_review_pgn(game, PgnExportMode.MOVES_ONLY)
        parsed = read_game(text)
        self.assertEqual(parsed.board().fen(), game.board().fen())
        self.assertEqual(list(parsed.mainline_moves()), list(game.mainline_moves()))
        self.assertIn("42...", text)

    def test_result_preserved(self):
        game = read_game('[Result "1-0"]\n\n1. e4 e5 1-0')
        text = export_review_pgn(game, PgnExportMode.MOVES_ONLY)
        self.assertTrue(text.endswith("1-0"))

    def test_invalid_labels_rejected(self):
        for ply in (0, 5, True):
            with self.subTest(ply=ply):
                with self.assertRaises(ValueError):
                    export_review_pgn(
                        self.game, PgnExportMode.CLOCKS_AND_ANALYSIS,
                        labels={ply: "best"},
                    )
        with self.assertRaises(ValueError):
            export_review_pgn(
                self.game, PgnExportMode.CLOCKS_AND_ANALYSIS,
                labels={1: "confirmed cheating"},
            )


if __name__ == "__main__":
    unittest.main()
