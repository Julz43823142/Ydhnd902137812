"""500-game full-depth execution contract, using synthetic engine and games only."""
import copy
import os
import unittest
from unittest.mock import Mock, patch

import chess
import fairplay_analysis as analysis
import fairplay_maia
import fairplay_policy
from fairplay_config import CONFIG
from test_fairplay import TARGET
from test_fairplay_v4 import game


class FiveHundredDeepTests(unittest.TestCase):
    def test_every_one_of_500_games_gets_depth18_not_just_last_100(self):
        records = [game(i, deep=False) for i in range(500)]
        for item in records:
            item.rated = True
        calls = []
        class FakeScanner:
            name = "Stockfish 19 synthetic"
            def __init__(self, *args):
                # Production EngineScanner wraps a UCI engine with a timeout.
                # This fake must expose the same contract for full-depth mode.
                self.engine = type("FakeEngine", (), {"timeout": 30.0})()
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
            result = analysis.review(TARGET, lambda stage: None,
                                     api_factory=lambda deadline: api,
                                     engine_factory=lambda: object())
        fast = {identity for identity, depth in calls if depth == "fast"}
        deep = {identity for identity, depth in calls if depth == 18}
        self.assertEqual(len(fast), 500)
        self.assertEqual(fast, deep)
        self.assertEqual(result.coverage["primary_collected"], 500)
        self.assertEqual(result.diagnostics["runtime"]["full_depth18_games_completed"], 500)
        self.assertEqual(result.diagnostics["run_contract"]["required_primary_depth"], 18)
