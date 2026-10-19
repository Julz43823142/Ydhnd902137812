"""Regression checks for the Method 3 5000-game / 200-game block route."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fairplay_method3 import BLOCK_SIZE, LIMIT, review
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


if __name__ == '__main__':
    unittest.main()
