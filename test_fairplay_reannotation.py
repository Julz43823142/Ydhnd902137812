"""Reanalysis must not retain fast-pass evidence on excluded decisions."""
import unittest

from fairplay_human import annotate_game
from test_fairplay_human import informative


class Reannotation(unittest.TestCase):
    def test_excluded_decision_clears_previous_positive_evidence(self):
        game = informative(0)
        decision = game.decisions[0]
        self.assertTrue(decision.metrics['human_opportunity'])
        self.assertTrue(decision.metrics['informative_quiet_hit'])
        original_fast = dict(decision.fast_engine)
        decision.metrics.update(useful=False, search_inconsistent=True)
        annotate_game(game)
        for key in ('human_opportunity', 'high_information', 'informative_quiet_hit'):
            self.assertFalse(decision.metrics.get(key), key)
        for key in ('quality_excess', 'observed_move_quality', 'search_stability'):
            self.assertNotIn(key, decision.metrics)
        self.assertEqual(decision.fast_engine, original_fast)

    def test_reincluded_decision_is_recomputed_and_pass_is_idempotent(self):
        game = informative(0)
        decision = game.decisions[0]
        original = dict(game.metrics['human'])
        decision.metrics['useful'] = False
        annotate_game(game)
        decision.metrics['useful'] = True
        annotate_game(game)
        self.assertTrue(decision.metrics['human_opportunity'])
        self.assertEqual(game.metrics['human'], original)
        annotate_game(game)
        self.assertEqual(game.metrics['human'], original)


if __name__ == '__main__':
    unittest.main()
