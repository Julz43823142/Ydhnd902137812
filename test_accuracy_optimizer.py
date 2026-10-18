"""Synthetic reference-lab tests: no network, engines, production state or jobs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch
import zipfile

import chess
import chess.engine
from cryptography.fernet import Fernet, InvalidToken

from scripts import accuracy_optimizer as lab
from scripts.accuracy_optimizer_engine import BUDGETS, Budget, BudgetEngine
from scripts.accuracy_optimizer_engine import analyze_in_child
from scripts.collect_chesscom_accuracy import validate
from scripts.nightly_caps2_lab import atomic_json
from test_chesscom_reference_collector import example, OCT


ENGINE = {"binary_sha256": "synthetic-test-only", "threads": 1, "hash_mb": 64}


def reference(index):
    raw = example(index, w="SyntheticWhite", b="SyntheticBlack")
    raw["accuracies"] = {"white": 70 + index % 10, "black": 65 + index % 10}
    record, error = validate(raw, "syntheticwhite", OCT, "2026-10-18T00:00:00+00:00")
    assert error is None, error
    return record


def fake_engine(moves, budget, seconds):
    return {"truncated": False, "analysed_plies": len(moves),
            "engine": "Synthetic Stockfish", "engine_binary_sha256": ENGINE["binary_sha256"],
            "white": {"accuracy": 80.0}, "black": {"accuracy": 75.0},
            "initial_white_win_pct": 50.0,
            "research_search": {"budget": asdict(budget), "searches": len(moves),
                                "minimum_depth": budget.value if budget.kind == "depth" else 16},
            "moves": [{"side": "white" if i % 2 == 0 else "black", "win_loss_pct": 3.0,
                       "eval_white_cp": i * 5, "classification": "good"} for i in range(len(moves))]}


class Optimizer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / "state"
        self.dataset = self.base / "input.json"
        atomic_json(self.dataset, [reference(i) for i in range(1, 41)])
        lab.initialize(self.state, self.dataset)

    def run_lab(self, **kwargs):
        return lab.run(self.state, authorized=True, isolated=True, engine=ENGINE,
                       analyze=kwargs.pop("analyze", fake_engine), trials=0,
                       profiles=kwargs.pop("profiles", (Budget("depth", 18),)), **kwargs)

    def root(self):
        return next((self.state / "contracts").iterdir())

    def test_guards_never_start_engine_or_report_fake_error(self):
        def fail(*args):
            self.fail("No engine allowed")
        for authorized, isolated in ((False, False), (True, False), (False, True)):
            status = lab.run(self.state, authorized=authorized, isolated=isolated, analyze=fail)
            self.assertEqual(status["state"], "blocked")
            self.assertIsNone(status["current_production_formula"])
            self.assertIsNone(status["validation_improvement_points"])
        self.assertFalse((self.state / "contracts").exists())

    def test_missing_dataset_does_not_start_engine(self):
        status = lab.run(self.base / "empty", authorized=True, isolated=True,
                         analyze=lambda *a: self.fail("No engine allowed"))
        self.assertEqual(status["state"], "waiting_for_reference_dataset")

    def test_all_requested_profiles_and_one_second_exist(self):
        self.assertEqual(len(BUDGETS), 13)
        self.assertEqual(BUDGETS[1], Budget("time", 1))
        self.assertEqual(Budget("time", 1).limit().time, 1)
        self.assertEqual(Budget("depth", 15).limit().depth, 15)
        self.assertEqual(Budget("nodes", 50000).limit().nodes, 50000)
        for args in (("depth", 1), ("nodes", True), ("time", float("nan")), ("time", 99)):
            with self.assertRaises(ValueError):
                Budget(*args)

    def test_completed_panel_experiment_and_plaintext_status_privacy(self):
        status = self.run_lab()
        self.assertEqual(status["completed_experiments"], 1)
        self.assertEqual(status["state"], "idle")
        baseline = status["current_production_formula"]["overall"]
        self.assertGreaterEqual(baseline["games"], 5)
        self.assertEqual(baseline["player_scores"], baseline["games"] * 2)
        for name in ("status.json", "runs.json"):
            text = (self.state / name).read_text()
            for private in ("SyntheticWhite", "SyntheticBlack", "[%clk", "https://www.chess.com/game/", '"pgn"'):
                self.assertNotIn(private, text)
        report = next((self.root() / "experiments").glob("*.json"))
        record = lab.read_json(report)
        self.assertGreater(len(record["candidate_trials"]), 1)
        self.assertIsNone(record["cost"]["currency_amount"])

    def test_timeout_keeps_prior_games_and_resumes_without_duplicate_search(self):
        calls = []
        def interrupted(moves, budget, seconds):
            calls.append(1)
            if len(calls) == 3:
                raise TimeoutError()
            return fake_engine(moves, budget, seconds)
        first = self.run_lab(analyze=interrupted)
        self.assertEqual(first["state"], "paused_budget")
        self.assertEqual(first["completed_experiments"], 0)
        resumed = []
        def continue_engine(*args):
            resumed.append(1)
            return fake_engine(*args)
        second = self.run_lab(analyze=continue_engine)
        self.assertEqual(second["completed_experiments"], 1)
        self.assertEqual(len(resumed), 38)

    def test_crash_after_completion_does_not_duplicate_experiment(self):
        self.run_lab()
        queue = lab.read_json(self.root() / "queue.json")
        queue["round"] = 0  # simulate crash before the final round checkpoint
        atomic_json(self.root() / "queue.json", queue)
        status = self.run_lab(analyze=lambda *a: self.fail("Completed games must not be searched"))
        self.assertEqual(status["completed_experiments"], 1)

    def test_incomplete_games_are_not_checkpointed_and_retries_are_bounded(self):
        count = []
        def incomplete(*args):
            count.append(1)
            result = fake_engine(*args)
            result["analysed_plies"] -= 1
            return result
        for _ in range(4):
            status = self.run_lab(analyze=incomplete)
        self.assertEqual(status["completed_experiments"], 0)
        self.assertEqual(len(count), 40 * 3)
        self.assertEqual(list((self.root() / "metrics").glob("**/*.json")), [])

    def test_changed_binary_invalidates_every_cached_game(self):
        self.run_lab()
        other = {**ENGINE, "binary_sha256": "different"}
        calls = []
        def changed_engine(*args):
            calls.append(1)
            result = fake_engine(*args)
            result["engine_binary_sha256"] = "different"
            return result
        status = lab.run(self.state, authorized=True, isolated=True, engine=other,
                         analyze=changed_engine, trials=0, profiles=(Budget("depth", 18),))
        self.assertEqual(status["completed_experiments"], 1)
        self.assertEqual(len(calls), 40)

    def test_dataset_is_frozen_and_reordered_input_is_identical(self):
        values = lab.read_json(self.dataset)
        atomic_json(self.dataset, list(reversed(values)))
        lab.initialize(self.state, self.dataset)
        values[0]["chesscom"]["white"] = 1.0
        atomic_json(self.dataset, values)
        with self.assertRaisesRegex(ValueError, "frozen"):
            lab.initialize(self.state, self.dataset)

    def test_status_does_not_need_engine_or_unlock_running_worker(self):
        with lab.exclusive(self.state):
            with self.assertRaisesRegex(RuntimeError, "Another optimizer"):
                self.run_lab()
            with patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(lab.main(["status", "--state-dir", str(self.state)]), 0)

    def test_actual_depth_and_tampering_are_checked(self):
        self.run_lab()
        path = next((self.root() / "metrics").glob("**/*.json"))
        metric = lab.read_json(path)
        games = lab.load_dataset(self.state / "dataset.json")
        game = next(g for g in games if lab.game_id(g) == metric["id"])
        self.assertTrue(lab.valid_metric(metric, game, metric["contract"], Budget("depth", 18)))
        metric["search"]["minimum_depth"] = 17
        metric.pop("checksum")
        metric["checksum"] = lab.digest(metric)
        self.assertFalse(lab.valid_metric(metric, game, metric["contract"], Budget("depth", 18)))

    def test_corrupt_metric_is_recomputed_on_resume(self):
        self.run_lab()
        queue = lab.read_json(self.root() / "queue.json")
        queue["round"] = 0
        atomic_json(self.root() / "queue.json", queue)
        # Remove only the completed experiment to simulate a previously
        # interrupted fit, then corrupt one metric on disk.
        for path in (self.root() / "experiments").glob("*.json"):
            path.unlink()
        next((self.root() / "metrics").glob("**/*.json")).write_text("{broken")
        calls = []
        def engine(*args):
            calls.append(1)
            return fake_engine(*args)
        status = self.run_lab(analyze=engine)
        self.assertEqual(status["completed_experiments"], 1)
        self.assertEqual(len(calls), 1)

    def test_official_targets_do_not_enter_predictions(self):
        self.run_lab()
        metric = lab.read_json(next((self.root() / "metrics").glob("**/*.json")))
        prediction = lab.fitting.score(lab.fitting.prepare(metric), lab.fitting.BASELINE)
        metric["chesscom"] = {"white": 1.0, "black": 99.0}
        self.assertEqual(prediction, lab.fitting.score(lab.fitting.prepare(metric), lab.fitting.BASELINE))

    def test_fitting_chooses_training_not_validation(self):
        self.run_lab()
        records = [lab.read_json(p) for p in (self.root() / "metrics").glob("**/*.json")]
        first = lab.fit(records, 0, 0, time.monotonic() + 60)
        for row in records:
            if row["partition"] == "validation":
                row["chesscom"] = {"white": 0.0, "black": 0.0}
        second = lab.fit(records, 0, 0, time.monotonic() + 60)
        self.assertEqual(first["settings"], second["settings"])
        self.assertNotEqual(first["validation"], second["validation"])

    def test_empty_statistics_and_unequal_score_game_denominators(self):
        stats = lab.statistics_for([])
        self.assertIsNone(stats["mae"])
        self.assertIsNone(stats["within_points_fraction"]["2"])
        row = {"production_scores": {"white": 90, "black": 70}, "chesscom": {"white": 90, "black": 80}}
        stats = lab.statistics_for([row])
        self.assertEqual(stats["mae"], 5)
        self.assertEqual(stats["bias"], -5)
        self.assertEqual(stats["within_points_fraction"]["2"], .5)
        self.assertEqual(stats["both_players_within_points_fraction"]["2"], 0)

    def test_encrypted_state_roundtrip_and_wrong_key(self):
        key = Fernet.generate_key().decode()
        bundle = self.base / "state.enc"
        with patch.dict(os.environ, {"ACCURACY_OPTIMIZER_STATE_KEY": key}):
            lab.transport_state(self.state, bundle)
            self.assertNotIn(b"SyntheticWhite", bundle.read_bytes())
            destination = self.base / "restored"
            lab.transport_state(destination, bundle, restore=True)
            self.assertEqual(lab.read_json(destination / "dataset.json"), lab.read_json(self.state / "dataset.json"))
        with patch.dict(os.environ, {"ACCURACY_OPTIMIZER_STATE_KEY": Fernet.generate_key().decode()}):
            with self.assertRaises(InvalidToken):
                lab.transport_state(self.base / "wrong", bundle, restore=True)

    def test_authenticated_bundle_cannot_escape_state_directory(self):
        key = Fernet.generate_key()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("../escape.json", "{}")
        bundle = self.base / "malformed.enc"
        bundle.write_bytes(Fernet(key).encrypt(data.getvalue()))
        with patch.dict(os.environ, {"ACCURACY_OPTIMIZER_STATE_KEY": key.decode()}):
            with self.assertRaisesRegex(ValueError, "Invalid state"):
                lab.transport_state(self.base / "restore", bundle, restore=True)
        self.assertFalse((self.base / "escape.json").exists())


class BudgetAdapter(unittest.TestCase):
    def test_child_deadline_terminates_only_its_own_process_group(self):
        process = SimpleNamespace(pid=12345, wait=unittest.mock.Mock(
            side_effect=[subprocess.TimeoutExpired("synthetic", 1), 0]))
        with patch("scripts.accuracy_optimizer_engine.subprocess.Popen", return_value=process), \
             patch("scripts.accuracy_optimizer_engine.os.killpg") as kill:
            with self.assertRaises(TimeoutError):
                analyze_in_child(["e4"], Budget("time", 1), 1)
        kill.assert_called_once()
        self.assertEqual(kill.call_args.args[0], process.pid)

    def test_actual_limit_replaces_production_limit_without_mutating_it(self):
        observed = []
        class Engine:
            options = {}
            timeout = 10
            def analyse(self, board, limit, **kwargs):
                observed.append(limit)
                return [{"depth": 15, "pv": [chess.Move.from_uci("e2e4")],
                         "score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE)}]
        original = chess.engine.Limit(depth=18)
        proxy = BudgetEngine(Engine(), Budget("time", 1), time.time() + 60)
        proxy.analyse(chess.Board(), original, multipv=2)
        self.assertEqual(original.depth, 18)
        self.assertEqual(observed[0].time, 1)
        self.assertIsNone(observed[0].depth)
        proxy = BudgetEngine(Engine(), Budget("depth", 18), time.time() + 60)
        with self.assertRaisesRegex(ValueError, "required depth"):
            proxy.analyse(chess.Board(), original)


class SafetyContract(unittest.TestCase):
    def test_workflow_remains_opt_in_and_does_not_restart_or_cancel_bot(self):
        text = (Path(__file__).parent / ".github/workflows/nightly_caps2_research.yml").read_text()
        for guard in ("ACCURACY_OPTIMIZER_ENABLED", "ACCURACY_OPTIMIZER_COMPUTE_ISOLATED",
                      "CHESSCOM_RESEARCH_WRITTEN_AUTHORIZATION"):
            self.assertIn(f"vars.{guard} == 'true'", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertNotIn("gh run cancel", text)
        self.assertNotIn("--force", text)
        self.assertNotIn("fairplay_distributed", text)


if __name__ == "__main__":
    unittest.main()
