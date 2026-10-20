"""Safety and detection regressions for Method 3 time-control and opponent reliability.

Synthetic labels are NOT known fair-play outcomes. Tests establish the
intended decision invariants and guard against predictable false positives.
"""
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from fairplay_method3 import review
from fairplay_method3_reliability import (
    rating_support, opponent_support, independent_results_tier)
from test_fairplay import TARGET
from test_fairplay_method3 import API
from test_fairplay_method3_history import profile_game


def game(i, *, kind='bullet', score=1, rated=True, rating=800, opponent_elo=1600,
         ended=None, opponent=None):
    g=profile_game(i,score=score,kind=kind,rating=rating,opponent=opponent_elo)
    g.ended=i if ended is None else ended
    g.rated=rated
    g.opponent_username=opponent
    return g


def run_games(games):
    API.status='basic'
    with patch('fairplay_method3.collect_games',return_value=(games,{},False)):
        return review(TARGET,lambda _:None,api_factory=API)


class RatingReliabilityTests(unittest.TestCase):
    def test_old_lifetime_bullet_games_do_not_authenticate_new_unrated_elo(self):
        old=[game(i,rated=True,ended=1600000000+i) for i in range(30)]
        recent=[game(100+i,rated=False,ended=1770000000+i) for i in range(40)]
        other=[game(200+i,kind='rapid',rating=1700,opponent_elo=1700,
                    score=.5,ended=1770000000-100+i) for i in range(30)]
        support=rating_support(old+other+recent,recent)
        self.assertEqual(support['rated_anchor_games_365d'],0)
        self.assertEqual(support['unrated_games_365d'],40)
        self.assertEqual(support['other_control_rated_games_365d'],30)
        self.assertTrue(support['unreliable'])

    def test_reliable_recent_anchors_restore_result_priority(self):
        recent=[game(100+i,rated=False,ended=1770000100+i) for i in range(35)]
        other=[game(200+i,kind='rapid',score=.5,rating=1700,opponent_elo=1700,
                    ended=1770000000+i) for i in range(30)]
        weak=run_games(other+recent)
        self.assertIn(weak.priority,('LOW','MODERATE'))
        self.assertTrue(weak.diagnostics['method3']['selected']['rating_support']['unreliable'])
        self.assertNotEqual(weak.priority,'HIGH')

        anchors=[game(i,score=.5,rated=True,opponent_elo=800,
                      ended=1770000000+i) for i in range(30)]
        strong=run_games(anchors+other+recent)
        self.assertEqual(strong.priority,'HIGH')
        self.assertFalse(strong.diagnostics['method3']['selected']['rating_support']['unreliable'])

    def test_sparse_rating_short_unrated_period_gets_low_without_accuracy(self):
        recent=[game(100+i,rated=False,ended=1770000100+i) for i in range(12)]
        other=[game(200+i,kind='rapid',score=.5,rating=1700,opponent_elo=1700,
                    ended=1770000000+i) for i in range(30)]
        result=run_games(other+recent)
        self.assertEqual(result.priority,'LOW')
        periods=result.diagnostics['method3']['subject']['periods']
        self.assertTrue(any(p.get('rating_reliability_downgrade') for p in periods
                            if p['time_class']=='bullet' and p['games']<=20))

    def test_other_controls_are_not_substituted_as_expected_elo(self):
        g=[game(i,rated=False,ended=1770000000+i) for i in range(35)]
        support=rating_support(g,g)
        self.assertFalse(support['unreliable'])
        self.assertIsNone(support['other_control_median_elo_365d'])
        self.assertIsNone(support['rating_gap'])
        self.assertTrue(support['unverified'])
        self.assertEqual(support['anchor_status'],'unverified')


