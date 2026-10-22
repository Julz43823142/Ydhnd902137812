"""Regression checks for selected-period highlighting."""
import unittest
from types import SimpleNamespace
from fairplay_method3_ui import mark_selected, selected_period_embed, history_svg


class SelectedPeriodHighlight(unittest.TestCase):
    def test_multiple_disjoint_periods_have_priority_colors(self):
        games=[SimpleNamespace(time_class='bullet',ended=1000+i*100) for i in range(10)]
        periods=[
            {'time_class':'bullet','first_ended':1100,'last_ended':1200,'priority':'MODERATE'},
            {'time_class':'bullet','first_ended':1400,'last_ended':1500,'priority':'HIGH'},
            {'time_class':'bullet','first_ended':1700,'last_ended':1800,'priority':'VERY HIGH'},
        ]
        result=SimpleNamespace(priority='VERY HIGH',games=games,
            diagnostics={'method3':{'marked_periods':periods}})
        svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        rows=[{} for _ in games]
        self.assertEqual(mark_selected(svg,result,'blitz',rows,(150,)),svg)
        marked=mark_selected(svg,result,'bullet',rows,(150,))
        for color in ('#808080','#3478d4','#8754c9'):
            self.assertIn(f'fill="{color}"',marked)
        self.assertEqual(marked.count('<rect x='),3)
        self.assertIn('not proof',marked)

    def test_other_control_uses_purple_with_matching_period_number(self):
        games=[
            SimpleNamespace(time_class='blitz',ended=1000+i*100) for i in range(5)
        ]+[
            SimpleNamespace(time_class='bullet',ended=1000+i*100) for i in range(5)
        ]
        periods=[{'time_class':'blitz','first_ended':1100,'last_ended':1300,
                  'priority':'HIGH'}]
        result=SimpleNamespace(games=games,diagnostics={'method3':{'marked_periods':periods}})
        svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        blitz=mark_selected(svg,result,'blitz',[{}]*5,(150,))
        bullet=mark_selected(svg,result,'bullet',[{}]*5,(150,))
        self.assertIn('fill="#3478d4"',blitz)
        self.assertIn('fill="#a78bfa"',bullet)
        self.assertIn('>1</text>',bullet)
        self.assertNotIn('fill="#3478d4"',bullet)

    def test_low_and_insufficient_data_are_not_shaded(self):
        games=[SimpleNamespace(time_class='bullet',ended=1000+i*100) for i in range(5)]
        svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        rows=[{} for _ in games]
        for priority in ('LOW','INSUFFICIENT DATA'):
            result=SimpleNamespace(priority=priority,games=games,
                diagnostics={'method3':{'selected':{'time_class':'bullet','first_game':1,'last_game':5},
                    'marked_periods':[{'time_class':'bullet','first_ended':1000,
                        'last_ended':1400,'priority':priority}]}})
            self.assertEqual(mark_selected(svg,result,'bullet',rows,(150,)),svg)

    def test_no_marked_periods_needs_no_game_list(self):
        result=SimpleNamespace(priority='LOW',diagnostics={'method3':{'marked_periods':[]}})
        svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        self.assertEqual(mark_selected(svg,result,'bullet',[{}],(150,)),svg)


    def test_selected_period_shows_points_opponents_dates_and_game_links(self):
        games=[
            SimpleNamespace(identity='a',ended=1778760000,time_class='bullet',
                opponent_username='opponentone',result='Loss',
                url='https://www.chess.com/game/live/100'),
            SimpleNamespace(identity='b',ended=1778761000,time_class='bullet',
                opponent_username='opponenttwo',result='Win',
                url='https://www.chess.com/game/live/101'),
        ]
        selected={'time_class':'bullet','first_game':1,'last_game':2,
                  'first_ended':games[0].ended,'last_ended':games[-1].ended,
                  'games':2,'results':{'expected_points':.2,'actual_points':1}}
        result=SimpleNamespace(username='example',priority='LOW',games=games,
             diagnostics={'method3':{'selected':selected}})
        card=selected_period_embed(result)
        self.assertIn('Expected: **0.2 points**',card.description)
        self.assertIn('Scored: **1.0 points**',card.description)
        self.assertNotIn(r'\n',card.description)
        self.assertEqual(len(card.fields),2)
        self.assertIn('opponentone',card.fields[0].value)
        self.assertIn('opponenttwo',card.fields[1].value)
        self.assertIn('Open game',card.fields[0].value)
        self.assertIn('Loss',card.fields[0].value)
        self.assertIn('(UTC)',card.fields[0].value)

    def test_history_chart_explains_older_and_newer_date_ranges(self):
        summary=lambda games: {'games':games,'score_excess':.2,
            'accuracy_median':None,'accuracy_games':0,'think_median':None}
        result=SimpleNamespace(diagnostics={'method3':{'history_changes':[{
            'time_class':'blitz','time_control':'180+0',
            'baseline':summary(200),'recent':summary(20),
            'earlier_range':(1770000000,1771000000),
            'recent_range':(1772000000,1773000000),
            'results_comparable':True}]}})
        svg=history_svg(result)
        self.assertIn('Earlier 200',svg)
        self.assertIn('Recent 20',svg)
        self.assertIn('Blitz · 3+0',svg)
        self.assertIn('Coverage: 0/200 → 0/20',svg)
        self.assertIn('Unknown',svg)


if __name__ == '__main__':
    unittest.main()
