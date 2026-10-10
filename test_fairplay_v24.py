"""v24 regressions: no threshold weakening, 14 shards, evidence-grade diagnostics,
player-disjoint ChessFraud baseline and label-safe offline calibration.
All samples synthetic. No Chess.com requests, Discord access or workflow dispatch.
"""
import copy
import unittest

from fairplay_config import CONFIG
from fairplay_distributed import WORKERS, artifact_name, shard_games
from fairplay_difficulty import stability
from fairplay_stability_audit import SCHEMA, summarise_stability
from scripts.benchmark_chessfraud import player_games, heldout_baselines
from scripts.fairplay_calibration_lab import evaluate, partition, wilson_interval
from test_fairplay_distributed import sample


class FourteenShards(unittest.TestCase):
    def test_indices_10_through_13_are_valid_not_truncated(self):
        self.assertEqual(WORKERS, 14)
        ticket = "a" * 24
        for shard in range(WORKERS):
            self.assertEqual(artifact_name("res", ticket, shard),
                             f"res_{ticket}_{shard}.enc")
            self.assertEqual(artifact_name("progress", ticket, shard),
                             f"progress_{ticket}_{shard}.enc")
        # Failure closed: invalid shard 14 is never silently remapped.
        with self.assertRaises(Exception):
            artifact_name("res", ticket, 14)

    def test_shard_balance_and_unique_game_assignment(self):
        games = [sample(i) for i in range(1, 52)]
        groups = shard_games(games)
        self.assertEqual(len(groups), 14)
        seen = [g.identity for group in groups for g in group]
        self.assertEqual(len(seen), len(games))
        self.assertEqual(len(set(seen)), len(games))
        self.assertLessEqual(max(len(g) for g in groups) -
                             min(len(g) for g in groups), 2)


class StabilitySeparation(unittest.TestCase):
    def _decision(self):
        game = sample(301, bullet=True)
        decision = next(d for d in game.decisions if d.useful)
        base = {"nodes": CONFIG.fast_nodes, "rank": 1, "best": decision.move,
                "cpl": 5, "scaled_loss": .01, "useful": True,
                "search_inconsistent": False, "played_boundary_cp": 90,
                "difficulty": .9, "competitive": True,
                "search_contract": {"engine": "synthetic", "mode": "nodes",
                                    "requested": CONFIG.fast_nodes,
                                    "completed": True, "exact": True,
                                    "multipv": 3}}
        decision.fast_engine = base
        deep = copy.deepcopy(base)
        deep.update(nodes=30000, search_depth=12,
                    search_contract={"engine": "synthetic", "mode": "depth",
                                     "requested": 12, "completed": True,
                                     "exact": True, "multipv": 3})
        decision.metrics = deep
        game.decisions = [decision]
        game.deep = True
        return game, decision

    def test_quality_can_be_preserved_without_evidence_geometry(self):
        game, decision = self._decision()
        decision.metrics["difficulty"] = .25
        self.assertFalse(stability(decision))
        record = decision.metrics["search_stability"]
        self.assertTrue(record["objective_quality_preserved"])
        self.assertFalse(record["evidential_geometry_preserved"])
        audit = summarise_stability([game], [game.identity])
        self.assertEqual(audit["schema"], SCHEMA)
        self.assertEqual(audit["classes"]["bullet"]["compared"], 1)
        self.assertEqual(audit["classes"]["bullet"]["objective_quality_preserved"], 1)
        self.assertEqual(audit["classes"]["bullet"]["evidential_geometry_preserved"], 0)
        self.assertEqual(audit["classes"]["bullet"]["stable"], 0)
        self.assertFalse(audit["scoring_influence"])

    def test_actual_evidence_stability_and_quality_preservation(self):
        game, decision = self._decision()
        self.assertTrue(stability(decision))
        audit = summarise_stability([game])
        row = audit["classes"]["bullet"]
        self.assertEqual(row["both_quality_and_geometry_preserved"], 1)
        self.assertEqual(row["stable"], 1)
        self.assertEqual(row["quality_preserved_among_compared"], 1)

    def test_unverified_depth_does_not_create_quality(self):
        game, decision = self._decision()
        decision.metrics["search_contract"]["completed"] = False
        self.assertFalse(stability(decision))
        row = summarise_stability([game])["classes"]["bullet"]
        self.assertEqual(row["unmeasured"], 1)
        self.assertEqual(row["objective_quality_preserved"], 0)


