"""Synthetic, account-free ten-game deep gameplay incident regressions."""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from fairplay_burst import analyze, integrate
from fairplay_config import CONFIG


def _game(i, *, hard=3, hits=3, quiet=2, fast_hits=None, deep=True,
          control="180+0", kind="blitz", fast=True, peer=True):
    human = {
        "opportunities":hard, "hits":hits, "quiet_hits":quiet,
        "information":.52, "quality_excess":.27,
        "anomaly_strength":.49,
        "paired_evaluated_opportunities":hard,
        "quality_stable_opportunities":hard,
        "quality_stable_hits":hits,
    }
    fast_human = copy.deepcopy(human)
    fast_human['hits'] = hits if fast_hits is None else fast_hits
    return SimpleNamespace(
        identity=f"fixture-{i}",ended=1710000000 + 200 * i,
        rated=True,probe_only=False,deep=deep,
        time_class=kind,time_control=control,control_index=i,
        rating=1550,opponent_rating=1550 if peer else 300,
        fast_metrics={"human":fast_human} if fast else {},
        metrics={"human":human},result="Loss",
        score=0.0,
    )


def _result(priority="LOW",complete=True):
    return SimpleNamespace(
        priority=priority,deep_confirmed=False,partial=False,
        diagnostics={},reasons=[],
        coverage={"primary_engine_complete":complete,
                  "required_deep_complete":complete},
    )


