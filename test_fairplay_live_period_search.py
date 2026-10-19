"""Chronological search guards and equal treatment, using anonymous fixtures."""
import itertools
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from fairplay_periods import points_bound
from fairplay_outcome_study import evaluate
from fairplay_method3 import review,STUDY
from test_fairplay import TARGET
from test_fairplay_method3 import API
from test_fairplay_method3_history import profile_game
from test_fairplay_outcome_study import game


class PeriodSearch(unittest.TestCase):
    def run_games(self,games):
        with patch('fairplay_method3.collect_games',return_value=(games,{},False)):
            return review(TARGET,lambda _:None,api_factory=API)

    def setUp(self):API.status='basic';API.rows=[]

    def history(self,start,length=20):
        games=[profile_game(i,score=.5) for i in range(1000)]
        for g in games[start:start+length]:g.score=1
        return games

    def test_early_middle_and_recent_regimes_can_all_qualify(self):
        for start in (3,413,977):
            with self.subTest(start=start):
                r=self.run_games(self.history(start))
                self.assertEqual(r.priority,'HIGH')
                selected=r.diagnostics['method3']['selected']
                self.assertLessEqual(selected['first_game'],start+1)
                self.assertGreaterEqual(selected['last_game'],start+20)
                self.assertEqual(r.diagnostics['method3']['priority_games'],1000)
                self.assertGreater(r.diagnostics['method3']['searched_windows'],1000)

    def test_five_extreme_upsets_in_middle_can_qualify_with_limited_confidence(self):
        games=self.history(413,0)
        for g in games[413:418]:g.score=1;g.opponent_rating=2000
        result=self.run_games(games)
        self.assertEqual(result.priority,'HIGH')
        self.assertEqual(result.confidence,'LOW')
        self.assertEqual(result.diagnostics['method3']['selected']['games'],5)

    def test_five_equal_rating_wins_and_one_exceptional_game_are_not_high(self):
        self.assertNotEqual(self.run_games(self.history(413,5)).priority,'HIGH')
        games=self.history(413,0);games[413].score=1;games[413].opponent_rating=2500
        self.assertNotEqual(self.run_games(games).priority,'HIGH')

    def test_unrated_status_is_not_a_priority_or_confidence_feature(self):
        rated=self.history(413)
        a=self.run_games(rated)
        for g in rated:g.rated=False
        b=self.run_games(rated)
        self.assertEqual(a.priority,b.priority)
        self.assertEqual(a.confidence,b.confidence)
        self.assertEqual(a.diagnostics['method3']['selected'],b.diagnostics['method3']['selected'])

    def test_mixed_statuses_share_one_period_and_earlier_reference(self):
        games=[profile_game(i,score=.5) for i in range(60)]
        for i,g in enumerate(games):
            g.rated=i%2==0;g.accuracy=60 if i<40 else 90
        r=self.run_games(games)
        self.assertEqual(r.priority,'MODERATE')
        self.assertEqual(r.diagnostics['method3']['subject']['rated_games'],30)
        self.assertEqual(r.diagnostics['method3']['subject']['unrated_games'],30)
        selected=r.diagnostics['method3']['selected']
        self.assertEqual(selected['reference_mode'],'personal_earlier')
        self.assertEqual(selected['accuracy']['matched_reference_median'],60)

    def test_cached_earlier_references_equal_original_matching(self):
        rows=[game(i,score=.5,accuracy=50+i%45) for i in range(80)]
        for i,g in enumerate(rows):
            g['player_elo']=500+(i%3)*100;g['opponent_elo']=600+(i%4)*100
            g['rated']=i%2==0
        config=replace(STUDY,widths=(5,10,20))
        baseline=evaluate([{'player_key':'subject','games':rows}],config=config,rated=None)['players'][0]
        dense=evaluate([{'player_key':'subject','games':rows}],config=config,rated=None,protect_search=True)['players'][0]
        indexed={(p['time_class'],p['first_game'],p['last_game'],p['reference_mode']):p for p in dense['periods']}
        for p in baseline['periods']:
            self.assertEqual(p['accuracy'],indexed[(p['time_class'],p['first_game'],p['last_game'],p['reference_mode'])]['accuracy'])

    def test_points_bound_conservatively_bounds_exact_bernoulli_tail(self):
        ratings=[(500,800),(700,900),(1000,900),(1400,1200)]
        from fairplay_outcome_study import expected_points
        ps=[expected_points(a,b) for a,b in ratings]
        rows=[{'score':1 if i<3 else 0,'player_elo':a,'opponent_elo':b} for i,(a,b) in enumerate(ratings)]
        tail=sum(math.prod(p if win else 1-p for p,win in zip(ps,outcome))
                 for outcome in itertools.product((0,1),repeat=4) if sum(outcome)>=3)
        self.assertGreaterEqual(points_bound(rows)+1e-12,tail)
        for row in rows:row['score']=1
        self.assertAlmostEqual(points_bound(rows),math.prod(ps))
        for row in rows:row['score']=0
        self.assertEqual(points_bound(rows),1)

    def test_normal_history_keeps_coverage_confidence_not_arbitrary_short_window(self):
        result=self.run_games([profile_game(i,score=.5) for i in range(60)])
        self.assertEqual(result.priority,'LOW')
        self.assertEqual(result.confidence,'MEDIUM')
        self.assertEqual(result.diagnostics['method3']['selected']['games'],60)

    def test_three_accidental_high_accuracy_games_do_not_flag_thousand_game_history(self):
        games=[profile_game(i,score=.5) for i in range(1000)]
        for i,g in enumerate(games):g.accuracy=60+i%21
        for g in games[413:416]:g.accuracy=99
        self.assertEqual(self.run_games(games).priority,'LOW')

    def test_five_replicated_accuracy_deviations_can_support_moderate(self):
        games=[profile_game(i,score=.5) for i in range(1000)]
        for i,g in enumerate(games):g.accuracy=60+i%21
        for g in games[413:418]:g.accuracy=99
        result=self.run_games(games)
        self.assertEqual(result.priority,'MODERATE')
        selected=result.diagnostics['method3']['selected']
        self.assertTrue(selected['accuracy_search_guard']['moderate_pass'])
        self.assertFalse(selected['search_guard']['high_pass'])

    def test_reference_modes_do_not_multiply_window_count(self):
        r=self.run_games(self.history(413))
        periods=r.diagnostics['method3']['subject']['periods']
        physical={(p['time_class'],p['first_game'],p['last_game']) for p in periods}
        self.assertEqual(r.diagnostics['method3']['searched_windows'],len(physical))
        self.assertEqual(len(periods),2*len(physical))
