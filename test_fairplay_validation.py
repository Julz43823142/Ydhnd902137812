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


class PrivateCohortContracts(unittest.TestCase):
    def test_case_variants_cannot_cross_development_and_holdout(self):
        from fairplay_validation import validated_cases
        from unittest.mock import Mock
        rows=[{'username':'Synthetic-Player','label':'trusted_normal'},
              {'username':'synthetic-player','label':'holdout_normal'}]
        analyze=Mock()
        with self.assertRaises(ValueError):evaluate_cases(rows,analyze)
        analyze.assert_not_called()
        with self.assertRaises(ValueError):validated_cases(rows,'development')

    def test_entire_cohort_is_validated_before_any_player_is_analyzed(self):
        from unittest.mock import Mock
        analyze=Mock()
        rows=[{'username':'synthetic-valid','label':'trusted_normal'},
              {'username':'https://invalid.example','label':'positive'}]
        with self.assertRaises(ValueError):evaluate_cases(rows,analyze)
        analyze.assert_not_called()

    def test_development_selection_never_calls_holdout_accounts(self):
        from fairplay_validation import validated_cases
        rows=[{'username':'synthetic-dev','label':'positive'},
              {'username':'synthetic-held','label':'holdout_positive'}]
        seen=[]
        report=evaluate_cases(validated_cases(rows,'development'),
                              lambda target:(seen.append(target) or result('LOW')))
        self.assertEqual(seen,['synthetic-dev'])
        self.assertEqual(report['cases']['holdout_positive']['LOW'],0)
        self.assertNotIn('synthetic-held',json.dumps(report))

    def test_abstentions_and_failures_remain_visible_in_coverage(self):
        rows=[{'username':'synthetic-failure','label':'trusted_normal'},
              {'username':'synthetic-abstain','label':'positive'}]
        def analyze(target):
            if target.endswith('failure'):raise RuntimeError('private input')
            return result('INSUFFICIENT DATA')
        report=evaluate_cases(rows,analyze)
        self.assertEqual(report['coverage'],{'requested':2,'completed':1,
                         'failed':1,'insufficient_data':1,'completion_fraction':.5})
        self.assertNotIn('private input',json.dumps(report))

    def test_production_configuration_keeps_real_pipeline_entry_contract(self):
        from fairplay_validation import analysis_config
        from fairplay_config import CONFIG
        self.assertIs(analysis_config('production-local'),CONFIG)
        self.assertIs(analysis_config('production-local',CONFIG.history_games),CONFIG)
        self.assertNotEqual(analysis_config('screening'),CONFIG)
        with self.assertRaises(ValueError):analysis_config('production-local',100)

    def test_local_production_scope_cannot_dispatch_or_write_remote_work(self):
        import os
        from unittest.mock import patch
        from fairplay_validation import local_production_profile
        from fairplay_distributed import start
        old={'GITHUB_TOKEN':'synthetic-token','GH_TOKEN':'synthetic-other',
             'DISCORD_TOKEN':'synthetic-discord','FAIRPLAY_DISTRIBUTED_KEY':'synthetic-key',
             'FAIRPLAY_CHECKPOINT_KEY':'synthetic-checkpoint','GITHUB_REPOSITORY':'synthetic/repository',
             'GITHUB_ACTIONS':'false','FAIRPLAY_MAIA_CHECKPOINT':'synthetic-local-model'}
        with patch.dict(os.environ,old):
            before=dict(os.environ)
            with local_production_profile(),patch('fairplay_distributed._dispatch') as dispatch:
                self.assertEqual(os.environ['GITHUB_ACTIONS'],'true')
                self.assertEqual(os.environ['FAIRPLAY_DISTRIBUTED'],'1')
                self.assertEqual(os.environ['FAIRPLAY_MAIA_CHECKPOINT'],'synthetic-local-model')
                self.assertIsNone(start([object()],'synthetic-player',revision='a'*40,engine='Synthetic'))
                dispatch.assert_not_called()
                for key in ('GITHUB_TOKEN','GH_TOKEN','DISCORD_TOKEN','FAIRPLAY_DISTRIBUTED_KEY','FAIRPLAY_CHECKPOINT_KEY'):
                    self.assertNotIn(key,os.environ)
            self.assertEqual(dict(os.environ),before)

    def test_screening_cannot_accidentally_inherit_production_flags(self):
        import os
        from unittest.mock import patch
        from fairplay_validation import local_production_profile,analysis_config
        from fairplay_config import CONFIG
        hostile={'GITHUB_ACTIONS':'true','FAIRPLAY_DISTRIBUTED':'1',
                 'FAIRPLAY_V21':'1','FAIRPLAY_FULL_DEPTH18':'1','GITHUB_TOKEN':'synthetic'}
        with patch.dict(os.environ,hostile),local_production_profile('screening'):
            self.assertEqual(analysis_config('screening',CONFIG.history_games),CONFIG)
            self.assertEqual(os.environ['FAIRPLAY_FULL_DEPTH18'],'0')
            self.assertEqual(os.environ['FAIRPLAY_DISTRIBUTED'],'0')
            self.assertNotIn('GITHUB_TOKEN',os.environ)

    def test_environment_is_restored_after_analysis_failure(self):
        import os
        from unittest.mock import patch
        from fairplay_validation import local_production_profile
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false','FAIRPLAY_V21':'0'}):
            before=dict(os.environ)
            with self.assertRaises(RuntimeError):
                with local_production_profile():raise RuntimeError('synthetic failure')
            self.assertEqual(dict(os.environ),before)
