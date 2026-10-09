"""Regression tests: HIGH candidates must be evaluated independently.

No actual users, account status, or case labels are used as scoring features.
All fixtures are synthetic, and the existing conservative gates are preserved.
"""
import unittest
from unittest.mock import patch

import fairplay_scoring as scoring
from fairplay_clusters import find_clusters, group_record
from test_fairplay_v4 import game, report


def _sample(*, late_deep=False, early_clocks=True):
    # Both chronological windows have strong synthetic gameplay. A later,
    # unconfirmed window must not mask a distinct earlier deep-confirmed one.
    games = [
        game(i, 30 <= i < 40 or 50 <= i < 60,
             clocks=early_clocks if 30 <= i < 40 else True,
             deep=(late_deep if 50 <= i < 60 else True))
        for i in range(60)
    ]
    for index, g in enumerate(games):
        g.control_index = index
    earlier = group_record(games[30:40], "chronological", fast=True)
    later = group_record(games[50:60], "chronological", fast=True)
    for item in (earlier, later):
        item["high_qualifying"] = True
    return games, earlier, later


class IndependentCandidateSelection(unittest.TestCase):
    @staticmethod
    def report_with_candidates(games, earlier, later, *, partial=False):
        original = find_clusters(games, fast=True)
        found = {
            **original,
            "strongest": later,
            "gate_candidates": [later, earlier],
            "candidates": [later, earlier],
        }
        with patch.object(scoring, "find_clusters", return_value=found):
            return report(games, partial=partial)

    def test_confirmed_earlier_period_not_masked_by_stronger_unconfirmed_period(self):
        games, earlier, later = _sample()
        r = self.report_with_candidates(games, earlier, later)
        self.assertEqual(r.priority, "HIGH")
        self.assertTrue(r.clusters["legacy_scope_selection"]["selected_alternate"])
        self.assertEqual(r.clusters["strongest"]["ids"], earlier["ids"])
        self.assertEqual(r.diagnostics["high_path"], "Personal anomaly with independent support")
        self.assertTrue(r.clusters["deep"]["confirmed"])

    def test_two_unconfirmed_periods_remain_below_high(self):
        games, earlier, later = _sample()
        for g in games[30:40]:
            g.deep = False
        r = self.report_with_candidates(games, earlier, later)
        self.assertNotIn(r.priority, ("HIGH", "VERY HIGH"))
        self.assertFalse(r.clusters["legacy_scope_selection"]["selected_alternate"])

    def test_no_cross_period_clock_borrowing(self):
        games, earlier, later = _sample(early_clocks=False)
        r = self.report_with_candidates(games, earlier, later)
        self.assertNotIn(r.priority, ("HIGH", "VERY HIGH"))

    def test_partial_review_cannot_promote_alternate_period(self):
        games, earlier, later = _sample()
        r = self.report_with_candidates(games, earlier, later, partial=True)
        self.assertFalse(r.clusters["legacy_scope_selection"]["selected_alternate"])
        self.assertFalse(r.clusters["legacy_scope_selection"]["incomplete_scan_guard"] is False)

    def test_candidate_limit_not_same_as_twelve_display_rows(self):
        found = find_clusters([game(i, i >= 30) for i in range(60)], fast=True)
        self.assertLessEqual(len(found["candidates"]), 12)
        self.assertGreaterEqual(len(found["gate_candidates"]), len(found["candidates"]))


if __name__ == "__main__":
    unittest.main()