class TenGameBurst(unittest.TestCase):
    def test_ten_short_games_trigger_high_without_six_opportunities_each(self):
        games=[_game(i) for i in range(10)]
        report=analyze(games)
        self.assertTrue(report["qualified"],report)
        self.assertEqual(report["window_games"],10)
        self.assertEqual(report["windows_examined"],1)
        self.assertEqual(report["best"]["deep_hits"],30)
        result=integrate(_result(),games)
        self.assertEqual(result.priority,"HIGH")
        self.assertTrue(result.deep_confirmed)
        self.assertIn("Ten-game",result.diagnostics['high_path'])
        self.assertNotIn("VERY HIGH",result.priority)

    def test_ten_games_with_only_a_few_engine_hits_remain_low(self):
        games=[_game(i,hits=1,fast_hits=1) for i in range(10)]
        result=integrate(_result(),games)
        self.assertEqual(result.priority,"LOW")
        self.assertFalse(result.diagnostics['ten_game_burst']['qualified'])

    def test_five_perfect_games_and_five_bad_games_do_not_fake_replication(self):
        games=[_game(i,hits=3 if i<5 else 0,
                     quiet=2 if i<5 else 0) for i in range(10)]
        self.assertFalse(analyze(games)['qualified'])

    def test_deep_search_missing_game_blocks_burst(self):
        games=[_game(i) for i in range(10)]
        games[5].deep=False
        self.assertFalse(analyze(games)["qualified"])
        self.assertEqual(integrate(_result(),games).priority,"LOW")

    def test_unverified_fast_search_cannot_be_deep_discovered(self):
        games=[_game(i) for i in range(10)]
        games[3].fast_metrics={}
        self.assertFalse(analyze(games)['qualified'])

    def test_all_fast_hits_and_deep_misses_block_high(self):
        games=[_game(i,hits=0,fast_hits=3,quiet=0) for i in range(10)]
        self.assertFalse(analyze(games)['qualified'])

    def test_missing_paired_searches_block_high(self):
        games=[_game(i) for i in range(10)]
        for g in games:
            g.metrics['human']['paired_evaluated_opportunities']=0
            g.metrics['human']['quality_stable_opportunities']=0
            g.metrics['human']['quality_stable_hits']=0
        self.assertFalse(analyze(games)['qualified'])

    def test_shallow_quality_disagreement_blocks_high(self):
        games=[_game(i) for i in range(10)]
        for g in games:
            g.metrics['human']['quality_stable_opportunities']=1
            g.metrics['human']['quality_stable_hits']=1
        self.assertFalse(analyze(games)['qualified'])

    def test_quiet_brilliance_must_be_spread_across_games(self):
        games=[_game(i,quiet=0 if i>=3 else 3) for i in range(10)]
        self.assertFalse(analyze(games)['qualified'])

    def test_no_high_on_outcomes_or_weak_opponents_alone(self):
        games=[_game(i,hits=0,fast_hits=0,quiet=0,peer=False)
               for i in range(10)]
        for g in games:
            g.result="Win"
            g.score=1.0
        self.assertFalse(analyze(games)['qualified'])
        self.assertEqual(integrate(_result(),games).priority,"LOW")

    def test_mismatched_clock_controls_are_not_combined(self):
        games=[_game(i,control="180+0" if i<5 else "300+0")
               for i in range(10)]
        self.assertEqual(analyze(games)['windows_examined'],0)
        self.assertFalse(analyze(games)['qualified'])

    def test_unselected_intervening_original_game_blocks_high(self):
        games=[_game(i) for i in range(10)]
        games[5].control_index=6
        games[6].control_index=7
        games[7].control_index=8
        games[8].control_index=9
        games[9].control_index=10
        self.assertEqual(analyze(games)['windows_examined'],0)

    def test_no_shortcut_to_high_when_any_review_is_partial(self):
        games=[_game(i) for i in range(10)]
        result=_result(complete=False)
        result=integrate(result,games)
        self.assertEqual(result.priority,'LOW')
        self.assertTrue(result.diagnostics['ten_game_burst']['qualified'])
        self.assertFalse(result.diagnostics['ten_game_burst']['coverage_gate_passed'])

    def test_already_high_priority_never_becomes_very_high(self):
        games=[_game(i) for i in range(10)]
        result=integrate(_result(priority='HIGH'),games)
        self.assertEqual(result.priority,'HIGH')
        result=integrate(_result(priority='VERY HIGH'),games)
        self.assertEqual(result.priority,'VERY HIGH')

    def test_bullet_is_not_silently_combined_with_blitz(self):
        games=[_game(i,kind='bullet',control='60+0') for i in range(10)]
        self.assertFalse(analyze(games)['qualified'])

    def test_overlapping_sliding_windows_count_as_one_incident(self):
        games=[_game(i) for i in range(15)]
        report=analyze(games)
        self.assertEqual(report['windows_examined'],6)
        self.assertEqual(report['deep_confirmed_windows'],6)
        self.assertTrue(report['qualified'])
        result=integrate(_result(),games)
        self.assertEqual(result.priority,'HIGH')

    def test_early_ten_game_cheating_burst_within_latest_fifty_is_not_lost(self):
        games=[_game(i) for i in range(50)]
        for g in games[10:]:
            g.fast_metrics['human']['hits']=0
            g.metrics['human']['hits']=0
            g.metrics['human']['quality_stable_hits']=0
            g.metrics['human']['quiet_hits']=0
        # The strong ten-game event was followed by forty ordinary games.
        # A last-20-only detector silently misses it despite full deep data.
        report=analyze(games)
        self.assertEqual(report['rated_latest_scope'],50)
        self.assertEqual(report['windows_examined'],41)
        self.assertTrue(report['qualified'])
        self.assertEqual(report['best']['deep_hits'],30)

    def test_burst_older_than_actual_last_fifty_is_excluded(self):
        games=[_game(i) for i in range(60)]
        for g in games[10:]:
            g.fast_metrics['human']['hits']=0
            g.metrics['human']['hits']=0
            g.metrics['human']['quality_stable_hits']=0
            g.metrics['human']['quiet_hits']=0
        report=analyze(games)
        self.assertFalse(report['qualified'])
        self.assertEqual(report['recent_rated_games_selected'],50)

    def test_elapsed_calendar_gap_does_not_drop_first_part_of_latest_fifty(self):
        games=[_game(i) for i in range(50)]
        for g in games[10:]:
            g.ended+=90*86400
            g.fast_metrics['human']['hits']=0
            g.metrics['human']['hits']=0
            g.metrics['human']['quality_stable_hits']=0
            g.metrics['human']['quiet_hits']=0
        self.assertTrue(analyze(games)['qualified'])

    def test_genuinely_suspicious_ten_games_can_span_low_opponent_rating(self):
        games=[_game(i,peer=False) for i in range(10)]
        self.assertTrue(analyze(games)['qualified'])
        # The human and engine evidence is what matters; weak-opponent wins
        # *alone* never qualify. Post-error moves are excluded upstream.

    def test_authoritative_data_is_not_read_from_results_or_accuracy(self):
        games=[_game(i) for i in range(10)]
        base=analyze(games)['qualified']
        for g in games:
            g.result='Win'
            g.score=1.0
            g.accuracy=100
        self.assertEqual(analyze(games)['qualified'],base)


if __name__=='__main__':
    unittest.main()
