"""Minimum sample boundaries and confidence gates, using synthetic games only."""
import unittest
from dataclasses import replace

import fairplay_analysis as analysis
from fairplay_config import CONFIG
from test_fairplay_v4 import game, report


class MinimumSample(unittest.TestCase):
    def test_nine_useful_games_remain_insufficient(self):
        self.assertEqual(report([game(i, True) for i in range(9)]).priority,
                         'INSUFFICIENT DATA')

    def test_ten_useful_games_receive_priority(self):
        result = report([game(i) for i in range(10)])
        self.assertEqual(result.priority, 'LOW')
        self.assertEqual(result.confidence, 'MEDIUM')

    def test_ten_minimum_length_games_receive_low_confidence_priority(self):
        games = [game(i) for i in range(10)]
        for g in games:
            useful = [d for d in g.decisions if d.metrics.get('useful')]
            for d in useful[8:]:
                d.metrics['useful'] = False
            analysis.summarize(g)
            g.fast_metrics = {k: v for k, v in g.metrics.items() if k != 'timing'}
        result = report(games)
        self.assertEqual(result.totals['decisions'], 80)
        self.assertEqual(result.priority, 'LOW')
        self.assertEqual(result.confidence, 'LOW')
        self.assertNotEqual(result.families['Engine Precision'], 'Insufficient meaningful decisions')

    def test_collected_game_without_eight_decisions_does_not_count(self):
        games = [game(i) for i in range(10)]
        games[-1].metrics['decisions'] = 7
        result = report(games)
        self.assertEqual(result.coverage['used'], 9)
        self.assertEqual(result.priority, 'INSUFFICIENT DATA')

    def test_ten_games_allow_high_only_with_existing_evidence_gates(self):
        args = dict(games=10, decisions=200, critical=40, confidence='MEDIUM',
                    deep_confirmed=True, partial=False)
        scores = (.9, .9, .8, 0, 0)
        self.assertEqual(analysis.priority_model(scores, **args), 'HIGH')
        for changed in ({'deep_confirmed': False}, {'confidence': 'LOW'}, {'persistent': False}):
            self.assertNotIn(analysis.priority_model(scores, **{**args, **changed}),
                             ('HIGH', 'VERY HIGH'))
        self.assertNotIn(analysis.priority_model((0, 0, 1, 0, 0), **args),
                         ('HIGH', 'VERY HIGH'))

    def test_ten_games_never_very_high_even_with_large_decision_count(self):
        result = analysis.priority_model((1, 1, 1, 1, 1), games=10, decisions=1000,
                                        critical=100, confidence='HIGH',
                                        deep_confirmed=True, partial=False)
        self.assertEqual(result, 'HIGH')

    def test_configured_minimum_remains_respected(self):
        result = analysis.priority_model((0, 0, 0, 0, 0), games=10, decisions=1000,
                                        critical=100, confidence='HIGH',
                                        deep_confirmed=True, partial=False,
                                        config=replace(CONFIG, min_games=12))
        self.assertEqual(result, 'INSUFFICIENT DATA')
