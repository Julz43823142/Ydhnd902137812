"""Regression tests for descriptive sparse-Accuracy review cues."""
import unittest
from types import SimpleNamespace
from fairplay_method3_accuracy_frequency import limited_coverage_review_flags


def game(index, accuracy=None, kind='rapid'):
    return SimpleNamespace(time_class=kind, accuracy=accuracy,
                           opponent_username=f'opponent-{index}')


class SparseAccuracyReviewTests(unittest.TestCase):
    def test_limited_coverage_observations_are_visible(self):
        games=[game(i, 95 if i<14 else 90 if i<30 else 80)
               for i in range(123)]
        games.extend(game(i) for i in range(123,385))
        flags=limited_coverage_review_flags(games)
        self.assertEqual(len(flags),1)
        self.assertEqual(flags[0]['time_class'],'rapid')
        self.assertEqual(flags[0]['accuracy_covered'],123)
        self.assertEqual(flags[0]['at_least_90'],30)
        self.assertEqual(flags[0]['at_least_95'],14)
        self.assertFalse(flags[0]['priority_effect'])

    def test_adequate_coverage_does_not_trigger_sparse_note(self):
        games=[game(i,95 if i<14 else 90 if i<30 else 80) for i in range(70)]
        games.extend(game(i) for i in range(70,100))
        self.assertEqual(limited_coverage_review_flags(games),[])

    def test_small_observed_sample_does_not_trigger(self):
        games=[game(i,95) for i in range(12)]
        games.extend(game(i) for i in range(12,200))
        self.assertEqual(limited_coverage_review_flags(games),[])


if __name__=='__main__':
    unittest.main()
