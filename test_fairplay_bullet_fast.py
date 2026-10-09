"""Synthetic-only regression tests for 500-game mixed-depth throughput and resume."""
import concurrent.futures
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import chess
import fairplay_analysis as analysis
import fairplay_maia
import fairplay_policy
from fairplay_checkpoint import CheckpointStore
from fairplay_config import CONFIG, VERSION
from fairplay_data import ScanDeadline, primary_limit
from test_fairplay import TARGET
from test_fairplay_checkpoint import game_with_positions, metrics
from test_fairplay_v4 import game


class MixedDepthTests(unittest.TestCase):
    def test_bullet_is_screened_at_12_while_blitz_and_rapid_remain_18(self):
        for kind, expected in (("bullet", 12), ("blitz", 18), ("rapid", 18)):
            self.assertEqual(analysis.full_depth_budget(
                type("Game", (), {"time_class": kind})()).depth, expected)
        self.assertEqual(primary_limit(CONFIG), 500)

    def test_all_500_primary_games_are_preserved_with_mixed_depth(self):
        records = [game(i, deep=False) for i in range(500)]
        for i, item in enumerate(records):
            # Distinct from existing 500-game test IDs: the process-local
            # warm cache must not hide any of this run's 500 fast searches.
            item.identity = f'bullet-fast-{i}'
            item.rated = True
            item.time_class = ("bullet" if i % 2 == 0 else
                               "rapid" if i % 3 else "blitz")
        calls = []
        class FakeScanner:
            name = "Stockfish 19 synthetic"
            def __init__(self, *args):
                self.engine = type("Engine", (), {"timeout": 30.0})()
            def analyse(self, item, budget):
                depth = getattr(budget, "depth", None)
                calls.append((item.identity, depth or "fast"))
                if depth:
                    for move in item.decisions:
                        move.metrics["search_depth"] = depth
                else:
                    item.fast_metrics = copy.deepcopy(item.metrics)
            def close(self):
                pass
        def fake_result(target, analyzed, *args, **kwargs):
            return analysis.ReviewResult(
                username=target, games=list(analyzed), selected_games=500,
                skipped={}, partial=False, engine="Synthetic", totals={},
                classes={}, performance={}, context={}, families={},
                priority="LOW", confidence="HIGH", reasons=[],
                deep_confirmed=False, deep_coverage={}, elapsed=0)
        api = Mock()
        api.get.return_value = {"username": TARGET}
        with patch.dict(os.environ, {"FAIRPLAY_FULL_DEPTH18": "1",
                                      "FAIRPLAY_REQUIRE_MAIA": "0"}), \
             patch.object(analysis, "collect_games", return_value=(records, {}, False)), \
             patch.object(analysis, "EngineScanner", FakeScanner), \
             patch.object(analysis, "score_review", side_effect=fake_result), \
             patch.object(fairplay_maia, "annotate_history",
                          return_value={"available": False}), \
             patch.object(fairplay_policy, "integrate",
                          side_effect=lambda result, *args: result):
            result = analysis.review(TARGET, lambda _: None,
                                     api_factory=lambda _: api,
                                     engine_factory=lambda: object())
        fast = {identity for identity, depth in calls if depth == "fast"}
        deep = {identity: depth for identity, depth in calls if depth != "fast"}
        self.assertEqual(len(fast), 500)
        self.assertEqual(len(deep), 500)
        self.assertEqual(result.coverage["primary_collected"], 500)
        for item in records:
            self.assertEqual(deep[item.identity],
                             12 if item.time_class == "bullet" else 18)
        contract = result.diagnostics["run_contract"]
        self.assertEqual(contract["required_primary_depth_by_class"],
                         {"bullet": 12, "blitz": 18, "rapid": 18})
        self.assertIsNone(contract["required_primary_depth"])
        self.assertEqual(contract["bullet_depth12_total_positions"],
                         contract["bullet_depth12_completed_positions"])
        self.assertEqual(contract["depth18_total_positions"],
                         contract["depth18_completed_positions"])

    def test_checkpoint_restores_only_correct_bullet_depth(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "DISCORD_TOKEN": "synthetic-only-not-real"}):
            store = CheckpointStore(path=str(Path(temp) / "resume.enc"), remote="")
            store.note("privateaccount", token="synthetic")
            store.bind("privateaccount", engine="Stockfish 19",
                       version=VERSION, config=CONFIG, full_depth=True, maia="synthetic")
            bullet = game_with_positions(1)
            bullet.time_class = "bullet"
            bullet.decisions[0].metrics = metrics("deep", depth=12)
            self.assertTrue(store.record("privateaccount", bullet, bullet.decisions[0], "deep"))
            restored = game_with_positions(1)
            restored.time_class = "bullet"
            self.assertTrue(store.restore("privateaccount", restored,
                                          restored.decisions[0], "deep", CONFIG, True))
            rapid = game_with_positions(1)
            rapid.time_class = "rapid"
            self.assertFalse(store.restore("privateaccount", rapid,
                                           rapid.decisions[0], "deep", CONFIG, True))
            bullet.decisions[0].metrics = metrics("deep", depth=11)
            store.record("privateaccount", bullet, bullet.decisions[0], "deep")
            self.assertFalse(store.restore("privateaccount", restored,
                                           restored.decisions[0], "deep", CONFIG, True))

    def test_batch_checkpoint_writes_and_durable_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "DISCORD_TOKEN": "synthetic-only-not-real"}):
            path = str(Path(temp) / "resume.enc")
            store = CheckpointStore(path=path, remote="")
            store.note("privateaccount", token="synthetic")
            store.bind("privateaccount", engine="Stockfish 19",
                       version=VERSION, config=CONFIG, full_depth=True, maia="synthetic")
            item = game_with_positions(130)
            class SyntheticEngine:
                def run_decision(self, game, decision, budget, deadline):
                    decision.metrics = metrics("fast")
            with patch.object(store, "_write", wraps=store._write) as writes:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    done, partial = analysis.run_position_batch(
                        executor, SyntheticEngine(), [(item, d) for d in item.decisions],
                        CONFIG.fast_nodes, ScanDeadline(time.monotonic()+60),
                        lambda _: None, "Fast", checkpoint=store,
                        target="privateaccount", phase="fast",
                        checkpoint_config=CONFIG, checkpoint_full_depth=True)
                self.assertLessEqual(writes.call_count, 4,
                                     "checkpoint must not serialize per position")
            self.assertFalse(partial)
            self.assertEqual(done, {id(item)})
            recovered = CheckpointStore(path=path, remote="")
            fresh = game_with_positions(130)
            self.assertTrue(recovered.restore("privateaccount", fresh,
                                              fresh.decisions[0], "fast", CONFIG, True))
            self.assertTrue(recovered.restore("privateaccount", fresh,
                                              fresh.decisions[-1], "fast", CONFIG, True))


if __name__ == "__main__":
    unittest.main()
