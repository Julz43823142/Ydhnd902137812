"""Validation-only miss taxonomy regressions. No production labels enter scoring."""
import json
import unittest
from types import SimpleNamespace

from fairplay_validation import evaluate_cases, positive_low_bucket


def result(priority, *, blockers=(), candidate_games=0, passed=False, index=None):
    diagnostics={'gameplay':{
        'distributed_moderate':{
            'passed':passed,
            'blockers':list(blockers),
            'candidate_games':candidate_games},
        'research_evidence_index':index}}
    return SimpleNamespace(priority=priority, diagnostics=diagnostics)


class ValidationMissBuckets(unittest.TestCase):
    def test_positive_low_bucket_distinguishes_near_miss_from_multi_blocked(self):
        near=result('LOW',blockers=('deep semantic-quality stability',),candidate_games=40)
        weak=result('LOW',blockers=('anomaly hit lower bound','raw quality separation'),candidate_games=40)
        none=result('LOW',blockers=('broad chronological sample',),candidate_games=0)
        self.assertEqual(positive_low_bucket(near)[0],'near_miss')
        self.assertEqual(positive_low_bucket(weak)[0],'weak_window')
        self.assertEqual(positive_low_bucket(none)[0],'weak_window')
        self.assertIsNone(positive_low_bucket(result('MODERATE'))[0])

    def test_validation_labels_are_consulted_only_after_frozen_analysis(self):
        cases=[
            {'username':'case-one','label':'known_fair_play_closed'},
            {'username':'case-two','label':'positive'},
            {'username':'case-three','label':'holdout_positive'},
            {'username':'control-one','label':'trusted_normal'},
        ]
        outputs={
            'case-one':result('LOW',blockers=('deep semantic-quality stability',),candidate_games=40,index=22),
            'case-two':result('LOW',blockers=('single-hit games are not dominant','raw quality separation'),candidate_games=50,index=9),
            'case-three':result('MODERATE',index=30),
            'control-one':result('LOW',blockers=('deep semantic-quality stability',),candidate_games=40,index=12),
        }
        calls=[]
        def analyze(target):
            calls.append(target)
            self.assertIsInstance(target,str)
            return outputs[target]

        summary=evaluate_cases(cases,analyze)
        self.assertEqual(calls,['case-one','case-two','case-three','control-one'])
        miss=summary['positive_low_miss_analysis']
        self.assertEqual(miss['completed'],2)
        self.assertEqual(miss['buckets']['near_miss'],1)
        self.assertEqual(miss['buckets']['weak_window'],1)
        self.assertEqual(miss['distributed_moderate_blockers']['deep semantic-quality stability'],1)
        self.assertEqual(miss['distributed_moderate_blockers']['raw quality separation'],1)
        self.assertNotIn('control-one',json.dumps(summary))
        self.assertNotIn('case-one',json.dumps(summary))

    def test_low_with_passed_moderate_is_flagged_as_diagnostic_inconsistency(self):
        bucket,blockers=positive_low_bucket(result('LOW',passed=True,candidate_games=40))
        self.assertEqual(bucket,'diagnostic_inconsistency')
        self.assertEqual(blockers,())


if __name__=='__main__':
    unittest.main()
