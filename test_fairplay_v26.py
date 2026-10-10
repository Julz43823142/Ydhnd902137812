"""v26: the original '75% stable' gate was confounded with best-move hits.

Synthetic-only regressions; no names, PGNs, live accounts or internet.
The false-positive fixture remains LOW-eligible only when all the *other*
demanding gameplay-evidence gates are independently satisfied.
"""
import copy
import unittest
from types import SimpleNamespace

from fairplay_config import CONFIG
from fairplay_confirmation import paired_quality_confirmation
from fairplay_sequence import deep_confirmation, sparse_deep_blockers


def game(index, *, opportunities=8, hits=7, paired=8, consistent=7, good_hits=7):
    human = {
        "opportunities": opportunities,
        "hits": hits,
        "hit_lower": 0.0,
        "information": 0.35,
        "quality_excess": 0.25,
        "quality_residual": 0.4,
        "anomaly_strength": 0.45,
        "quality_reference": 0.4,
        "observed_quality": 0.85,
        "hard_opportunities": opportunities,
        "hard_hits": hits,
        "quiet_hits": 4,
        "hard_stable": 2,
        # Previous v25 was unable to pass 75% with two geometric hits.
        "stable_opportunities": 2,
        "stable_hits": 2,
        # New evidence measures *search reliability*, including mistakes.
        "paired_evaluated_opportunities": paired,
        "quality_stable_opportunities": consistent,
        "quality_stable_hits": good_hits,
    }
    return SimpleNamespace(identity=f"synthetic-{index}", deep=True,
                           rating=2200, metrics={"human": copy.deepcopy(human)},
                           fast_metrics={"human": copy.deepcopy(human)})


def candidate(games):
    return {"ids": [g.identity for g in games],
            "class": "blitz", "absolute": True, "personal": False,
            "acute": False, "baseline_ids": []}


