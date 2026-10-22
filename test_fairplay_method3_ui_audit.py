"""Production priority labels and exact selected-window presentation."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fairplay_method3 import review
from fairplay_method3_ui import detail_embed, result_embed, selected_period_embed, selected_window_svg
from test_fairplay_method3 import API, TARGET
from test_fairplay_method3_context import fixture


class SelectedWindowPresentation(unittest.TestCase):
    def frequency_review(self):
        # A large frequency change across 90, but only a two-point median
        # increase: the separate >=8-point Accuracy route cannot qualify.
        games=[fixture(i,score=.5,accuracy=89 if i<70 else 91,clock_moves=30)
               for i in range(100)]
        for i,game in enumerate(games):
            game.opponent_username=f'synthetic-opponent-{i}'
            game.result='Draw'
        with patch('fairplay_method3.collect_games',return_value=(games,{},False)):
            return review(TARGET,lambda _:None,api_factory=API)

    def test_production_frequency_promotion_preserves_low_results_tier(self):
        result=self.frequency_review()
        info=result.diagnostics['method3']
        self.assertEqual((result.priority,info['priority_basis']),
                         ('MODERATE','accuracy_frequency'))
        self.assertEqual(info['selected_period_priority'],'LOW')
        self.assertIn('LOW results window',selected_period_embed(result).description)
        self.assertNotIn('MODERATE results window',selected_period_embed(result).description)
        svg=selected_window_svg(result)
        self.assertIn('Latest 200 games · personal review · LOW',svg)
        self.assertIn('fill="#94a3b8"',svg)
        self.assertNotIn('fill="#eab308"',svg)

    def test_detail_reference_belongs_to_exact_selected_period(self):
        result=self.frequency_review()
        info=result.diagnostics['method3']
        info['subject']['periods']=[{
            'reference_mode':'personal_earlier','last_game':999,'time_class':'rapid','games':20,
            'accuracy':{'matched_games':20,'matched_observed_median':99,
                        'matched_reference_median':12}}]
        selected=info['selected']
        selected['reference_mode']='personal_earlier'
        selected['accuracy']={'matched_games':10,'matched_observed_median':92,
                              'matched_reference_median':74}
        for card in (detail_embed(result,'Performance'),selected_period_embed(result)):
            value=next(f.value for f in card.fields
                       if f.name=='Selected period · earlier personal Accuracy')
            self.assertIn('10/100 selected games matched',value)
            self.assertIn('92.0 / 74.0',value)
            self.assertNotIn('99.0 / 12.0',value)
        selected['accuracy']['matched_games']=0
        card=detail_embed(result,'Performance')
        self.assertIn('Not enough earlier',str(card.to_dict()))
        self.assertNotIn('99.0 / 12.0',str(card.to_dict()))

    def test_five_game_window_remains_readable_in_5000_without_aggregating(self):
        games=[fixture(i,accuracy=50+i%50,clock_moves=1) for i in range(5000)]
        games[2502].accuracy=None
        selected={'time_class':'blitz','first_game':2501,'last_game':2505,'games':5,
                  'first_ended':games[2500].ended,'last_ended':games[2504].ended,
                  'results':{'expected_points':2.5,'actual_points':5}}
        result=SimpleNamespace(games=games,priority='HIGH',diagnostics={'method3':{
            'selected':selected,'selected_period_priority':'HIGH'}})
        from fairplay_charts import plot
        with patch('fairplay_charts.plot',wraps=plot) as drawing:
            svg=selected_window_svg(result)
        self.assertEqual(drawing.call_count,4)
        accuracy=drawing.call_args_list[0]
        self.assertEqual(accuracy.args[4],[g.accuracy for g in games[2400:2605]])
        self.assertEqual(accuracy.kwargs['first_game'],2401)
        self.assertIsNone(accuracy.args[4][102])
        self.assertIn('History games #2501–#2505',svg)
        self.assertIn('>2401</text>',svg)
        self.assertIn('>2605</text>',svg)
        self.assertIn('fill="#f97316"',svg)

    def test_no_candidate_does_not_invent_selected_chart(self):
        result=SimpleNamespace(games=[],diagnostics={'method3':{'selected':None}})
        self.assertIsNone(selected_window_svg(result))

    def test_many_frequency_signals_fit_discord_without_changing_diagnostics(self):
        result=self.frequency_review()
        info=result.diagnostics['method3']
        original=info['accuracy_frequency_signals'][0]
        # The 5000-game cap permits dozens of exact controls with >=90 games.
        info['accuracy_frequency_signals']=[dict(original,time_control=f'{60+i}+0')
                                           for i in range(50)]
        card=result_embed(result)
        self.assertTrue(all(len(field.value)<=1024 for field in card.fields))
        self.assertLessEqual(len(card),6000)
        self.assertIn('additional time controls omitted',str(card.to_dict()))
        self.assertEqual(len(info['accuracy_frequency_signals']),50)


if __name__=='__main__':unittest.main()
