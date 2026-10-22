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
        # These are independent synthetic games, not twenty consecutive
        # challenges to the same opponent. The separate reliability suite
        # explicitly tests repeated head-to-head opposition.
        for index,row in enumerate(API.rows,1):
            name=f'synthetic-opponent-{index}'
            row['black']['username']=name
            row['pgn']=row['pgn'].replace('[Black "synthetic-opponent"]',
                                          f'[Black "{name}"]')
            row['accuracies']={'white':85,'black':60}
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
    def test_observed_data_coverage_is_reported_without_imputing_missing_values(self):
        from fairplay_ui import result_embed, detail_embed
        result=self.run_review()
        quality=result.diagnostics['method3']['data_quality']
        self.assertEqual(quality['history_games'],20)
        self.assertEqual(quality['published_accuracy_games'],20)
        self.assertEqual(quality['identified_opponents'],20)
        self.assertEqual(quality['valid_rating_pairs'],20)
        summary=str(result_embed(result).to_dict())
        self.assertIn('Data reliability',summary)
        self.assertIn('20/20',summary)
        for row in API.rows[:10]:
            row.pop('accuracies')
        reduced=self.run_review()
        quality=reduced.diagnostics['method3']['data_quality']
        self.assertEqual(quality['published_accuracy_games'],10)
        self.assertIn('10/20',str(result_embed(reduced).to_dict()))
        self.assertLess(len(detail_embed(reduced,'Review Gates')),6000)

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
    def test_accuracy_frequency_priority_does_not_mislabel_results_window(self):
        from fairplay_method3_ui import result_embed, selected_period_embed
        result=self.run_review()
        result.priority='MODERATE'
        info=result.diagnostics['method3']
        info['priority_basis']='accuracy_frequency'
        info['selected_period_priority']='LOW'
        info['accuracy_frequency_signals']=[{
            'time_class':'blitz','recent_at_least_90':27,
            'recent_games':30,'baseline_at_least_90':3,
            'baseline_games':70,'distinct_opponents':21}]
        main=result_embed(result).to_dict()
        summary=str(main)
        self.assertIn('Earlier → recent Accuracy frequency',summary)
        self.assertIn('27/30',summary)
        self.assertIn('separate from Accuracy signal',summary)
        selected=selected_period_embed(result)
        self.assertIn('LOW results window',selected.description)
        self.assertIn('Overall MODERATE',selected.description)
        self.assertNotIn('Red shading',selected.description)

    def test_period_context_fields_use_real_linebreaks(self):
        from fairplay_method3_ui import selected_period_embed
        result=self.run_review()
        field_values=[field.value for field in selected_period_embed(result).fields]
        for value in field_values:
            self.assertNotIn(r'\n',value)
        self.assertTrue(any('\n' in value for value in field_values))

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
        self.assertTrue(any('accuracy_frequency' in name for name,data in files))
        self.assertTrue(any('selected_period' in name for name,data in files))
        self.assertLessEqual(len(files),6)
        self.assertTrue(all(data.startswith(b'\x89PNG') for name,data in files))

if __name__=='__main__':unittest.main()
