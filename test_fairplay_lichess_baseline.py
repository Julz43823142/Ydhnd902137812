"""Local Lichess research baseline regression tests (no real accounts)."""
import unittest
import chess
import chess.pgn
from scripts.benchmark_lichess_baseline import bracket, examine


def synthetic_lichess(*, control="300+0", white_rating="1540", black_rating="1650", variant="Standard"):
    game = chess.pgn.Game()
    game.headers.update({"White": "synthetic-private-identifier",
                         "Black": "synthetic-secret",
                         "Variant": variant, "WhiteElo": white_rating,
                         "BlackElo": black_rating, "Rated": "True",
                         "TimeControl": control})
    board = game.board()
    node = game
    for ply in range(30):
        if board.is_game_over():
            break
        move = next(iter(board.legal_moves))
        node = node.add_variation(move)
        node.comment = f"[%clk 0:04:{59 - ply:02}]"
        board.push(move)
    return game


class LichessOffline(unittest.TestCase):
    def test_rating_bands(self):
        self.assertEqual(bracket("1573"), "1400-1599")
        self.assertIsNone(bracket("unknown"))

    def test_synthetic_local_pgn_is_grouped_without_names(self):
        data = examine([synthetic_lichess()])
        self.assertEqual(data["games_read"], 1)
        self.assertEqual(len(data["groups"]), 2)
        self.assertEqual({x["time_control"] for x in data["groups"]}, {"300+0"})
        self.assertEqual({x["rating_band"] for x in data["groups"]},
                         {"1400-1599", "1600-1799"})
        self.assertNotIn("synthetic-secret", str(data))
        self.assertNotIn("synthetic-private-identifier", str(data))

    def test_study_is_not_a_fair_play_classification(self):
        result = examine([synthetic_lichess(variant="Chess960"),
                          synthetic_lichess(control="300+0"),
                          synthetic_lichess(control="600+5")])
        self.assertEqual(len(result["groups"]), 4)
        self.assertNotIn("priority", result)
        self.assertNotIn("is_cheating", str(result))
        self.assertIn("neither clean labels nor evidence", result["source"])


if __name__ == "__main__":
    unittest.main()
