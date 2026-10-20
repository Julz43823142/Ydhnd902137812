"""Synthetic Method 3 routing, scoring and Discord coverage."""
import copy
import os
import threading
import unittest
from unittest.mock import Mock, patch
from dataclasses import replace

from fairplay_config import CONFIG, active_version
from fairplay_method3 import review
from test_fairplay import sample_row,TARGET


class API:
    status='basic';rows=None
    def __init__(self,deadline):self.deadline=deadline;self.closed=False
    def get(self,target,suffix='',*,profile=False):
        if profile:return {'username':TARGET,'status':self.status}
        if suffix=='/games/archives':return {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']}
        return {'games':copy.deepcopy(self.rows)}
    def close(self):self.closed=True


class Method3(unittest.TestCase):
    def setUp(self):
        API.rows=[sample_row(i) for i in range(1,21)]
        for row in API.rows:row['accuracies']={'white':85,'black':60}
        API.status='basic'
    def run_review(self):return review(TARGET,lambda _:None,api_factory=API)
    def test_actual_production_entry_does_not_call_engine_or_maia(self):
        from fairplay_analysis import review as entry
        with (patch.dict(os.environ,{'FAIRPLAY_METHOD3':'1','FAIRPLAY_V51':'1'}),
              patch('fairplay_analysis.get_shared_engine_pool',side_effect=AssertionError('no engine')),
              patch('fairplay_maia.annotate_history',side_effect=AssertionError('no model'))):
            result=entry(TARGET,lambda _:None,api_factory=API,engine_factory=Mock(side_effect=AssertionError()))
            self.assertEqual(active_version(),result.version)
        self.assertEqual(result.priority,'HIGH')
        self.assertFalse(result.deep_confirmed)
        self.assertEqual(result.coverage['deep_reviewed'],0)
        self.assertFalse(result.diagnostics['method3']['maia_used'])
    def test_status_and_accuracy_do_not_force_results_tier(self):
        first=self.run_review()
        API.status='closed:fair_play_violations'
        for row in API.rows:row['accuracies']={'white':10,'black':99}
        second=self.run_review()
        self.assertEqual(first.priority,second.priority)
        self.assertEqual(first.diagnostics['method3']['subject']['overall_results'],second.diagnostics['method3']['subject']['overall_results'])
    def test_small_sample_and_no_very_high(self):
        API.rows=API.rows[:1]
        self.assertEqual(self.run_review().priority,'INSUFFICIENT DATA')
    def test_elo_expected_clean_wins_are_not_high(self):
        for row in API.rows:
            row['white']['rating']=2500;row['black']['rating']=500
        self.assertEqual(self.run_review().priority,'LOW')
    def test_missing_accuracy_is_unknown_and_ui_is_truthful(self):
        for row in API.rows:row.pop('accuracies')
        result=self.run_review()
        from fairplay_ui import result_embed,detail_embed
        self.assertIn('review priority',result_embed(result).description)
        self.assertNotIn('Stockfish: not run',str(result_embed(result).to_dict()))
        self.assertIn('Published Accuracy: 0/20',str(result_embed(result).to_dict()))
        self.assertNotIn('Missing scores were not engine-reviewed',str(result_embed(result).to_dict()))
        for mode in ('Engine Analysis','Human Moves','Timing','Performance','Review Gates'):
            card=detail_embed(result,mode)
            self.assertLess(len(card),6000)
        self.assertIn('Not enough earlier',str(detail_embed(result,'Performance').to_dict()))
    def test_opponent_is_retained_for_selected_period(self):
        result=self.run_review()
        self.assertTrue(all(g.opponent_username for g in result.games))

    def test_cancel_is_not_a_report(self):
        event=threading.Event();event.set()
        from fairplay_data import DeadlineReached
        with self.assertRaises(DeadlineReached):review(TARGET,lambda _:None,api_factory=API,cancel=event)
    def test_charts_render_without_engine_or_estimated_accuracy(self):
        from fairplay_method3_ui import chart_pngs
        result=self.run_review();files=chart_pngs(result)
        self.assertTrue(any('expected_points' in name for name,data in files))
        self.assertTrue(all(data.startswith(b'\x89PNG') for name,data in files))

if __name__=='__main__':unittest.main()