class RepeatOpponentTests(unittest.TestCase):
    def test_repeated_opponent_episode_caps_high_without_accuracy(self):
        matches=[game(i,kind='blitz',score=1,rating=1400,opponent_elo=1400,
                      opponent='same-player',ended=1770000000+i) for i in range(35)]
        repeated=run_games(matches)
        self.assertEqual(repeated.priority,'MODERATE')
        self.assertIn('repeated_opponent_concentration',
                      repeated.diagnostics['method3']['selected']['classification_guards'])
        diverse=[game(i,kind='blitz',score=1,rating=1400,opponent_elo=1400,
                      opponent='player-'+str(i),ended=1770000000+i) for i in range(35)]
        ordinary=run_games(diverse)
        self.assertEqual(ordinary.priority,'HIGH')

    def test_short_repeat_opponent_run_is_not_independent_high(self):
        # Previous safeguard required a 25-game surrounding cohort and could
        # treat a 20-game head-to-head match as 20 independent opponents.
        repeated=[game(i,kind='blitz',score=1,rating=1400,opponent_elo=1400,
                       opponent='  SAME-PLAYER  ',ended=1770000000+i)
                  for i in range(20)]
        summary=opponent_support(repeated,history=repeated)
        self.assertTrue(summary['local_opponent_concentrated'])
        self.assertFalse(summary['nearby_opponent_concentrated'])
        self.assertEqual(summary['period_distinct_opponents'],1)
        self.assertEqual(summary['period_effective_opponents'],1.0)
        result=run_games(repeated)
        self.assertNotEqual(result.priority,'HIGH')
        self.assertIn('repeated_opponent_concentration',
                      result.diagnostics['method3']['selected']['classification_guards'])

    def test_effective_opponents_identifies_three_uneven_repeats(self):
        # Three opponents with strongly repeated pairings should not count
        # as 22 independent opposition draws even though none dominates 75%.
        names=['alpha']*9+['beta']*7+['gamma']*6
        games=[game(i,kind='blitz',score=1,rating=1400,opponent_elo=1400,
                    opponent=name,ended=1770000000+i)
               for i,name in enumerate(names)]
        summary=opponent_support(games,history=games)
        self.assertEqual(summary['period_distinct_opponents'],3)
        self.assertLess(summary['period_effective_opponents'],4)
        self.assertTrue(summary['local_opponent_concentrated'])

    def test_sparse_opponent_identity_does_not_invent_concentration(self):
        rows=[game(i,kind='blitz',opponent='same' if i<3 else None)
              for i in range(20)]
        summary=opponent_support(rows,history=rows)
        self.assertFalse(summary['opponent_concentrated'])
        self.assertLess(summary['period_opponent_coverage'],.8)

    def test_missing_opponent_names_do_not_invent_independence(self):
        matches=[game(i,kind='blitz',score=1,rating=1400,opponent_elo=1400,
                      ended=1770000000+i) for i in range(35)]
        support=opponent_support(matches,history=matches)
        self.assertFalse(support['opponent_concentrated'])
        self.assertEqual(support['opponent_names_covered'],0)

    def test_accuracy_cannot_bypass_rating_or_opponent_guards(self):
        for tier in ('HIGH','VERY HIGH'):
            capped,reasons=independent_results_tier(tier,games=30,
                rating={'unreliable':True,'unverified':False},
                opponents={'opponent_concentrated':True},accuracy_confirmed=True)
            self.assertEqual(capped,'MODERATE')
            self.assertEqual(reasons,
                ('unreliable_time_control_rating','repeated_opponent_concentration'))
        tier,reasons=independent_results_tier('VERY HIGH',games=30,
            rating={'unreliable':False,'unverified':False},
            opponents={'opponent_concentrated':True},accuracy_confirmed=True)
        self.assertEqual(tier,'MODERATE')
        self.assertEqual(reasons,('repeated_opponent_concentration',))
        tier,reasons=independent_results_tier('VERY HIGH',games=30,
            rating={'unreliable':False,'unverified':False},
            opponents={'opponent_concentrated':False},accuracy_confirmed=True)
        self.assertEqual((tier,reasons),('VERY HIGH',()))

    def test_missing_rated_anchors_cannot_authorize_high_even_without_other_controls(self):
        rows=[game(i,kind='blitz',score=1,rated=False,
                   rating=1400,opponent_elo=1400,
                   opponent='other-'+str(i),ended=1770000000+i)
              for i in range(35)]
        support=rating_support(rows,rows)
        self.assertTrue(support['unverified'])
        self.assertFalse(support['unreliable'])
        self.assertIsNone(support['rating_gap'])
        report=run_games(rows)
        self.assertEqual(report.priority,'MODERATE')
        chosen=report.diagnostics['method3']['selected']
        self.assertIn('unverified_time_control_rating',chosen['classification_guards'])
        self.assertEqual(chosen['effective_priority'],'MODERATE')
        self.assertEqual(report.confidence,'LOW')

    def test_zero_rated_anchors_with_confirmed_accuracy_keeps_only_moderate(self):
        for tier in ('HIGH','VERY HIGH'):
            moderate,reasons=independent_results_tier(tier,games=15,
                rating={'unverified':True,'unreliable':False},
                opponents={'opponent_concentrated':False},
                accuracy_confirmed=True)
            self.assertEqual(moderate,'MODERATE')
            self.assertIn('unverified_time_control_rating',reasons)
            moderate_without_accuracy,_=independent_results_tier(tier,games=15,
                rating={'unverified':True,'unreliable':False},
                opponents={'opponent_concentrated':False},
                accuracy_confirmed=False)
            self.assertEqual(moderate_without_accuracy,'MODERATE')

if __name__=='__main__':
    unittest.main()
