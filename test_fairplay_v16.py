"""Fair Play v16 regression tests for residual evidence and diagnostics."""
import unittest
from types import SimpleNamespace

from fairplay_config import CONFIG
from fairplay_human import evidence_funnel
from fairplay_validation import evaluate_cases
from test_fairplay_human import informative


class V16ResidualEvidence(unittest.TestCase):
    def test_sequential_funnel_is_monotonic(self):
        games=[informative(i,rating=1500) for i in range(3)]
        flow=evidence_funnel(games)['_flow']
        values=list(flow.values())
        self.assertEqual(values,sorted(values,reverse=True))
        self.assertLessEqual(flow['high_information'],flow['high_difficulty'])

    def test_accuracy_is_diagnostic_not_probability(self):
        game=informative(0,rating=1500)
        row=game.metrics['human']
        self.assertGreaterEqual(row['accuracy_index'],0)
        self.assertLessEqual(row['accuracy_index'],100)
        self.assertNotIn('cheating_probability',row)
        self.assertIn('not probability',row['model'])

    def test_validation_reports_false_positive_metrics(self):
        cases=[
            {'username':'positive-a','label':'positive'},
            {'username':'normal-a','label':'trusted_normal'}]
        priorities={'positive-a':'HIGH','normal-a':'LOW'}
        def analyze(target):
            return SimpleNamespace(priority=priorities[target],diagnostics={})
        result=evaluate_cases(cases,analyze)
        metrics=result['binary_high_cutoff']
        self.assertEqual(metrics['recall_tpr'],1)
        self.assertEqual(metrics['false_positive_rate'],0)
        self.assertEqual(metrics['specificity'],1)
        self.assertEqual(metrics['precision'],1)

    def test_public_priority_vocabulary_unchanged(self):
        self.assertEqual(CONFIG.primary_engine_games,500)
        self.assertEqual(CONFIG.history_games,500)


if __name__=='__main__':unittest.main()
