"""Offline regression tests for v51: no accusation, no invented data."""
import math
import unittest
from types import SimpleNamespace

from fairplay_config import CONFIG
from fairplay_v51_shadow import (MODE, _accuracy, _validation, _verify_deep,
                                 class_anomalies, observations,
                                 summarize_observations, chart_pngs)


def game(index, kind="blitz", *, elo=1400, accurate=80, official=82,
         seconds=3, control="180+0", rated=True, completed=True):
    depth = CONFIG.bullet_deep_depth if kind == "bullet" else 18
    decision = SimpleNamespace(
        phase="middlegame", forced=False, clock_valid=seconds is not None,
        think=seconds,
        metrics={"scaled_loss": .02 if accurate >= 80 else .18,
                 "search_depth": depth,
                 "search_inconsistent": False,
                 "search_contract": {"mode": "depth", "requested": depth,
                     "multipv": CONFIG.deep_multipv, "completed": completed,
                     "exact": completed}})
    return SimpleNamespace(time_class=kind, ended=1700000000+index,
        identity=f"fake-{kind}-{index}", time_control=control, rated=rated,
        accuracy=official, rating=1500, opponent_rating=elo,
        decisions=[decision], deep=True)


class DeepOnlyShadow(unittest.TestCase):
    def test_three_independent_game_axes_and_missing_clock_gaps(self):
        games = [game(2, "rapid", seconds=None),
                 game(3, "bullet", seconds=0.25),
                 game(1, "blitz", seconds=5),
                 game(4, "blitz", seconds=None),
                 game(5, "rapid", seconds=9)]
        groups = observations(games)
        self.assertEqual({k: len(v) for k, v in groups.items()},
                         {"bullet": 1, "blitz": 2, "rapid": 2})
        self.assertEqual([r["game"] for r in groups["blitz"]], [1, 2])
        self.assertEqual(groups["blitz"][1]["median_think_seconds"], None)
        self.assertEqual(groups["rapid"][0]["median_think_seconds"], None)
        self.assertEqual(groups["rapid"][1]["median_think_seconds"], 9)
        self.assertFalse(summarize_observations(groups)["priority_authority"])

    def test_estimate_does_not_use_official_reference(self):
        first = game(1, official=10)
        second = game(2, official=99)
        self.assertEqual(_accuracy(first), _accuracy(second))
        val = _validation(observations([first, second])["blitz"])
        self.assertEqual(val["compared_games"], 2)
        self.assertTrue(val["mae"] > 0)

    def test_no_missing_scores_are_imputed(self):
        group = observations([game(1, official=None) ])
        self.assertEqual(group["blitz"][0]["official_accuracy"], None)
        self.assertEqual(summarize_observations(group)["accuracy_validation"]["compared_games"], 0)
        self.assertIsNone(summarize_observations(group)["accuracy_validation"]["within_5"])

    def test_clock_drift_or_sparse_games_block_shift_flag(self):
        rows = []
        for i in range(30):
            rows.append({"control": "180+0", "rated": True,
                "estimated_accuracy": 65 if i < 15 else 90,
                "opponent_elo": 1500 if i < 15 else 1250})
        self.assertEqual(class_anomalies(rows), [])
        for r in rows[15:]:
            r["opponent_elo"] = 1510
        self.assertEqual(len(class_anomalies(rows)), 1)
        self.assertEqual(class_anomalies(rows)[0]["opponent_elo_shift"], 10)
        self.assertEqual(class_anomalies(rows[:20]), [])

    def test_exact_full_depth_contract_is_mandatory(self):
        valid = game(1)
        self.assertTrue(_verify_deep(valid, CONFIG))
        valid.decisions[0].metrics["search_contract"]["exact"] = False
        self.assertFalse(_verify_deep(valid, CONFIG))
        valid.decisions[0].metrics["search_contract"]["exact"] = True
        valid.decisions[0].metrics["search_contract"]["requested"] = 12
        self.assertFalse(_verify_deep(valid, CONFIG))
        self.assertTrue(_verify_deep(game(2, "bullet"), CONFIG))

    def test_deep_only_review_never_executes_fast_engine(self):
        from unittest.mock import patch
        from fairplay_v51_shadow import review

        rows = [game(i, kind) for i,kind in enumerate(("blitz", "bullet", "rapid"))]
        class API:
            def __init__(self, deadline): self.deadline = deadline
            def get(self, target, profile=False):
                return {"username": target} if profile else None
            def close(self): pass

        class Scanner:
            def __init__(self, deadline, config, factory):
                self.name="Mock Stockfish 19"
            def analyse_decision(self, item, decision, budget):
                self.budgets.append(budget.depth)
                decision.metrics["search_depth"]=budget.depth
                decision.metrics["search_contract"]["requested"]=budget.depth
            def close(self): pass
            budgets=[]
        scanner=Scanner
        with (patch("fairplay_v51_shadow.collect_games",
                    return_value=(rows, {}, False)) as collected,
              patch("fairplay_v51_shadow.EngineScanner", scanner),
              patch("fairplay_v51_shadow.summarize",
                    side_effect=lambda g,config: setattr(g, "metrics", {})),
              patch("fairplay_v51_shadow.aggregate",
                    side_effect=lambda games: {"games":len(games),"decisions":len(games)})):
            result=review("username",lambda stage:None,api_factory=API,
                          engine_factory=lambda:None)
        self.assertEqual(result.diagnostics["v51_shadow"]["fast_games"],0)
        self.assertEqual(result.coverage["deep_reviewed"],3)
        self.assertEqual(result.priority,"INSUFFICIENT DATA")
        self.assertEqual(sorted(scanner.budgets),[12,18,18])
        self.assertTrue(collected.call_args.kwargs["include_unrated"])
        self.assertEqual(collected.call_args.kwargs["include_recent_all_short_count"],500)

    def test_pngs_are_separate_and_named_by_class(self):
        groups = observations([game(i, kind)
            for i,kind in enumerate(("rapid", "bullet", "blitz", "bullet"))])
        pngs = chart_pngs(groups)
        self.assertEqual([name for name, _ in pngs],
                         ["fairplay_bullet_charts.png", "fairplay_blitz_charts.png",
                          "fairplay_rapid_charts.png"])
        self.assertTrue(all(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 5000
                            for _,data in pngs))


if __name__ == "__main__":
    unittest.main()
