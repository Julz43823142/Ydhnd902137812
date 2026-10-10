"""v27: exact opportunity-feasibility and episodic manual-review regressions.

Only constructed synthetic players and opportunities; no labels or private PGNs.
"""
import math
import unittest
from types import SimpleNamespace
from fairplay_config import CONFIG
from fairplay_episodic import (
    conditional_scan_probability, opportunity_feasibility, episodic_audit, integrate)


def sample(i, n, hits, *, kind="blitz", control="180+0",
           fast_hits=None, fast_opportunities=None, paired=None, stable=None,
           deep=True, rated=True):
    deep_human = {
        "opportunities": n, "hits": hits,
        "paired_evaluated_opportunities": n if paired is None else paired,
        "quality_stable_hits": hits if stable is None else stable,
    }
    fast_human = {
        "opportunities": n if fast_opportunities is None else fast_opportunities,
        "hits": hits if fast_hits is None else fast_hits,
    }
    return SimpleNamespace(
        identity=f"synthetic-{kind}-{i}", deep=deep, rated=rated, probe_only=False,
        time_class=kind, time_control=control,
        metrics={"human":deep_human}, fast_metrics={"human":fast_human})


def episodic_group():
    # One confirmed 5/5 game within 51 opportunity positions containing
    # 20 hits, from many games with isolated hard positions. All misses stay.
    group = [sample(0, 5, 5, fast_hits=3, fast_opportunities=3)]
    group.extend(sample(i + 1, 2, 2 if i == 0 else 1)
                 for i in range(12))
    group.extend(sample(i + 13, 1, 1) for i in range(2))
    group.extend(sample(i + 15, 1, 0) for i in range(20))
    group.extend(sample(i + 35, 0, 0) for i in range(17))
    assert len(group) == 52
    assert sum(g.metrics["human"]["opportunities"] for g in group) == 51
    assert sum(g.metrics["human"]["hits"] for g in group) == 20
    return group


class EpisodicCoverage(unittest.TestCase):
    def test_exact_hypergeometric_one_five_opportunity_game(self):
        probability = conditional_scan_probability([5] + [2] * 12 + [1] * 22, 20)
        self.assertAlmostEqual(
            probability, math.comb(20, 5) / math.comb(51, 5), places=12)

    def test_impossibility_is_not_fair_play(self):
        group = episodic_group()
        report = opportunity_feasibility(group, CONFIG)
        blitz = report["blitz"]["deep"]
        self.assertEqual(blitz["max_difficult_opportunities_in_one_game"], 5)
        self.assertFalse(blitz["absolute_route_exposure_possible"])
        self.assertEqual(blitz["games_capable_of_acute_minimum"], 0)
        self.assertGreater(blitz["required_contributor_games"],
                           blitz["max_two_hit_contributor_games"])

    def test_exact_episode_promotes_only_to_moderate(self):
        group = episodic_group()
        result = SimpleNamespace(priority="LOW", reasons=[], diagnostics={})
        result = integrate(result, group, CONFIG)
        self.assertEqual(result.priority, "MODERATE")
        self.assertEqual(result.diagnostics["moderate_path"],
                         "Deep-confirmed concentrated episodic gameplay (exploratory)")
        scan = result.diagnostics["opportunity_feasibility_audit"]["episodic"]
        self.assertEqual(scan["comparisons"], 1)
        self.assertTrue(scan["exploratory_flag"])
        self.assertEqual(scan["strata"][0]["deep_confirmed_episodic_games"], 1)
        self.assertNotIn("synthetic-", str(result.diagnostics))

    def test_no_one_game_cherry_pick_and_misses_change_inference(self):
        group = episodic_group()
        # Five attractive hits are not rare if nearly all the other
        # comparable difficult opportunities were also hits.
        for g in group[1:]:
            hm = g.metrics["human"]
            hm["hits"] = hm["opportunities"]
        audit = episodic_audit(group, CONFIG)
        self.assertFalse(audit["exploratory_flag"])

    def test_unconfirmed_fast_or_deep_search_cannot_promote(self):
        for mode in ("no_fast", "no_deep_pair", "quality_drift", "missing_fast"):
            group = episodic_group()
            one = group[0]
            if mode == "no_fast":
                one.fast_metrics["human"]["hits"] = 0
            elif mode == "no_deep_pair":
                one.metrics["human"]["paired_evaluated_opportunities"] = 4
            elif mode == "quality_drift":
                one.metrics["human"]["quality_stable_hits"] = 2
            else:
                one.fast_metrics = {}
            with self.subTest(mode=mode):
                audit = episodic_audit(group, CONFIG)
                self.assertFalse(audit["exploratory_flag"])
                result = integrate(SimpleNamespace(priority="LOW", diagnostics={},
                                                   reasons=[]), group, CONFIG)
                self.assertEqual(result.priority, "LOW")

    def test_existing_high_never_downgraded_or_duplicated(self):
        result = SimpleNamespace(priority="HIGH", reasons=["existing"], diagnostics={})
        output = integrate(result, episodic_group(), CONFIG)
        self.assertEqual(output.priority, "HIGH")
        self.assertEqual(output.reasons, ["existing"])

    def test_multiple_strata_bonferroni_includes_failures(self):
        group = episodic_group()
        second = [sample(i, 5 if i == 0 else 2,
                         0 if i == 0 else (1 if i < 14 else 0),
                         control="300+0") for i in range(25)]
        scan = episodic_audit(group + second, CONFIG)
        self.assertEqual(scan["comparisons"], 2)
        self.assertFalse(scan["exploratory_flag"])
        self.assertEqual(sum(bool(row["exploratory_flag"]) for row in scan["strata"]), 0)

    def test_bullet_unrated_and_unreviewed_not_promoted(self):
        g = episodic_group()
        for i, item in enumerate(g):
            item.time_class = "bullet" if i % 2 else "blitz"
        self.assertFalse(episodic_audit(g, CONFIG)["exploratory_flag"])
        g = episodic_group()
        g[0].rated = False
        self.assertFalse(episodic_audit(g, CONFIG)["exploratory_flag"])
        g = episodic_group()
        g[0].deep = False
        self.assertFalse(episodic_audit(g, CONFIG)["exploratory_flag"])

    def test_malformed_counters_fail_closed(self):
        g = episodic_group()
        g[1].metrics["human"]["hits"] = 20
        self.assertEqual(episodic_audit(g, CONFIG)["comparisons"], 0)

    def test_no_episode_when_high_ratio_is_just_two_moves(self):
        games = [sample(i, 2, 2 if i < 9 else 0) for i in range(25)]
        self.assertFalse(episodic_audit(games, CONFIG)["exploratory_flag"])
        self.assertEqual(episodic_audit(games, CONFIG)["comparisons"], 0)


if __name__ == "__main__":
    unittest.main()
