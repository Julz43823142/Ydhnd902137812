"""Synthetic regressions; no named account or private game records."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fairplay_accuracy_blocks import inspect
from fairplay_recent_shift import evidence, integrate
from fairplay_config import CONFIG
from fairplay_v21 import broad_and_core, discovery_extras
from test_fairplay_v28 import _game as engine_game, _result


def game(i, accuracy=71, *, control='180+0', kind='blitz',
         opponent=700, rating=700, rated=True, deep=False):
    return SimpleNamespace(
        identity=f'block-{i}',ended=1700000000+i*600,
        rated=rated,probe_only=False,time_class=kind,time_control=control,
        rating=rating,opponent_rating=opponent,accuracy=accuracy,deep=deep,
        result='Loss',control_index=i,
        fast_metrics={'robust_cpl':40},metrics={'robust_cpl':40})


def strong_maia(games):
    n=len(games)
    return {'contributors':n,'contributor_weight':float(n),
            'effective_positions':3.0*n,'signed_excess':.24,
            'information':.21,'positions':3*n,'stable':3*n}


def missing_maia(games):
    return {'contributors':0,'contributor_weight':0.,
            'effective_positions':0.,'signed_excess':0.,
            'information':0.,'positions':0,'stable':0}


class AccuracyBlocks(unittest.TestCase):
    def test_700_elo_71_to_95_jump_finds_latest_ten_without_wins(self):
        rows=[game(i) for i in range(45)]+[game(i,95+i%5) for i in range(45,55)]
        audit,candidates=inspect(rows)
        self.assertTrue(candidates,audit)
        self.assertTrue(audit['bullet_excluded'])
        self.assertGreater(audit['best']['accuracy_rise'],20)
        self.assertEqual(audit['best']['similar_rating_opponents'],10)
        self.assertEqual(candidates[0]['ids'],tuple(g.identity for g in rows[-10:]))
        self.assertEqual(audit['rated_blitz_rapid_games'],55)

    def test_includes_fixed_blocks_and_last_ten_not_lost_at_boundary(self):
        rows=[game(i,70) for i in range(43)]+[game(i,96) for i in range(43,53)]
        audit,candidates=inspect(rows)
        self.assertTrue(candidates)
        self.assertTrue(audit['best']['latest_control_window'])

    def test_all_bullet_excluded_even_with_perfect_accuracy(self):
        rows=[game(i,65) for i in range(30)]
        rows.extend(game(i,99,kind='bullet',control='60+0') for i in range(30,60))
        audit,candidates=inspect(rows)
        self.assertFalse(candidates)
        self.assertEqual(audit['rated_blitz_rapid_games'],30)

    def test_weaker_opposition_not_used_as_cheating_discovery(self):
        rows=[game(i) for i in range(40)]
        rows.extend(game(i,99,opponent=200) for i in range(40,50))
        audit,candidates=inspect(rows)
        self.assertFalse(candidates)
        self.assertEqual(audit['discovery_windows'],0)

    def test_no_fabricated_missing_accuracy_or_baseline(self):
        rows=[game(i,None) for i in range(40)]
        rows.extend(game(i,99) for i in range(40,50))
        audit,candidates=inspect(rows)
        self.assertFalse(candidates)
        self.assertEqual(audit['best']['older_accuracy_reported'],0)
        rows=[game(i) for i in range(40)]
        rows.extend(game(i,99 if i<47 else None) for i in range(40,50))
        self.assertFalse(inspect(rows)[1])

    def test_same_control_and_player_rating_required_for_baseline(self):
        rows=[game(i,70,control='300+0') for i in range(40)]
        rows.extend(game(i,97,control='180+0') for i in range(40,50))
        self.assertFalse(inspect(rows)[1])
        rows=[game(i,70,rating=1300,opponent=1300) for i in range(40)]
        rows.extend(game(i,97) for i in range(40,50))
        self.assertFalse(inspect(rows)[1])

    def test_maximum_250_nonbullet_games_and_no_duplicate_identity(self):
        rows=[game(i,75) for i in range(260)]
        rows.append(copy.deepcopy(rows[-1]))
        audit,candidates=inspect(rows)
        self.assertEqual(audit['rated_blitz_rapid_games'],250)
        self.assertGreaterEqual(audit['windows_evaluated'],25)
        self.assertEqual(candidates,[])

    def test_nonrated_games_never_contribute(self):
        rows=[game(i) for i in range(30)]
        rows.extend(game(i,99,rated=False) for i in range(30,40))
        self.assertFalse(inspect(rows)[1])


class DeepSelection(unittest.TestCase):
    def test_full_flagged_older_window_uses_optional_slots_and_preserves_latest50(self):
        rows=[game(i) for i in range(500)]
        plan=broad_and_core(rows,broad_count=500,deep_count=100)
        flagged={'ids':tuple(g.identity for g in rows[330:340])}
        chosen=discovery_extras(plan,max_extra=25,accuracy_windows=[flagged])
        deep_ids={g.identity for g in chosen.deep}
        self.assertTrue({g.identity for g in rows[-50:]}.issubset(deep_ids))
        self.assertTrue(set(flagged['ids']).issubset(deep_ids))
        self.assertLessEqual(len(chosen.deep),125)

    def test_incomplete_older_window_is_not_partially_prioritized(self):
        rows=[game(i) for i in range(500)]
        plan=broad_and_core(rows,broad_count=500,deep_count=100)
        flagged={'ids':tuple(g.identity for g in rows[300:309])+('absent-id',)}
        ordinary=discovery_extras(plan,max_extra=25)
        with_window=discovery_extras(plan,max_extra=25,accuracy_windows=[flagged])
        self.assertEqual({g.identity for g in ordinary.deep},
                         {g.identity for g in with_window.deep})


class ConfirmedReview(unittest.TestCase):
    def setUp(self):
        self.games=[engine_game(i) for i in range(10)]
        for g in self.games:
            g.rating=g.opponent_rating=700
        self.candidate={
            'ids':tuple(g.identity for g in self.games),
            'time_class':'blitz','time_control':'180+0',
            'window_end_time':self.games[-1].ended,
            'accuracy_rise':24.,'mean_accuracy':96.,
        }

    def test_high_only_with_full_engine_and_maia_proof(self):
        with patch('fairplay_recent_shift.maia_summary',side_effect=strong_maia):
            r=integrate(_result(),self.games,CONFIG,[self.candidate])
        self.assertEqual(r.priority,'HIGH')
        self.assertEqual(r.diagnostics['recent_engine_shift']['windows_confirmed'],1)
        self.assertIn('Maia',r.diagnostics['high_path'])

    def test_maia_missing_blocks_high_even_when_engine_hits_all(self):
        with patch('fairplay_recent_shift.maia_summary',side_effect=missing_maia):
            r=integrate(_result(),self.games,CONFIG,[self.candidate])
        self.assertEqual(r.priority,'LOW')
        self.assertEqual(r.diagnostics['recent_engine_shift']['windows_confirmed'],0)

    def test_accuracy_alone_cannot_make_high(self):
        games=[engine_game(i,hits=0,fast_hits=0,quiet=0) for i in range(10)]
        with patch('fairplay_recent_shift.maia_summary',side_effect=strong_maia):
            r=integrate(_result(),games,CONFIG,[self.candidate])
        self.assertEqual(r.priority,'LOW')

    def test_missing_deep_game_or_partial_coverage_blocks_high(self):
        games=copy.deepcopy(self.games)
        games[6].deep=False
        with patch('fairplay_recent_shift.maia_summary',side_effect=strong_maia):
            self.assertEqual(integrate(_result(),games,CONFIG,[self.candidate]).priority,'LOW')
            self.assertEqual(integrate(_result(complete=False),self.games,CONFIG,
                                       [self.candidate]).priority,'LOW')

    def test_discovery_without_approved_personal_baseline_is_never_high(self):
        with patch('fairplay_recent_shift.maia_summary',side_effect=strong_maia):
            r=integrate(_result(),self.games,CONFIG,[])
        self.assertEqual(r.priority,'LOW')

    def test_five_strong_five_weak_engine_halves_cannot_score(self):
        games=[engine_game(i,hits=3 if i<5 else 0,
                           quiet=2 if i<5 else 0,
                           fast_hits=3 if i<5 else 0) for i in range(10)]
        with patch('fairplay_recent_shift.maia_summary',side_effect=strong_maia):
            r=integrate(_result(),games,CONFIG,[self.candidate])
        self.assertEqual(r.priority,'LOW')

    def test_unverified_bullet_and_mixed_clock_never_high(self):
        for kind in ('bullet','blitz'):
            games=[engine_game(i,kind=kind,control='60+0' if kind=='bullet' else
                               '180+0' if i<5 else '300+0') for i in range(10)]
            with patch('fairplay_recent_shift.maia_summary',side_effect=strong_maia):
                r=integrate(_result(),games,CONFIG,[self.candidate])
            self.assertEqual(r.priority,'LOW')

    def test_false_positive_known_maia_but_no_stockfish_is_low(self):
        with patch('fairplay_recent_shift.maia_summary',side_effect=strong_maia):
            self.assertFalse(evidence([engine_game(i,hits=1,fast_hits=1)
                                       for i in range(10)],CONFIG)['passed'])


if __name__=='__main__':
    unittest.main()
