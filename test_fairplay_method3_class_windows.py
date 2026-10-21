"""Anonymous regressions for windows within each time class.

Intervening games in another control must not fragment a results sequence.
The ordinary control games still contribute to the global search correction.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fairplay_method3 import review


class AnonymousAPI:
    def __init__(self, deadline):
        self.deadline = deadline

    def get(self, target, suffix='', *, profile=False):
        return {'username': target} if profile else {}

    def close(self):
        pass


def interleaved_games(*, rapid_score=1, mixed_rated=False):
    games = []
    for index in range(820):
        rapid = index % 41 == 0
        games.append(SimpleNamespace(
            identity=f'anonymous-game-{index:04}',
            ended=1700000000 + index * 300,
            rated=not (mixed_rated and rapid and (index // 41) % 2),
            time_class='rapid' if rapid else 'blitz',
            time_control='600+0' if rapid else '300+0',
            rating=1500,
            opponent_rating=1500,
            score=rapid_score if rapid else .5,
            accuracy=None,
            decisions=[],
            opponent_username=f'anonymous-opponent-{index:04}',
        ))
    return games


def run_review(games):
    with patch('fairplay_method3.collect_games', return_value=(games, {}, False)):
        return review('anonymous-subject', lambda _: None, api_factory=AnonymousAPI)


class TimeClassWindows(unittest.TestCase):
    def test_rare_time_class_sequence_survives_other_control_games(self):
        # No global 200-game segment contains more than five rapid games.
        # All twenty rapid wins belong to one same-control sequence.
        games = interleaved_games()
        result = run_review(games)
        self.assertEqual(result.priority, 'HIGH')
        diagnostics = result.diagnostics['method3']
        selected = diagnostics['selected']
        self.assertEqual(selected['time_class'], 'rapid')
        self.assertEqual((selected['first_game'], selected['last_game']), (1, 20))
        self.assertEqual(selected['games'], 20)
        self.assertEqual(selected['first_ended'], games[0].ended)
        self.assertEqual(selected['last_ended'], games[19 * 41].ended)
        self.assertEqual(diagnostics['subject']['overall_results']['games'], 820)
        guard = selected['search_guard']
        self.assertAlmostEqual(guard['adjusted_model_bound'],
            min(1, guard['raw_model_bound'] * diagnostics['searched_windows']))
        self.assertGreater(diagnostics['searched_windows'], 28)
        self.assertTrue(all(period['time_class'] == 'rapid'
                            for period in diagnostics['marked_periods']))

    def test_ordinary_interleaved_controls_remain_low(self):
        result = run_review(interleaved_games(rapid_score=.5))
        self.assertEqual(result.priority, 'LOW')
        self.assertEqual(result.diagnostics['method3']['marked_periods'], [])

    def test_mixed_rated_unrated_games_keep_class_sequence_and_totals(self):
        result = run_review(interleaved_games(mixed_rated=True))
        self.assertEqual(result.priority, 'HIGH')
        diagnostics = result.diagnostics['method3']
        self.assertEqual(diagnostics['selected']['games'], 20)
        self.assertEqual(diagnostics['selected']['time_class'], 'rapid')
        self.assertEqual(diagnostics['subject']['overall_results']['games'], 820)
        self.assertEqual(diagnostics['unrated_games'], 10)
        self.assertEqual(sum(g.rated is True for g in result.games), 810)


if __name__ == '__main__':
    unittest.main()
