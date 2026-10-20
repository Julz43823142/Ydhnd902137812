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
    def test_production_validation_calls_the_actual_depth_only_entry_point(self):
        from unittest.mock import patch
        from fairplay_validation import analyze_target, analysis_config
        config=analysis_config('production-local')
        expected=object()
        with patch('fairplay_v51_shadow.review',return_value=expected) as deployed, \
                patch('fairplay_analysis.review') as legacy:
            self.assertIs(analyze_target('synthetic-validation','production-local',config),expected)
            self.assertIs(deployed.call_args.args[2],config)
            self.assertEqual(deployed.call_args.args[0],'synthetic-validation')
            self.assertNotIn('candidate',deployed.call_args.kwargs)
            legacy.assert_not_called()

    def test_method3_validation_uses_actual_engine_free_production_route(self):
        from unittest.mock import patch
        from fairplay_validation import analyze_target, analysis_config, local_production_profile
        from fairplay_config import CONFIG
        import os
        self.assertIs(analysis_config('method3-local'),CONFIG)
        with self.assertRaises(ValueError):
            analysis_config('method3-local',100)
        expected=object()
        with (patch('fairplay_method3.review',return_value=expected) as method3,
              patch('fairplay_v51_shadow.review') as engine,
              patch('fairplay_analysis.review') as legacy,
              patch.dict(os.environ,{'FAIRPLAY_METHOD3':'0','GITHUB_TOKEN':'never-expose'})):
            before=dict(os.environ)
            with local_production_profile('method3-local'):
                self.assertEqual(os.environ['FAIRPLAY_METHOD3'],'1')
                self.assertEqual(os.environ['FAIRPLAY_V51'],'0')
                self.assertEqual(os.environ['FAIRPLAY_DISTRIBUTED'],'0')
                self.assertEqual(os.environ['FAIRPLAY_FULL_DEPTH18'],'0')
                self.assertEqual(os.environ['GITHUB_ACTIONS'],'false')
                self.assertNotIn('GITHUB_TOKEN',os.environ)
                self.assertIs(analyze_target('synthetic-validation','method3-local',CONFIG),expected)
            self.assertEqual(dict(os.environ),before)
        method3.assert_called_once()
        self.assertIs(method3.call_args.args[2],CONFIG)
        engine.assert_not_called()
        legacy.assert_not_called()

    def test_research_candidate_requires_an_explicit_local_profile(self):
        from unittest.mock import patch
        from fairplay_validation import analyze_target,analysis_config,local_production_profile
        from fairplay_v51_shadow import MODE,CANDIDATE_MODE
        self.assertNotEqual(MODE,CANDIDATE_MODE)
        with patch('fairplay_v51_shadow.review') as candidate:
            with local_production_profile('candidate-local'):
                analyze_target('synthetic-validation','candidate-local',analysis_config('candidate-local'))
            self.assertTrue(candidate.call_args.kwargs['candidate'])

    def test_legacy_and_screening_are_explicit_separate_comparisons(self):
        from unittest.mock import patch
        from fairplay_validation import analyze_target, analysis_config
        for profile in ('screening','legacy-production-local'):
            with patch('fairplay_v51_shadow.review') as deployed, \
                    patch('fairplay_analysis.review') as legacy:
                analyze_target('synthetic-validation',profile,analysis_config(profile))
                legacy.assert_called_once()
                deployed.assert_not_called()

    def test_positive_low_bucket_distinguishes_near_miss_from_multi_blocked(self):
        near=result('LOW',blockers=('deep semantic-quality stability',),candidate_games=40)
        weak=result('LOW',blockers=('anomaly hit lower bound','raw quality separation'),candidate_games=40)
        none=result('LOW',blockers=('broad chronological sample',),candidate_games=0)
        self.assertEqual(positive_low_bucket(near)[0],'near_miss')
        self.assertEqual(positive_low_bucket(weak)[0],'weak_window')
        self.assertEqual(positive_low_bucket(none)[0],'weak_window')
        self.assertIsNone(positive_low_bucket(result('MODERATE'))[0])

    def test_depth_only_miss_bucket_reads_the_deployed_diagnostics(self):
        report=SimpleNamespace(priority='LOW',diagnostics={'v51_shadow':{'review_priority':{
            'classes':{'blitz':{'closest_fixed_block':{
                'passed':False,'summary':{'information':.2},'blockers':['anomaly hit lower bound']}}}}}})
        self.assertEqual(positive_low_bucket(report),('near_miss',('anomaly hit lower bound',)))
        report.diagnostics['v51_shadow']['review_priority']['classes']['blitz']['closest_fixed_block']=None
        self.assertEqual(positive_low_bucket(report),('weak_window',('no complete fixed gameplay block',)))

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
                 'FAIRPLAY_METHOD3':'1','FAIRPLAY_V21':'1',
                 'FAIRPLAY_FULL_DEPTH18':'1','GITHUB_TOKEN':'synthetic'}
        with patch.dict(os.environ,hostile),local_production_profile('screening'):
            self.assertEqual(analysis_config('screening',CONFIG.history_games),CONFIG)
            self.assertEqual(os.environ['FAIRPLAY_FULL_DEPTH18'],'0')
            self.assertEqual(os.environ['FAIRPLAY_DISTRIBUTED'],'0')
            self.assertEqual(os.environ['FAIRPLAY_METHOD3'],'0')
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


