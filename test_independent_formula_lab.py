"""Offline tests for independent, resumable original-data Stockfish research."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts import independent_formula_lab as lab

NOW = datetime(2026, 10, 16, 13, 0, tzinfo=timezone.utc)


def mock_engine(san, **kwargs):
    check = kwargs.get("interrupt_check")
    if check:
        check()
    return {"analysed_plies": len(san), "truncated": False,
            "white": {"accuracy": 85.5},
            "black": {"accuracy": 82.0},
            "moves": [
                {"side": "white" if i % 2 == 0 else "black",
                 "win_loss_pct": (i * 7) % 10,
                 "classification": "good",
                 "is_best": i % 2 == 0, "sacrifice_cp": 0}
                for i in range(len(san))]}


class IndependentGeneration(unittest.TestCase):
    def test_games_are_original_deterministic_legal(self):
        a = lab.own_game(0)
        b = lab.own_game(0)
        c = lab.own_game(1)
        self.assertEqual(a, b)
        self.assertNotEqual(a["sha256"], c["sha256"])
        board = __import__("chess").Board()
        for san in a["san"]:
            board.push_san(san)
        self.assertLessEqual(len(a["san"]), 48)

    def test_only_own_stockfish_scores_are_used(self):
        game = lab.own_game(0)
        d = lab.evaluate_game(game, "depth18", analyze=mock_engine)
        f = lab.evaluate_game(game, "fast", analyze=mock_engine)
        self.assertTrue(lab.validate_metric(d, "depth18", game))
        self.assertTrue(lab.validate_metric(f, "fast", game))
        self.assertEqual(d["reference_scores"]["white"], 85.5)
        self.assertNotIn("chesscom", d)
        self.assertEqual(len(d["moves"]), len(game["san"]))
        corrupt = dict(d, sha256="not-the-game")
        self.assertFalse(lab.validate_metric(corrupt, "depth18", game))

    def test_incomplete_engine_game_never_cached(self):
        game = lab.own_game(2)
        def incomplete(san, **kwargs):
            result = mock_engine(san, **kwargs)
            result["analysed_plies"] -= 1
            return result
        with self.assertRaisesRegex(ValueError, "incomplete"):
            lab.evaluate_game(game, "fast", analyze=incomplete)

    def test_resume_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = lab.run(tmp, mode="depth18", now_fn=lambda: NOW,
                            analyze=mock_engine, max_games=1, trials=0)
            second = lab.run(tmp, mode="depth18", now_fn=lambda: NOW,
                             analyze=mock_engine, max_games=1, trials=0)
            third = lab.run(tmp, mode="fast", now_fn=lambda: NOW,
                            analyze=mock_engine, max_games=2, trials=0)
            self.assertEqual([first["new_completed_games"], second["new_completed_games"],
                              third["new_completed_games"]], [1, 1, 2])
            self.assertEqual(third["paired_games"], 2)
            self.assertTrue(third["no_chesscom_requests"])
            self.assertTrue(third["no_production_changes"])
            self.assertEqual(len(list((Path(tmp) / "metrics" / "depth18").glob("*.json"))), 2)

    def test_atomic_json_and_mode_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            r1 = lab.run(tmp, now_fn=lambda: NOW, analyze=mock_engine, max_games=1, trials=0)
            r2 = lab.run(tmp, now_fn=lambda: NOW, analyze=mock_engine, max_games=1, trials=0)
            self.assertEqual((r1["mode"], r2["mode"]), ("depth18", "fast"))
            self.assertEqual(lab.load_json(Path(tmp) / "run_counter.json")["runs"], 2)


class Training(unittest.TestCase):
    def test_train_only_selection_and_holdout(self):
        pairs = []
        for i in range(100):
            game = lab.own_game(i)
            fast = lab.evaluate_game(game, "fast", analyze=mock_engine)
            deep = lab.evaluate_game(game, "depth18", analyze=mock_engine)
            pairs.append((deep, fast))
        train, holdout = lab.stable_split(pairs)
        self.assertGreaterEqual(len(train), 10)
        self.assertGreaterEqual(len(holdout), 3)
        fit = lab.search_formulas(pairs, trials=5, seed=42)
        self.assertEqual(fit["status"], "evaluated")
        self.assertEqual(fit["train_games"] + fit["holdout_games"], 100)
        self.assertIn("candidate_holdout_mae", fit)
        self.assertTrue(fit["no_chesscom_data"])
        self.assertEqual(lab.stable_split(list(reversed(pairs)))[0][0][0]["id"]
                         in {x[0]["id"] for x in train}, True)

    def test_not_enough_paired_games_is_explicit(self):
        result = lab.search_formulas([], trials=0)
        self.assertEqual(result["status"], "insufficient_paired_original_games")


if __name__ == "__main__":
    unittest.main()