class CalibrationLab(unittest.TestCase):
    def test_fixed_partition_prevents_same_player_leakage(self):
        self.assertEqual(partition("pseudonym-a"), partition("pseudonym-a"))
        self.assertIn(partition("pseudonym-b"), ("holdout", "development"))

    def test_repeated_scans_count_once_and_suspicions_are_not_labels(self):
        cases = [
            {"player_key": "a", "truth": "controlled_fair", "priority": "LOW",
             "shadow": {"higher_gate": "MODERATE"}},
            {"player_key": "a", "truth": "controlled_fair", "priority": "HIGH",
             "shadow": {"higher_gate": "LOW"}},
            {"player_key": "b", "truth": "controlled_assisted", "priority": "HIGH",
             "shadow": {"higher_gate": "VERY HIGH"}},
            {"player_key": "s", "truth": "suspected", "priority": "VERY HIGH"},
        ]
        result = evaluate(cases)
        self.assertEqual(result["players_evaluated"], 2)
        self.assertEqual(result["scans_evaluated"], 3)
        self.assertIn("higher_gate", result["scenarios"])
        self.assertFalse(result["production_influence"])
        self.assertNotIn('"player_key"', str(result))
        self.assertNotIn('"s"', str(result))

    def test_conflicting_labels_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([{"player_key": "a", "truth": "controlled_fair",
                       "priority": "LOW"},
                      {"player_key": "a", "truth": "controlled_assisted",
                       "priority": "HIGH"}])

    def test_shadow_missing_scans_not_cherry_picked(self):
        cases = [{"player_key": "a", "truth": "controlled_fair",
                  "priority": "LOW", "shadow": {"trial": "HIGH"}},
                 {"player_key": "a", "truth": "controlled_fair",
                  "priority": "LOW"}]
        result = evaluate(cases)
        self.assertEqual(result["scenarios"]["trial"]["missing_players"], 1)

    def test_wilson_zero_false_positives_not_zero_uncertainty(self):
        lo, hi = wilson_interval(0, 20)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)

    def test_production_thresholds_are_not_modified(self):
        self.assertEqual(CONFIG.high_engine_score, .65)
        self.assertEqual(CONFIG.high_critical_score, .65)
        self.assertEqual(CONFIG.very_high_score, .88)
        self.assertEqual(CONFIG.human_stability_fraction, .75)


class ChessFraudGameBenchmark(unittest.TestCase):
    def test_uses_game_labels_and_post_opening_eligible_moves(self):
        rows = []
        for player, game, label in [("a", "g1", True), ("b", "g2", False)]:
            for half_move in range(21, 34):
                rows.append({"player_id": player, "game_id": game,
                             "half_move": half_move, "is_used": True,
                             "is_cheating_player_game": label,
                             "is_cheating_move": False,
                             "move_player": "e2e4",
                             "move_stockfish_1": "e2e4" if label else "d2d4",
                             "move_stockfish_9": "e2e4",
                             "move_stockfish_15": "e2e4"})
        games = player_games(rows)
        self.assertEqual(len(games), 2)
        self.assertEqual(sorted(g["assisted"] for g in games), [False, True])
        report = heldout_baselines(games)
        self.assertTrue(report["holdout_disjoint"])
        self.assertEqual(report["development_player_games"] +
                         report["holdout_player_games"], 2)

    def test_skips_undercovered_and_unknown_game_labels(self):
        rows = [{"player_id": "a", "game_id": "g1", "is_used": True,
                 "move_player": "e2e4", "is_cheating_player_game": True}] * 7
        self.assertEqual(player_games(rows), [])


if __name__ == "__main__":
    unittest.main()
