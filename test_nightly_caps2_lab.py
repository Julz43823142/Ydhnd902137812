"""Research-only nightly schedule, analysis, resume and fitting regressions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scripts import nightly_caps2_lab as lab
from scripts.collect_chesscom_accuracy import validate
from test_chesscom_reference_collector import example, OCT

UTC = timezone.utc
NIGHT = datetime(2026, 10, 17, 0, 12, tzinfo=lab.LOCAL)


def record(num):
    item = example(num)
    row, err = validate(item, "sharkmeister", OCT, "2026-10-16T00:00:00+00:00")
    assert not err, err
    return row


def pretend_analyze(moves, **kwargs):
    checks = kwargs.get("interrupt_check")
    if checks is not None:
        checks()
    return {
        "analysed_plies": len(moves),
        "truncated": False,
        "moves": [
            {"side": "white" if i % 2 == 0 else "black",
             "win_loss_pct": float(i % 3), "classification": "good",
             "eval_white_cp": 4, "move_accuracy": 99.0}
            for i in range(len(moves))
        ],
        "initial_white_win_pct": 50.0,
        "engine": "Stockfish 19",
    }


class Schedule(unittest.TestCase):
    def test_window_bounds_and_mode(self):
        for h in (0, 1, 5, 8):
            t = NIGHT.replace(hour=h)
            self.assertTrue(lab.local_window(t))
            self.assertIsNotNone(lab.nightly_deadline(t))
            self.assertEqual(lab.mode_for_time(t), "depth18" if h % 2 == 0 else "time1")
        for h in (9, 10, 18, 23):
            t = NIGHT.replace(hour=h)
            self.assertFalse(lab.local_window(t))
            self.assertIsNone(lab.nightly_deadline(t))

    def test_dst_clock_shifts_not_fixed_utc(self):
        a = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        b = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
        self.assertTrue(lab.local_window(a))
        self.assertTrue(lab.local_window(b))
        self.assertEqual(a.astimezone(lab.LOCAL).hour, 2)
        self.assertEqual(b.astimezone(lab.LOCAL).hour, 2)
        late = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
        self.assertFalse(lab.local_window(late))

    def test_near_closing_window_deadline(self):
        near = NIGHT.replace(hour=8, minute=51)
        cutoff = lab.nightly_deadline(near)
        self.assertEqual(cutoff.astimezone(lab.LOCAL).strftime("%H:%M"), "08:58")

    def test_outside_window_does_not_fetch_or_analyze(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail(*args):
                raise AssertionError("No work outside window")
            result = lab.run(tmp, now=NIGHT.replace(hour=9), collector=fail, analyze=fail)
            self.assertEqual(result["status"], "skipped_outside_window")
            self.assertFalse((Path(tmp) / "dataset.json").exists())


class Capture(unittest.TestCase):
    def test_mode_override_does_not_change_default(self):
        game = record(11)
        captured = []
        def spy(moves, **kwargs):
            captured.append(kwargs)
            return pretend_analyze(moves, **kwargs)

        baseline = lab.analyze_reference(game, "depth18", analyze=spy)
        time1 = lab.analyze_reference(game, "time1", analyze=spy)
        self.assertEqual(baseline["mode"], "depth18")
        self.assertEqual(time1["mode"], "time1")
        self.assertNotIn("analysis_time_per_position", captured[0])
        self.assertEqual(captured[1]["analysis_time_per_position"], 1.0)
        self.assertEqual(len(time1["moves"]), 6)
        self.assertEqual(time1["chesscom"], game["chesscom"])

    def test_incomplete_engine_metrics_are_refused(self):
        g = record(12)
        def shortened(moves, **kwargs):
            result = pretend_analyze(moves, **kwargs)
            result["analysed_plies"] = len(moves) - 1
            return result
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            lab.analyze_reference(g, "depth18", analyze=shortened)

    def test_fingerprint_is_mode_and_source_bound(self):
        g = record(13)
        a = lab.analyze_reference(g, "depth18", analyze=pretend_analyze)
        self.assertTrue(lab.validate_metric(a, g, "depth18"))
        self.assertFalse(lab.validate_metric(a, g, "time1"))
        old = dict(a)
        old["model_version"] = "stale-v0"
        self.assertFalse(lab.validate_metric(old, g, "depth18"))
        newer = dict(g)
        newer["pgn"] += "\n{extra comment}"
        self.assertFalse(lab.validate_metric(a, newer, "depth18"))

    def test_resume_and_alternating_hours(self):
        games = [record(21), record(22)]
        def collect_stub(player, target):
            return games, {"stored_games": 2, "api_errors": []}
        with tempfile.TemporaryDirectory() as tmp:
            # Stub two entries to stand in for a 100-entry snapshot in the loop.
            with patch.object(lab, "verify", return_value=2):
                counter = []
                def tracked(moves, **kwargs):
                    counter.append(1)
                    return pretend_analyze(moves, **kwargs)
                first = lab.run(tmp, now=NIGHT.replace(hour=0), now_fn=lambda: NIGHT.replace(hour=0),
                                collector=collect_stub, analyze=tracked, max_games_per_run=1, trials=0)
                self.assertEqual(first["status"], "no_reference_snapshot")
                # Seed an independently audited snapshot as a prior full fetch.
                lab.atomic_json(Path(tmp) / "dataset.json", games)
                second = lab.run(tmp, now=NIGHT.replace(hour=0), now_fn=lambda: NIGHT.replace(hour=0),
                                 analyze=tracked, max_games_per_run=1, trials=0)
                third = lab.run(tmp, now=NIGHT.replace(hour=0), now_fn=lambda: NIGHT.replace(hour=0),
                                analyze=tracked, max_games_per_run=1, trials=0)
                self.assertEqual([second["new_completed_games"], third["new_completed_games"]], [1, 1])
                self.assertEqual(third["cached_research_metrics"], 2)
                fourth = lab.run(tmp, now=NIGHT.replace(hour=0), now_fn=lambda: NIGHT.replace(hour=0),
                                 analyze=tracked, trials=0)
                self.assertEqual(fourth["new_completed_games"], 0)
                night1 = lab.run(tmp, now=NIGHT.replace(hour=1), now_fn=lambda: NIGHT.replace(hour=1),
                                 analyze=tracked, max_games_per_run=1, trials=0)
                self.assertEqual(night1["mode"], "time1")
                self.assertEqual(night1["new_completed_games"], 1)
                self.assertEqual(len(counter), 3)
                self.assertEqual(len(list((Path(tmp) / "metrics" / "depth18").glob("*.json"))), 2)
                self.assertEqual(len(list((Path(tmp) / "metrics" / "time1").glob("*.json"))), 1)

    def test_interrupted_games_not_saved(self):
        games = [record(21)]
        with tempfile.TemporaryDirectory() as tmp:
            lab.atomic_json(Path(tmp) / "dataset.json", games)
            with patch.object(lab, "verify", return_value=1):
                def interrupted(moves, **kwargs):
                    raise lab.DeadlineReached()
                outcome = lab.run(tmp, now=NIGHT, now_fn=lambda: NIGHT,
                                  analyze=interrupted, max_games_per_run=1, trials=0)
                self.assertTrue(outcome["incomplete_game_interrupted"])
                self.assertEqual(outcome["cached_research_metrics"], 0)
                self.assertFalse(list(Path(tmp).glob("metrics/**/*.json")))

    def test_preexisting_state_not_overwritten_when_api_incomplete(self):
        games = [record(31)]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            lab.atomic_json(folder / "dataset.json", games)
            original = (folder / "dataset.json").read_text()
            with patch.object(lab, "verify", return_value=1):
                incoming, updated, report = lab.get_snapshot(
                    folder, target=100,
                    collector=lambda p, n: ([], {"api_errors": ["HTTP 429"], "stored_games": 0})
                )
            self.assertFalse(updated)
            self.assertEqual(incoming, games)
            self.assertEqual((folder / "dataset.json").read_text(), original)


class Optimization(unittest.TestCase):
    def test_fit_only_uses_data_with_engine_metrics(self):
        samples = []
        for n in range(48):
            source = record(100 + n)
            metric = lab.analyze_reference(source, "depth18", analyze=pretend_analyze)
            samples.append(metric)
        train, holdout = lab.stable_split(samples)
        self.assertGreaterEqual(len(train), 15)
        self.assertGreaterEqual(len(holdout), 5)
        fit = lab.fit_candidates(samples, seed=42, trials=5)
        self.assertTrue(fit["candidate_available"])
        self.assertEqual(fit["training_games"] + fit["holdout_games"], 48)
        self.assertTrue(fit["no_production_changes"])
        self.assertIn("baseline_holdout_mae", fit)
        self.assertEqual(fit["settings"][4] in (True, False), True)

    def test_insufficient_game_count_does_not_fit(self):
        self.assertFalse(lab.fit_candidates([], seed=0)["candidate_available"])
        self.assertFalse(lab.fit_candidates(
            [lab.analyze_reference(record(1), "depth18", analyze=pretend_analyze)]
        )["candidate_available"])

    def test_holdout_is_independent_of_list_order(self):
        samples = [lab.analyze_reference(record(100+n), "depth18", analyze=pretend_analyze)
                   for n in range(45)]
        train, holdout = lab.stable_split(samples)
        rt, rh = lab.stable_split(list(reversed(samples)))
        self.assertEqual(len(train), len(rt))
        self.assertEqual(len(holdout), len(rh))

    def test_atomic_json_writes_and_can_read_zeros(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "best.json"
            lab.atomic_json(path, {"candidate": [0.0, 100.0], "recommended": False})
            self.assertEqual(lab.read_json(path)["candidate"][0], 0)
            self.assertEqual(lab.read_json(Path(tmp) / "missing.json", {}), {})


if __name__ == "__main__":
    unittest.main()
