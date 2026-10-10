"""Regression tests for v25 intermittent sparse-game observations.

Synthetic, zero external players/PGNs, no engines or live scores.
"""
import unittest
from types import SimpleNamespace
from fairplay_intermit_audit import summarize_intermit


def make_game(index, *, kind="blitz", control="180+0", decisions=3,
              fast_hits=0, deep_hits=0, opportunities=3,
              deep=True, rating=420, control_index=None):
    def metrics(hits):
        return {
            "decisions": decisions,
            "critical": min(decisions, opportunities),
            "blunders": max(0, decisions-hits),
            "human": {"opportunities": opportunities, "hits": hits,
                      "stable_hits": min(deep_hits, hits)}
        }
    return SimpleNamespace(
        identity=f"synthetic-secret-{index}", ended=1000+index,
        time_class=kind, time_control=control, control_index=(
            index if control_index is None else control_index),
        rated=True, probe_only=False, deep=deep, rating=rating,
        fast_metrics=metrics(fast_hits), metrics=metrics(deep_hits))


class IntermittentCoverage(unittest.TestCase):
    def test_short_games_are_counted_without_becoming_new_votes(self):
        games = [make_game(i, fast_hits=int(i in (1, 2, 3)),
                           deep_hits=int(i in (1, 2, 3)))
                 for i in range(10)]
        report = summarize_intermit(games)
        self.assertFalse(report["scoring_influence"])
        self.assertEqual(report["deep_sample"]["games"], 10)
        self.assertEqual(report["deep_sample"]["fully_scored_games"], 0)
        self.assertEqual(report["deep_sample"]["short_games"], 10)
        self.assertEqual(report["classes"]["blitz"]["opportunities"], 30)
        self.assertEqual(report["classes"]["blitz"]["hits"], 3)
        self.assertNotIn("synthetic-secret", str(report))

    def test_fast_selected_period_deep_keeps_all_misses(self):
        games = [make_game(i, fast_hits=1 if 2 <= i <= 6 else 0,
                           deep_hits=1 if i in (2, 4) else 0)
                 for i in range(10)]
        report = summarize_intermit(games)
        selected = report["chronological_research_candidate"]
        self.assertIsNotNone(selected)
        self.assertGreaterEqual(selected["fast"]["hits"], selected["deep"]["hits"])
        self.assertEqual(selected["deep"]["games"], selected["games"])
        self.assertGreaterEqual(selected["deep"]["opportunities"],
                                selected["deep"]["hits"])
        self.assertGreater(report["windows_examined"], 0)

    def test_changing_deep_outcomes_cannot_change_fast_discovery(self):
        games = [make_game(i, fast_hits=(1 if i in (3, 4, 5) else 0),
                           deep_hits=0) for i in range(12)]
        left = summarize_intermit(games)["chronological_research_candidate"]
        for game in games:
            game.metrics["human"]["hits"] = 3
        right = summarize_intermit(games)["chronological_research_candidate"]
        self.assertEqual(left["games"], right["games"])
        self.assertEqual(left["fast"], right["fast"])
        self.assertNotEqual(left["deep"], right["deep"])

    def test_missing_fast_data_cannot_borrow_deep_data(self):
        games = [make_game(i, fast_hits=1, deep_hits=3) for i in range(9)]
        for game in games:
            game.fast_metrics = {}
        report = summarize_intermit(games)
        self.assertIsNone(report["chronological_research_candidate"])
        self.assertEqual(report["deep_sample"]["hits"], 27)

    def test_intervening_unscanned_index_breaks_contiguity(self):
        games = [make_game(i, fast_hits=1, deep_hits=1)
                 for i in (0, 1, 10, 11)]
        self.assertIsNone(summarize_intermit(games)["chronological_research_candidate"])

    def test_time_control_and_class_never_pool_into_window(self):
        games = [make_game(i, fast_hits=1, deep_hits=1,
                           control="180+0" if i%2 else "300+0")
                 for i in range(8)]
        self.assertIsNone(summarize_intermit(games)["chronological_research_candidate"])

    def test_no_inflation_from_casual_or_unreviewed_game(self):
        good = [make_game(i, fast_hits=1, deep_hits=1) for i in range(5)]
        bad = make_game(8, fast_hits=1, deep_hits=1, deep=False)
        casual = make_game(9, fast_hits=1, deep_hits=1)
        casual.rated = False
        report = summarize_intermit(good + [bad, casual])
        self.assertEqual(report["deep_sample"]["games"], 5)

    def test_fixed_chronological_halves_keep_misses_and_do_not_leak(self):
        games = [make_game(i, decisions=3, opportunities=3,
                           fast_hits=1, deep_hits=(0 if i < 8 else 2))
                 for i in range(16)]
        report = summarize_intermit(games)
        item = report["predeclared_half_comparisons"][0]
        self.assertEqual(item["games"], 16)
        self.assertEqual(item["earlier"]["hits"], 0)
        self.assertEqual(item["later"]["hits"], 16)
        self.assertEqual(item["earlier"]["opportunities"], 24)
        self.assertEqual(item["later"]["opportunities"], 24)
        self.assertEqual(item["deep_hit_rate_difference"], round(16/24, 4))
        self.assertNotIn("synthetic-secret", str(report))

    def test_model_rating_warning_is_descriptive_only(self):
        games = [make_game(i, rating=200 if i<3 else 1800) for i in range(6)]
        report = summarize_intermit(games)
        self.assertEqual(report["maia_rating_domain"]["below_600_chesscom_rating_games"], 3)
        self.assertFalse(report["scoring_influence"])


if __name__ == "__main__":
    unittest.main()
