"""v86 review-data and non-disruptive deployment regressions."""
from pathlib import Path
import unittest

import yaml

from fairplay_method3 import review_confidence


class SampleSupport(unittest.TestCase):
    def test_large_history_does_not_inflate_short_period_support(self):
        self.assertEqual(review_confidence('HIGH',5000,{'games':10},False),'LOW')
        self.assertEqual(review_confidence('VERY HIGH',5000,{'games':19},False),'LOW')
        self.assertEqual(review_confidence('HIGH',5000,{'games':20},False),'MEDIUM')
        self.assertEqual(review_confidence('HIGH',5000,{'games':100},False),'HIGH')
        self.assertEqual(review_confidence('LOW',5000,{'games':200},False),'HIGH')

    def test_missing_information_never_gives_high_support(self):
        self.assertEqual(review_confidence('HIGH',5000,{'games':200},True),'LOW')
        self.assertEqual(review_confidence('HIGH',5000,{'games':200,
            'classification_guards':['unverified_opponent_identities']},False),'LOW')
        self.assertEqual(review_confidence('INSUFFICIENT DATA',5000,None,False),'LOW')
        self.assertEqual(review_confidence('HIGH',20,{'games':20},False),'LOW')


class DeploymentSafety(unittest.TestCase):
    def test_main_push_does_not_cancel_active_controller(self):
        workflow=yaml.safe_load(Path('.github/workflows/daily_puzzle_and_answer.yml').read_text())
        self.assertEqual(workflow['concurrency']['group'],'daily-puzzle')
        self.assertIs(workflow['concurrency']['cancel-in-progress'],False)
        self.assertEqual(workflow['jobs']['run']['steps'][-2]['name'],'Run Daily Puzzle')
        self.assertEqual(workflow['jobs']['run']['steps'][-1]['name'],
                         'Dispatch replacement after planned rotation')


if __name__=='__main__':
    unittest.main()
