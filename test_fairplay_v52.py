"""Synthetic depth-only priority, honest chart scales and recovery tests."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from concurrent.futures import Future

from fairplay_charts import axis_range, class_svg, comparison_svg
from fairplay_config import CONFIG
from fairplay_depth_priority import review_priority
from fairplay_v51_shadow import observations, _verified_decision
from test_fairplay_v51 import game as engine_game


def sample(index, *, extraordinary=False, kind="blitz", rated=True):
    human = {"opportunities": 12, "hits": 12 if extraordinary else 2,
             "hard_opportunities": 12, "hard_hits": 12 if extraordinary else 3,
             "information": .8 if extraordinary else .02,
             "quality_excess": .5 if extraordinary else .01,
             "anomaly_strength": .8 if extraordinary else .02,
             "observed_quality": .99 if extraordinary else .7,
             "quality_reference": .4 if extraordinary else .69,
             "quiet_hits": 8 if extraordinary else 0}
    return SimpleNamespace(identity=f"synthetic-{index}", ended=index * 600,
        rated=rated, rating=1600, time_class=kind, time_control="180+2",
        decisions=[], metrics={"decisions": 40, "top1": .99, "human": human})


class Priority(unittest.TestCase):
    def test_one_extraordinary_game_and_ordinary_background_are_not_high(self):
        rows = [sample(i) for i in range(40)]
        rows[-1] = sample(39, extraordinary=True)
        self.assertEqual(review_priority(rows, complete=True)["priority"], "LOW")

    def test_overwhelming_disjoint_gameplay_can_be_high_without_clocks(self):
        rows = [sample(i, extraordinary=True) for i in range(20)]
        result = review_priority(rows, complete=True)
        self.assertEqual(result["priority"], "HIGH")
        self.assertEqual(result["classes"]["blitz"]["qualifying_disjoint_blocks"], 2)
        self.assertFalse(result["classes"]["blitz"]["independent_clock_support"])

    def test_ten_game_anomaly_is_moderate_and_single_game_insufficient(self):
        rows = [sample(i, extraordinary=True) for i in range(10)]
        self.assertEqual(review_priority(rows, complete=True)["priority"], "MODERATE")
        self.assertEqual(review_priority(rows[:1], complete=True)["priority"], "INSUFFICIENT DATA")

    def test_timing_alone_and_generic_elite_precision_cannot_be_high(self):
        rows = [sample(i) for i in range(40)]
        for row in rows:
            row.rating = 2800
        with patch("fairplay_depth_priority.trivial_delay_summary", return_value={"recurrent": True}):
            self.assertEqual(review_priority(rows, complete=True)["priority"], "LOW")

    def test_unrated_and_account_status_do_not_raise_priority(self):
        rows = [sample(i) for i in range(30)]
        original = review_priority(rows, complete=True)
        for row in rows:
            row.status = "closed:fair_play_violations"
        self.assertEqual(original, review_priority(rows, complete=True))
        rows += [sample(100+i, extraordinary=True, rated=False) for i in range(40)]
        self.assertEqual(original, review_priority(rows, complete=True))

    def test_incomplete_search_cannot_issue_a_negative_or_positive_tier(self):
        rows = [sample(i, extraordinary=True) for i in range(30)]
        self.assertEqual(review_priority(rows, complete=False)["priority"], "INSUFFICIENT DATA")

    def test_ten_small_games_can_have_a_tier_with_low_coverage_confidence(self):
        rows = [sample(i) for i in range(10)]
        for row in rows:
            row.metrics["decisions"] = CONFIG.min_game_decisions
        result = review_priority(rows, complete=True)
        self.assertEqual(result["priority"], "LOW")
        self.assertEqual(result["confidence"], "LOW")

    def test_low_opportunity_games_are_not_dropped_from_chronology(self):
        rows = [sample(i, extraordinary=i % 2 == 0) for i in range(40)]
        for row in rows[1::2]:
            row.metrics = {"decisions": 0}
        result = review_priority(rows, complete=True)
        self.assertEqual(result["classes"]["blitz"]["games"], 40)
        self.assertNotEqual(result["priority"], "HIGH")

    def test_bullet_requires_broader_replication(self):
        rows = [sample(i, extraordinary=True, kind="bullet") for i in range(60)]
        self.assertEqual(review_priority(rows[:20], complete=True)["priority"], "LOW")
        self.assertEqual(review_priority(rows[:30], complete=True)["priority"], "MODERATE")
        self.assertEqual(review_priority(rows, complete=True)["priority"], "HIGH")

    def test_very_high_requires_clock_support_and_separate_sessions(self):
        rows = [sample(i, extraordinary=True) for i in range(30)]
        self.assertEqual(review_priority(rows, complete=True)["priority"], "HIGH")
        with patch("fairplay_depth_priority.trivial_delay_summary", return_value={"recurrent": True}):
            self.assertEqual(review_priority(rows, complete=True)["priority"], "HIGH")
            for row in rows[15:]:
                row.ended += 10000
            self.assertEqual(review_priority(rows, complete=True)["priority"], "VERY HIGH")


class Charts(unittest.TestCase):
    def test_zoomed_accuracy_and_small_clock_axes_keep_all_values(self):
        low, high = axis_range([88, 92, 95], minimum_span=10, ceiling=100)
        self.assertGreater(low, 50)
        self.assertLessEqual(high, 100)
        self.assertLessEqual(low, 88)
        self.assertGreaterEqual(high, 95)
        self.assertGreaterEqual(axis_range([63, 90, 100], minimum_span=10, ceiling=100)[0], 55)
        low, high = axis_range([.8, 1, 2.2])
        self.assertLess(high, 20)
        self.assertGreaterEqual(high, 2.2)
        self.assertGreaterEqual(axis_range([1, 180])[1], 180)
        self.assertEqual(axis_range([None, float("nan")], ceiling=100), (0, 100))

    def test_clock_observations_do_not_hide_valid_long_thinks(self):
        row = engine_game(1, "rapid", seconds=180)
        self.assertEqual(observations([row])["rapid"][0]["median_think_seconds"], 180)

    def test_svg_keeps_isolated_points_and_explains_zoom(self):
        rows = [{"estimated_accuracy": 90, "official_accuracy": value,
                 "median_think_seconds": 1.5, "opponent_elo": 1500}
                for value in (95, None, 91)]
        svg = class_svg("bullet", rows)
        ElementTree.fromstring(svg)
        self.assertIn("Y-axes zoom", svg)
        self.assertIn("font-size=\"18\"", svg)
        self.assertGreaterEqual(svg.count("<circle"), 2)
        self.assertNotIn("120s", svg)

    def test_comparison_is_official_only_with_shared_groups_and_missing_data(self):
        groups = {"bullet": [{"estimated_accuracy": 99, "official_accuracy": 80, "opponent_elo": 1450}],
                  "rapid": [{"estimated_accuracy": 95, "official_accuracy": None, "opponent_elo": 1800}]}
        svg = comparison_svg(groups)
        ElementTree.fromstring(svg)
        self.assertIn("80.0%", svg)
        self.assertNotIn("99.0%", svg)
        self.assertIn("No data", svg)


class Recovery(unittest.TestCase):
    def test_finished_positions_are_checkpointed_before_a_slow_first_position(self):
        from fairplay_v51_shadow import review
        rows = [engine_game(1)]
        rows[0].decisions = [deepcopy(rows[0].decisions[0]) for _ in range(2)]
        for i, decision in enumerate(rows[0].decisions, 1):
            decision.metrics = {}
            decision.ply = i
        submitted, recorded = [], []
        class GuardFuture(Future):
            def result(self, timeout=None):
                if not self.done():
                    raise AssertionError("Waited for an unfinished position in submission order")
                return super().result(timeout)
        class Executor:
            def __init__(self, **kwargs): pass
            def submit(self, function, *args):
                future = GuardFuture()
                submitted.append((future, function, args))
                if len(submitted) == 2:
                    function(*args)
                    future.set_result(None)
                return future
            def shutdown(self, **kwargs): pass
        class API:
            def __init__(self, deadline): pass
            def get(self, target, profile=False): return {"username": target}
            def close(self): pass
        class Pool:
            size = 2
            name = "Synthetic Stockfish"
            def run_decision(self, game, decision, budget, deadline):
                decision.metrics = deepcopy(engine_game(1).decisions[0].metrics)
        class Checkpoint:
            def bind(self, *args, **kwargs): pass
            def restore(self, *args): return False
            def record(self, target, game, decision, phase, **kwargs):
                recorded.append(decision.ply)
                if decision.ply == 2:
                    future, function, args = submitted[0]
                    function(*args)
                    future.set_result(None)
                return True
            def flush(self, **kwargs): pass
        with (patch("fairplay_v51_shadow.collect_games", return_value=(rows, {}, False)),
              patch("fairplay_v51_shadow.ThreadPoolExecutor", Executor),
              patch("fairplay_v51_shadow.get_shared_engine_pool", return_value=Pool()),
              patch("fairplay_v51_shadow.summarize", side_effect=lambda g, config: setattr(g, "metrics", {})),
              patch("fairplay_v51_shadow.aggregate", side_effect=lambda games: {"games": len(games)})):
            review("synthetic", lambda stage: None, api_factory=API, checkpoint=Checkpoint())
        self.assertEqual(recorded, [2, 1])

    def test_bad_contract_types_are_not_verified(self):
        row = engine_game(1)
        decision = row.decisions[0]
        self.assertTrue(_verified_decision(decision, "blitz", CONFIG))
        for value in (True, None, "18", 18.0):
            changed = deepcopy(decision)
            changed.metrics["search_depth"] = value
            self.assertFalse(_verified_decision(changed, "blitz", CONFIG))

    def test_wrong_or_unknown_multipv_is_never_restored_as_complete(self):
        from test_fairplay_checkpoint import CheckpointTests, game_with_positions, metrics
        fixture = CheckpointTests()
        fixture.setUp()
        try:
            store = fixture.store()
            fixture.bind(store)
            game = game_with_positions(1)
            decision = game.decisions[0]
            key = store._key(game, decision, "deep")
            for count in (None, 1, True, "3", 3.0):
                row = metrics("deep")
                row["search_contract"]["multipv"] = count
                store.state["jobs"]["privateaccount"]["positions"][key] = {"metrics": row}
                self.assertFalse(store.restore("privateaccount", game, decision, "deep", CONFIG, True))
        finally:
            fixture.doCleanups()

    def test_full_snapshot_writes_are_batched_without_reducing_engine_work(self):
        from fairplay_v51_shadow import review
        rows = [engine_game(1)]
        rows[0].decisions = [deepcopy(rows[0].decisions[0]) for _ in range(64)]
        for decision in rows[0].decisions:
            decision.metrics = {}
        counter = {"engine": 0, "record": 0, "flush": 0}
        class API:
            def __init__(self, deadline): pass
            def get(self, target, profile=False): return {"username": target}
            def close(self): pass
        class Pool:
            size = 2
            name = "Synthetic Stockfish"
            def run_decision(self, game, decision, budget, deadline):
                counter["engine"] += 1
                decision.metrics = deepcopy(engine_game(1).decisions[0].metrics)
        class Checkpoint:
            def bind(self, *args, **kwargs): pass
            def restore(self, *args): return False
            def record(self, *args, persist=True):
                if persist: raise AssertionError("Per-move full serialization returned")
                counter["record"] += 1
                return True
            def flush(self, **kwargs): counter["flush"] += 1
        with (patch("fairplay_v51_shadow.collect_games", return_value=(rows, {}, False)),
              patch("fairplay_v51_shadow.get_shared_engine_pool", return_value=Pool()),
              patch("fairplay_v51_shadow.summarize", side_effect=lambda g, config: setattr(g, "metrics", {})),
              patch("fairplay_v51_shadow.aggregate", side_effect=lambda games: {"games": len(games)})):
            result = review("synthetic", lambda stage: None, api_factory=API, checkpoint=Checkpoint())
        self.assertEqual(counter["engine"], 64)
        self.assertEqual(counter["record"], 64)
        self.assertEqual(counter["flush"], 2)
        self.assertEqual(result.coverage["deep_reviewed"], 1)


class Presentation(unittest.TestCase):
    def test_report_shows_tier_and_details_do_not_invent_maia(self):
        from fairplay_v51_shadow import summarize_observations, MODE
        from fairplay_ui import result_embed, detail_embed
        record = summarize_observations(observations([engine_game(1)]))
        record["review_priority"] = review_priority([sample(i, extraordinary=True) for i in range(20)], complete=True)
        result = SimpleNamespace(username="Synthetic", version=MODE, priority="HIGH",
            confidence="HIGH", reasons=record["review_priority"]["reasons"], classes={},
            diagnostics={"v51_shadow": record})
        embed = result_embed(result)
        serialized = json.dumps(embed.to_dict())
        self.assertIn("HIGH review priority", serialized)
        self.assertIn("not a cheating verdict", serialized)
        self.assertIn(MODE, serialized)
        self.assertLessEqual(len(embed), 6000)
        detail = detail_embed(result, "Human Moves")
        self.assertIn("No learned Maia inference", json.dumps(detail.to_dict()))
        gates = detail_embed(result, "Review Gates")
        self.assertIn("Qualified disjoint blocks", json.dumps(gates.to_dict()))
        self.assertLessEqual(len(gates), 6000)


if __name__ == "__main__":
    unittest.main()
