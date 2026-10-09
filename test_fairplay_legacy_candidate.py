"""Regression tests: HIGH candidates must be evaluated independently.

No actual users, account status, or case labels are used as scoring features.
All fixtures are synthetic, and the existing conservative gates are preserved.
"""
import unittest
from unittest.mock import patch

import fairplay_scoring as scoring
from fairplay_clusters import find_clusters, group_record, select_gate_candidates
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
        self.assertIn(r.priority, ("HIGH", "VERY HIGH"))
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
        for g in games[50:60]:
            g.time_class = "rapid"
            g.time_control = "1800+0"
        # Rapid clocks must not be attributed to a Blitz candidate. Results
        # remain independently eligible evidence, so HIGH itself is not barred.
        later = group_record(games[50:60], "chronological", fast=True)
        later["high_qualifying"] = True
        r = self.report_with_candidates(games, earlier, later)
        checks = r.clusters["legacy_scope_selection"]["candidate_checks"]
        early = next(row for row in checks if row["class"] == "blitz")
        self.assertLess(early["timing"], 0.5)

    def test_partial_review_cannot_promote_alternate_period(self):
        games, earlier, later = _sample()
        r = self.report_with_candidates(games, earlier, later, partial=True)
        self.assertFalse(r.clusters["legacy_scope_selection"]["selected_alternate"])
        self.assertFalse(r.clusters["legacy_scope_selection"]["incomplete_scan_guard"] is False)

    def test_fully_occupied_fast_windows_cannot_hide_different_control(self):
        from fairplay_config import CONFIG
        from dataclasses import replace
        config=replace(CONFIG,baseline_candidate_limit=8)
        def candidate(i, control, *, persistent=True):
            return {'kind':'chronological','time_class':'blitz' if control=='180+0' else 'rapid',
                    'time_control':control,'rated':True,'persistent':persistent,
                    'strength':1-i*.01,'end':1700000000+i,
                    'ids':list(range(i,i+8))}
        dominant=[candidate(i,'180+0') for i in range(8)]
        minority=candidate(9,'600+0')
        selected=select_gate_candidates(dominant,dominant+[minority],config)
        self.assertEqual(len(selected),config.baseline_candidate_limit)
        self.assertIs(selected[0],dominant[0])
        self.assertIn(minority,selected)
        self.assertEqual(sum(row['time_class']=='rapid' for row in selected),1)

    def test_nonpersistent_ranked_excerpt_cannot_take_a_gate_slot(self):
        from dataclasses import replace
        from fairplay_config import CONFIG
        config=replace(CONFIG,baseline_candidate_limit=2)
        base=[{'time_class':'blitz','time_control':'180+0','rated':True,
               'persistent':True,'kind':'chronological'} for _ in range(2)]
        ranked={'time_class':'rapid','time_control':'600+0','rated':True,
                'persistent':False,'kind':'ranked'}
        selected=select_gate_candidates(base,base+[ranked],config)
        self.assertEqual(selected,base)

    def test_real_discovery_retains_a_second_class_under_window_crowding(self):
        from dataclasses import replace
        from fairplay_config import CONFIG
        config=replace(CONFIG,baseline_candidate_limit=12)
        games=[game(i, i>=20 and i<60, control='180+0') for i in range(65)]
        games += [game(70+i,True,control='600+0') for i in range(12)]
        for g in games[65:]:
            g.time_class='rapid'
        for cohort in (games[:65],games[65:]):
            for i,g in enumerate(cohort):g.control_index=i
        found=find_clusters(games,config=config,fast=True)
        self.assertLessEqual(len(found['gate_candidates']),12)
        self.assertTrue(any(row['time_class']=='rapid' for row in found['gate_candidates']))
        self.assertTrue(all(row['persistent'] for row in found['gate_candidates']))

    def test_candidate_limit_not_same_as_twelve_display_rows(self):
        found = find_clusters([game(i, i >= 30) for i in range(60)], fast=True)
        self.assertLessEqual(len(found["candidates"]), 12)
        self.assertGreaterEqual(len(found["gate_candidates"]), len(found["candidates"]))


if __name__ == "__main__":
    unittest.main()