class PairedReliability(unittest.TestCase):
    def test_reliable_search_is_not_a_requirement_for_perfect_player_moves(self):
        info = paired_quality_confirmation({
            "opportunities": 53,
            "paired_evaluated_opportunities": 53,
            "quality_stable_opportunities": 44,
            "quality_stable_hits": 41,
            "stable_opportunities": 31,
            "stable_hits": 31,
        }, CONFIG.human_stability_fraction)
        self.assertAlmostEqual(info["fraction"], 44 / 53)
        self.assertEqual(info["hits"], 41)
        self.assertTrue(info["coverage_passed"])
        self.assertTrue(info["fraction_passed"])
        self.assertFalse(info["legacy_fixture"])

    def test_real_review_route_not_blocked_by_double_counted_accuracy(self):
        games = [game(i) for i in range(8)]
        result = deep_confirmation(candidate(games), games)
        self.assertTrue(result["qualified"], result["blockers"])
        self.assertTrue(result["absolute"])
        self.assertAlmostEqual(result["stability_fraction"], 7 / 8)
        self.assertAlmostEqual(result["legacy_geometric_stability_fraction"], 2 / 8)
        self.assertEqual(result["paired_search_coverage_fraction"], 1.0)

    def test_15_game_strong_case_had_one_structural_reliability_blocker(self):
        # Independently constructed synthetic aggregate: 53 difficult
        # opportunities, 41 anomalous hits, 44 paired-quality-consistent
        # positions and only 31 old geometric 'stable' positions.
        games = []
        for i in range(15):
            if i < 8:
                opp, hits, reliable = 4, 4, 4
            elif i == 8:
                opp, hits, reliable = 3, 3, 3
            else:
                opp, hits, reliable = 3, 1, (2 if i >= 12 else 1)
            g = game(i, opportunities=opp, hits=hits,
                     paired=opp, consistent=reliable, good_hits=hits)
            g.metrics["human"]["information"] = 0.304
            g.metrics["human"]["quality_excess"] = 0.1168
            g.metrics["human"]["anomaly_strength"] = 0.419
            g.fast_metrics["human"] = copy.deepcopy(g.metrics["human"])
            # Preserve the old geometric metric as a non-scoring audit.
            g.metrics["human"]["stable_opportunities"] = 3 if i < 8 else 1
            g.metrics["human"]["stable_hits"] = min(
                hits, g.metrics["human"]["stable_opportunities"])
            games.append(g)
        from fairplay_human import period_summary
        s = period_summary(games)
        self.assertEqual(s["opportunities"], 53)
        self.assertEqual(s["hits"], 41)
        self.assertEqual(s["contributors"], 9)
        self.assertEqual(s["quality_stable_opportunities"], 44)
        self.assertEqual(s["quality_stable_hits"], 41)
        self.assertEqual(s["stable_opportunities"], 31)
        reviewed = deep_confirmation(candidate(games), games)
        self.assertTrue(reviewed["absolute"], reviewed["blockers"])
        self.assertGreaterEqual(reviewed["stability_fraction"], .75)
        self.assertLess(reviewed["legacy_geometric_stability_fraction"], .75)

    def test_15_game_sparse_case_stays_unqualified_after_correct_reliability(self):
        games = []
        for i in range(15):
            opp, hits = (6, 4) if i < 2 else (1, 1 if i < 7 else 0)
            reliable = 5 if i < 2 else (1 if i < 11 else 0)
            g = game(i, opportunities=opp, hits=hits,
                     paired=opp, consistent=reliable, good_hits=hits)
            g.fast_metrics["human"] = copy.deepcopy(g.metrics["human"])
            games.append(g)
        from fairplay_human import period_summary
        s = period_summary(games)
        self.assertEqual((s["opportunities"], s["hits"], s["contributors"]), (25, 13, 2))
        self.assertEqual(s["quality_stable_opportunities"], 19)
        reviewed = deep_confirmation(candidate(games), games)
        self.assertFalse(reviewed["qualified"])
        self.assertIn("deep contributor games", reviewed["blockers"])
        self.assertIn("deep anomaly hit lower bound", reviewed["blockers"])
        self.assertNotIn("deep semantic-quality stability", reviewed["blockers"])

    def test_incomplete_paired_contract_blocks_high_regardless_of_hits(self):
        games = [game(i, paired=0, consistent=0, good_hits=0) for i in range(8)]
        result = deep_confirmation(candidate(games), games)
        self.assertFalse(result["qualified"])
        self.assertIn("paired search-quality coverage", result["blockers"])

    def test_search_is_stable_but_missed_moves_do_not_become_hits(self):
        games = [game(i, hits=1, consistent=8, good_hits=1) for i in range(8)]
        result = deep_confirmation(candidate(games), games)
        self.assertFalse(result["qualified"])
        self.assertIn("deep contributor games", result["blockers"])
        self.assertIn("deep anomaly hit lower bound", result["blockers"])

    def test_many_hits_cannot_override_unstable_engine(self):
        games = [game(i, consistent=2, good_hits=2) for i in range(8)]
        result = deep_confirmation(candidate(games), games)
        self.assertFalse(result["qualified"])
        self.assertIn("deep semantic-quality stability", result["blockers"])

    def test_missing_explicit_v26_fields_fails_closed(self):
        result = paired_quality_confirmation({
            "opportunities": 12,
            "paired_evaluated_opportunities": 0,
            "quality_stable_opportunities": 0,
            "quality_stable_hits": 0,
            "stable_opportunities": 12,
            "stable_hits": 12,
        }, .75)
        self.assertFalse(result["coverage_passed"])
        self.assertFalse(result["fraction_passed"])

    def test_invalid_counter_geometry_fails_closed(self):
        for row in (
            {"opportunities": 5, "paired_evaluated_opportunities": 3,
             "quality_stable_opportunities": 4, "quality_stable_hits": 1},
            {"opportunities": 5, "paired_evaluated_opportunities": 5,
             "quality_stable_opportunities": 4, "quality_stable_hits": 5},
        ):
            result = paired_quality_confirmation(row, .75)
            self.assertFalse(result["fraction_passed"])

    def test_sparse_route_also_measures_search_reliability_not_player_perfection(self):
        games = [game(i) for i in range(8)]
        info = deep_confirmation(candidate(games), games)
        from fairplay_human import period_summary
        deep = period_summary(games)
        fast = period_summary(games, fast=True)
        period = candidate(games)
        period["summary"] = fast
        blockers = sparse_deep_blockers(period, {
            "games": 8, "summary": deep, "paired_fast": fast,
        })
        self.assertNotIn("deep semantic-quality stability", blockers)
        self.assertNotIn("paired search-quality coverage", blockers)


if __name__ == "__main__":
    unittest.main()
