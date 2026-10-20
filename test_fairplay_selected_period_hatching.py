"""Regression checks for selected-period highlighting."""
import unittest
from types import SimpleNamespace
from fairplay_method3_ui import mark_selected, selected_period_embed, history_svg


class SelectedPeriodHighlight(unittest.TestCase):
    def test_only_matching_class_gets_red_hatched_window(self):
        result = SimpleNamespace(priority='HIGH', diagnostics={'method3': {'selected': {
            'time_class': 'bullet', 'first_game': 2, 'last_game': 4,
            'first_ended': 1778760000, 'last_ended': 1778761000}}})
        svg = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        rows = [{} for _ in range(10)]
        self.assertEqual(mark_selected(svg, result, 'blitz', rows, (150,)), svg)
        marked = mark_selected(svg, result, 'bullet', rows, (150,))
        self.assertIn('patternTransform="rotate(45)"', marked)
        self.assertIn('fill="url(#fairplay-selected-red-hatch)"', marked)
        self.assertIn('strongest selected results period (HIGH)', marked)
        self.assertEqual(marked.count('<rect x='), 1)


    def test_selected_period_shows_points_opponents_dates_and_game_links(self):
        games=[
            SimpleNamespace(identity='a',ended=1778760000,time_class='bullet',
                opponent_username='opponent_one',result='Loss',
                url='https://www.chess.com/game/live/100'),
            SimpleNamespace(identity='b',ended=1778761000,time_class='bullet',
                opponent_username='opponent_two',result='Win',
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
        self.assertIn('opponent_one',card.fields[0].value)
        self.assertIn('opponent_two',card.fields[1].value)
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