class BlindRankingMetrics(unittest.TestCase):
    def test_pairwise_ordering_keeps_development_and_holdout_separate(self):
        rows=[{'username':'synthetic-dev-positive','label':'positive'},
              {'username':'synthetic-dev-normal','label':'trusted_normal'},
              {'username':'synthetic-held-positive','label':'holdout_positive'},
              {'username':'synthetic-held-normal','label':'holdout_normal'}]
        scores=dict(zip((r['username'] for r in rows),(10,20,40,30)))
        report=evaluate_cases(rows,lambda target:result('LOW',index=scores[target]))
        ranking=report['ranking']['partitions']
        self.assertEqual(ranking['development']['pairwise_auc'],0)
        self.assertEqual(ranking['holdout']['pairwise_auc'],1)
        self.assertEqual(ranking['development']['pairs'],1)
        self.assertNotIn('synthetic-dev-positive',json.dumps(report))

    def test_ties_receive_half_credit_without_inventing_missing_scores(self):
        rows=[{'username':'synthetic-positive','label':'positive'},
              {'username':'synthetic-tied','label':'trusted_normal'},
              {'username':'synthetic-lower','label':'trusted_normal'},
              {'username':'synthetic-missing','label':'trusted_normal'}]
        scores=dict(zip((r['username'] for r in rows),(20,20,10,None)))
        report=evaluate_cases(rows,lambda target:result('LOW',index=scores[target]))
        ranking=report['ranking']['partitions']
        self.assertEqual(ranking['development']['pairwise_auc'],.75)
        self.assertEqual(ranking['development']['normal_scores'],2)
        self.assertEqual(ranking['development']['tied_pairs'],1)
        self.assertIsNone(ranking['holdout']['pairwise_auc'])
        self.assertEqual(report['coverage']['completed'],4)

    def test_nonfinite_and_boolean_indices_cannot_poison_aggregate_json(self):
        values=(float('nan'),float('inf'),float('-inf'),True,None)
        rows=[{'username':f'synthetic-{i}','label':'positive'} for i in range(len(values))]
        report=evaluate_cases(rows,lambda target:result('HIGH',index=values[int(target.rsplit('-',1)[1])]))
        self.assertIsNone(report['ranking']['median_research_index']['positive'])
        self.assertEqual(report['ranking']['partitions']['development']['positive_scores'],0)
        self.assertEqual(report['cases']['positive']['HIGH'],5)
        json.dumps(report,allow_nan=False)

    def test_abstaining_normals_are_not_credited_as_correct_classifications(self):
        rows=[{'username':'synthetic-positive','label':'positive'},
              {'username':'synthetic-abstain','label':'trusted_normal'},
              {'username':'synthetic-failed','label':'positive'}]
        def analyze(target):
            if target.endswith('failed'):raise RuntimeError('private input must remain hidden')
            return result('HIGH' if target.endswith('positive') else 'INSUFFICIENT DATA')
        report=evaluate_cases(rows,analyze)
        decided=report['decided_high_cutoff'];whole=report['end_to_end_high_cutoff']
        self.assertEqual(decided['normal_decided'],0)
        self.assertEqual(decided['normal_abstained'],1)
        self.assertIsNone(decided['false_positive_rate'])
        self.assertEqual(whole['positive_requested'],2)
        self.assertEqual(whole['positive_failed'],1)
        self.assertEqual(whole['positive_high_fraction'],.5)
        self.assertNotIn('private input',json.dumps(report))


if __name__=='__main__':
    unittest.main()
