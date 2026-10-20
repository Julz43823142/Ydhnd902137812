"""Regression checks for the Method 3 5000-game / 200-game block route."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fairplay_method3 import BLOCK_SIZE, LIMIT, review
from test_fairplay import TARGET
from fairplay_progress import estimate


class BlockReviewTests(unittest.TestCase):
    def test_limits_and_progress(self):
        self.assertEqual((LIMIT, BLOCK_SIZE), (5000, 200))
        self.assertLess(estimate('Collecting rated and unrated games… 100/5000 games · 1/20 archives'),
                        estimate('Collecting rated and unrated games… 3000/5000 games · 12/20 archives'))
        self.assertLess(estimate('Method 3: block 1/25'), estimate('Method 3: block 24/25'))
        self.assertLess(estimate('Method 3: block 24/25'), 95)

    def test_early_high_is_not_diluted_by_later_normal_blocks(self):
        games = [
            SimpleNamespace(identity=f'g{i:04}', ended=i + 1000, rated=True, time_class='blitz',
                time_control='300', rating=1300, opponent_rating=1300, score=1 if i < 200 else .5,
                accuracy=85, decisions=[])
            for i in range(450)
        ]
        def study(players, **kwargs):
            block = players[0]['games']
            high = block[-kwargs['candidate_tail']]['identity'] == 'g0000'
            length = kwargs['candidate_tail']
            period = {
                'time_class': 'blitz', 'first_game': 1, 'last_game': length, 'games': length,
                'reference_mode': 'personal_earlier',
                'results': {'expected_points': length/2, 'actual_points': length if high else length/2,
                    'score_excess': .5 if high else 0, 'standardized_residual': 5 if high else 0,
                    'win_fraction': 1 if high else 0},
                'accuracy': {'matched_games': 0},
                'search_guard': {'adjusted_model_bound': .0001 if high else 1,
                    'searched_windows': 1, 'short_sample_complete': True},
                'accuracy_search_guard': {'adjusted_reference_model_bound': 1, 'searched_windows': 1},
                'scenarios': {scenario: {'results_pass': high, 'results_review_pass': high,
                    'accuracy_pass': False, 'results_covered': True, 'accuracy_covered': False}
                    for scenario in ('balanced', 'development_middle')}
            }
            return {'players': [{'periods': [period], 'opponent_groups': []}]}
        class API:
            def __init__(self, deadline): self.deadline = deadline
            def get(self, target, suffix='', *, profile=False):
                return {'username': target} if profile else {}
            def close(self): pass
        stages=[]
        with (patch('fairplay_method3.collect_games', return_value=(games, {}, False)),
              patch('fairplay_method3.evaluate', side_effect=study)):
            result = review('sharkmeister', stages.append, api_factory=API)
        self.assertEqual(result.priority, 'HIGH')
        self.assertEqual(result.diagnostics['method3']['blocks_checked'], 3)
        self.assertEqual(result.diagnostics['method3']['selected']['history_block'], 1)
        self.assertIn('Method 3: block 3/3', stages)
        self.assertEqual(result.coverage['requested_primary_limit'], 5000)


    def test_shared_results_guard_is_adjusted_once_per_window(self):
        # protect_periods reuses one results guard for two reference modes.
        # One physical period should be charged for all unique global search
        # windows, including any eligible cross-boundary bridges, exactly once.
        games = [
            SimpleNamespace(identity=f'g{i:04}', ended=i + 1000, rated=True,
                time_class='blitz', time_control='300+0', rating=1300,
                opponent_rating=1300, score=1 if i < 200 else .5,
                accuracy=None, decisions=[])
            for i in range(450)
        ]
        def study(players, **kwargs):
            block=players[0]['games']
            high=block[-kwargs['candidate_tail']]['identity']=='g0000'
            length=kwargs['candidate_tail']
            guard={'adjusted_model_bound': .002 if high else 1,
                   'searched_windows': 1,'short_sample_complete': True}
            periods=[]
            for mode in ('other_players','personal_earlier'):
                periods.append({
                    'time_class':'blitz','first_game':1,'last_game':length,
                    'games':length,'reference_mode':mode,
                    'results':{'expected_points':length/2,
                        'actual_points':length if high else length/2,
                        'score_excess':.5 if high else 0,
                        'standardized_residual':5 if high else 0,
                        'win_fraction':1 if high else 0},
                    'accuracy':{'matched_games':0},
                    'search_guard':guard,
                    'accuracy_search_guard':{'adjusted_reference_model_bound':1},
                    'scenarios':{scenario:{
                        'results_pass':high,'results_review_pass':high,
                        'accuracy_pass':False,'results_covered':True,
                        'accuracy_covered':False}
                        for scenario in ('balanced','development_middle')},
                })
            return {'players':[{'periods':periods,'opponent_groups':[]}]}
        class API:
            def __init__(self, deadline): self.deadline=deadline
            def get(self, target, suffix='', *, profile=False):
                return {'username':target} if profile else {}
            def close(self): pass
        with (patch('fairplay_method3.collect_games',return_value=(games,{},False)),
              patch('fairplay_method3.evaluate',side_effect=study)):
            result=review('sharkmeister',lambda _:None,api_factory=API)
        self.assertEqual(result.priority,'HIGH')
        self.assertAlmostEqual(
            result.diagnostics['method3']['selected']['search_guard']['adjusted_model_bound'],
            .002*result.diagnostics['method3']['searched_windows'])


    def test_period_split_by_200_game_boundary_is_still_detected(self):
        from test_fairplay_method3_history import profile_game
        from test_fairplay_method3 import API
        # Neither regular 200-game block contains more than ten wins in a
        # row; the 20-game winning period straddles their arbitrary boundary.
        games=[profile_game(i,score=1 if 190<=i<210 else .5) for i in range(400)]
        API.status='basic'
        with patch('fairplay_method3.collect_games',return_value=(games,{},False)):
            result=review(TARGET,lambda _:None,api_factory=API)
        self.assertEqual(result.priority,'HIGH')
        d=result.diagnostics['method3']
        self.assertEqual(d['blocks_checked'],2)
        self.assertEqual(d['boundary_bridges_checked'],1)
        self.assertEqual(d['segments_checked'],3)
        selected=d['selected']
        self.assertEqual(selected['history_segment'],'boundary')
        self.assertLessEqual(selected['first_game'],191)
        self.assertGreaterEqual(selected['last_game'],210)
        guard=selected['search_guard']
        self.assertLess(guard['raw_model_bound'],1)
        self.assertAlmostEqual(guard['adjusted_model_bound'],
            min(1,guard['raw_model_bound']*d['searched_windows']))
        accuracy=selected['accuracy_search_guard']
        self.assertAlmostEqual(accuracy['adjusted_reference_model_bound'],
            min(1,accuracy['raw_reference_model_bound']*d['searched_windows']))
        self.assertEqual(selected['first_ended'],games[selected['first_game']-1].ended)
        self.assertEqual(selected['last_ended'],games[selected['last_game']-1].ended)

    def test_no_artificial_jump_when_crossing_a_block_boundary(self):
        from test_fairplay_method3_history import profile_game
        from test_fairplay_method3 import API
        games=[profile_game(i,score=.5) for i in range(400)]
        API.status='basic'
        with patch('fairplay_method3.collect_games',return_value=(games,{},False)):
            result=review(TARGET,lambda _:None,api_factory=API)
        self.assertEqual(result.priority,'LOW')
        self.assertGreater(result.diagnostics['method3']['searched_windows'],0)


    def test_trailing_under_five_games_never_rescans_earlier_context(self):
        from test_fairplay_method3_history import profile_game
        from test_fairplay_method3 import API
        from fairplay_outcome_study import evaluate
        games=[profile_game(i,score=.5) for i in range(203)]
        API.status='basic'
        with (patch('fairplay_method3.collect_games',return_value=(games,{},False)),
              patch('fairplay_method3.evaluate',wraps=evaluate) as spy):
            result=review(TARGET,lambda _:None,api_factory=API)
        self.assertEqual(result.priority,'LOW')
        self.assertEqual(result.diagnostics['method3']['blocks_checked'],2)
        self.assertEqual(result.diagnostics['method3']['boundary_bridges_checked'],1)
        self.assertEqual(spy.call_count,2)  # complete block and real bridge only
        self.assertTrue(all(call.kwargs['candidate_tail']>=5 for call in spy.call_args_list))
        self.assertEqual(result.diagnostics['method3']['subject']['overall_results']['games'],203)



if __name__ == '__main__':
    unittest.main()
