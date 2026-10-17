"""Themed puzzles must be legal, interactive and reward-free."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from shark_theme_puzzles import fetch_theme_puzzle


class ThemedPuzzleTests(unittest.TestCase):
    def test_en_passant_is_selected_from_real_solution_moves(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pool.sqlite3")
            with sqlite3.connect(path) as con:
                con.execute("CREATE TABLE puzzles (puzzle_id TEXT, fen TEXT, moves TEXT, rating INTEGER, band INTEGER)")
                con.execute("INSERT INTO puzzles VALUES (?, ?, ?, ?, ?)", (
                    "real_ep", "4k3/3p4/8/4P3/8/8/8/4K3 b - - 0 1",
                    "d7d5 e5d6", 1200, 0,
                ))
            with patch("shark_theme_puzzles.band_row_ids", return_value=[1]):
                puzzle = fetch_theme_puzzle(path, ((1200, 1499),), "enpassant", max_candidates=6)
            self.assertEqual(puzzle["lichess_id"], "real_ep")
            self.assertEqual(puzzle["pgn"], "exd6")
            self.assertIn(" d6 ", puzzle["fen"])

    def test_rejects_unsupported_theme(self):
        with self.assertRaises(ValueError):
            fetch_theme_puzzle("/not/a/pool", ((1200, 1499),), "anything")
