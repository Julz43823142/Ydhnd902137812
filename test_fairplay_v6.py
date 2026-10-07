"""Rated-only calibration and false-positive regressions. All players are synthetic."""
import copy
import time
import unittest
from collections import Counter
from dataclasses import replace
from unittest.mock import Mock, patch

import fairplay_analysis as analysis
import fairplay_clusters as clusters
import fairplay_calibration as calibration
import fairplay_data as data
import fairplay_ui as ui
from fairplay_config import CONFIG
from test_fairplay import TARGET, sample_row
from test_fairplay_v4 import game, report


def played(index, high=False, *, clocks=True, rating=1800):
    result=game(index,high,clocks=clocks,rating=rating)
    result.rated=True;result.result='Draw';result.score=.5
    result.ended=1700000000+index*3600
    return result


def quality(index, top=.8, cpl=15, *, clock_style=False):
    result=played(index,clock_style)
    moves=[d for d in result.decisions if d.metrics.get('useful')]
    for i,d in enumerate(moves):
        d.metrics.update(top1=i<round(len(moves)*top),top3=True,cpl=cpl)
    critical=[d for d in moves if d.metrics.get('critical')]
    for i,d in enumerate(critical):d.metrics['top1']=i<round(len(critical)*top)
    analysis.summarize(result);result.fast_metrics={k:v for k,v in result.metrics.items() if k!='timing'}
    return result


class RatedOnly(unittest.TestCase):
    def test_explicit_boolean_rated_required_with_skip_counters(self):
        for value,reason in ((False,'unrated'),(None,'rated_status_unknown'),('true','rated_status_unknown'),(1,'rated_status_unknown')):
            row=sample_row();row['rated']=value;skipped=Counter()
            self.assertIsNone(data.parse_game(row,TARGET,exclusions=skipped))
            self.assertEqual(skipped[reason],1)
        row=sample_row();row.pop('rated');skipped=Counter()
        self.assertIsNone(data.parse_game(row,TARGET,exclusions=skipped))
        self.assertEqual(skipped['rated_status_unknown'],1)
        self.assertIsNotNone(data.parse_game(sample_row(),TARGET))

    def collect(self, rows, config=CONFIG):
        api=Mock();api.deadline=time.monotonic()+120
        api.get.side_effect=lambda target,suffix: {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']} if suffix.endswith('archives') else {'games':rows}
        return data.collect_games(api,TARGET,lambda _:None,config)

    def test_history_skips_120_casual_and_keeps_80_rated(self):
        template=sample_row();rows=[]
        for i in range(200):
            row=copy.deepcopy(template);row.update(uuid=f'synthetic-rated-{i}',end_time=1700000000+i,rated=i%5<2)
            rows.append(row)
        history,skipped,partial=self.collect(rows)
        self.assertEqual(len(history),80);self.assertEqual(skipped['unrated'],120)
        self.assertTrue(all(g.rated is True for g in history));self.assertFalse(partial)

    def test_pipeline_retains_120_context_but_scans_latest_100_rated(self):
        history=[played(i) for i in range(180)]
        for i,g in enumerate(history):g.rated=i%3!=0
        calls=[]
        class Scanner:
            name='Synthetic'
            def __init__(self,*args):pass
            def analyse(self,g,nodes):calls.append((g.identity,nodes))
            def close(self):pass
        api=Mock();api.get.return_value={'username':TARGET}
        config=replace(CONFIG,deep_games=0,historical_target_games=0,historical_probe_budget_fraction=0)
        with patch.object(analysis,'collect_games',return_value=(list(reversed(history)),{},False)),patch.object(analysis,'EngineScanner',Scanner):
            result=analysis.review(TARGET,lambda _:None,config,api_factory=lambda _:api)
        expected=[g.identity for g in history if g.rated is True][-100:]
        self.assertEqual([row[0] for row in calls],list(reversed(expected)))
        self.assertEqual(result.coverage['primary_collected'],100)
        self.assertEqual(result.history['collected'],120)
        self.assertTrue(all(g.rated is True for g in result.games))

    def test_unrated_extreme_games_cannot_change_analytical_result(self):
        rated=[played(i) for i in range(20)]
        casual=[played(100+i,True) for i in range(25)]
        for i,g in enumerate(casual):g.rated=False if i%2 else None
        before,after=report(rated),report(rated+casual)
        self.assertEqual(before.priority,after.priority)
        self.assertEqual(before.totals,after.totals)
        self.assertEqual(before.families,after.families)
        self.assertFalse(clusters.find_clusters(casual)['candidates'])
        self.assertFalse(clusters.regime_changes(casual))


class Uncertainty(unittest.TestCase):
    def test_wilson_bounds_distinguish_tiny_hit_samples(self):
        self.assertLess(calibration.lower_bound(1,2),.5)
        self.assertGreater(calibration.lower_bound(1,20),.8)
        self.assertGreater(calibration.lower_bound(.9,100),.8)

    def test_rates_shrink_toward_expectation_not_zero(self):
        self.assertAlmostEqual(calibration.posterior_rate(1,2,.6,8),.68)
        self.assertGreater(calibration.posterior_rate(1,100,.6,8),.95)

    def test_two_critical_hits_do_not_create_persistence(self):
        games=[played(i) for i in range(6)]
        for g in games:g.metrics.update(critical=2,critical_top1=1,weighted_top1=.5)
        row=clusters.group_record(games,'chronological')
        self.assertEqual(row['metrics']['critical_top1'],1)
        self.assertFalse(row['persistent'])
        self.assertFalse(calibration.high_cluster_qualification(row,games))

    def test_personal_flag_never_bypasses_rating_expectation(self):
        metrics=clusters.summary([quality(i,.8,10) for i in range(20)])
        self.assertEqual(clusters.evidence(metrics,rating=2700),clusters.evidence(metrics,rating=2700,personal=True))
        self.assertGreater(clusters.evidence(metrics,rating=1200)[0],clusters.evidence(metrics,rating=2700)[0])


class PersonalCalibration(unittest.TestCase):
    def test_stable_untitled_player_best_window_is_not_high(self):
        games=[quality(i,.79,16) for i in range(100)]
        games[40:48]=[quality(i,.83,13) for i in range(40,48)]
        result=report(games)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertFalse(result.clusters['personal']['established'])
        self.assertFalse(result.diagnostics['independent_support'])

    def test_six_game_hot_streak_without_corroboration_not_high(self):
        games=[quality(i,.8,15) for i in range(60)]
        games[30:36]=[quality(i,.9,10) for i in range(30,36)]
        result=report(games)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_stable_elite_and_deep_strength_are_not_personal_anomaly(self):
        games=[quality(i,.97,4) for i in range(100)]
        for g in games:g.rating=2800
        result=report(games,{'title':'GM'})
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertFalse(result.clusters['deep'].get('anomaly_confirmed'))

    def test_true_cluster_retains_personal_anomaly_and_timing_support(self):
        result=report([played(i,i>=40) for i in range(50)])
        self.assertIn(result.priority,('HIGH','VERY HIGH'))
        self.assertTrue(result.clusters['personal']['established'])
        self.assertTrue(result.clusters['deep']['anomaly_confirmed'])
        self.assertTrue(result.diagnostics['same_period_support'])
        self.assertEqual(result.diagnostics['gate_scores']['Performance Shift'],0)

    def test_engine_regime_change_alone_is_not_independent(self):
        result=report([played(i,i>=40,clocks=False) for i in range(50)])
        self.assertTrue(result.performance['regime_changes'])
        self.assertTrue(result.clusters['personal']['established'])
        self.assertFalse(result.diagnostics['independent_support'])
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_leave_cluster_out_keeps_no_members_or_other_control(self):
        games=[played(i,i>=40) for i in range(50)]
        cluster=clusters.group_record(games[-10:],'chronological',fast=True)
        outside=played(99);outside.time_control='180+2'
        comparison=calibration.baseline_comparison(cluster,games+[outside])
        self.assertEqual(comparison['baseline']['games'],40)
        self.assertFalse(set(cluster['ids'])&set(comparison['baseline_ids']))
        self.assertNotIn(outside.identity,comparison['baseline_ids'])
        self.assertGreater(comparison['deltas']['weighted_top1'],.3)
        self.assertLess(comparison['deltas']['robust_cpl'],-40)

    def test_deep_selection_reserves_representative_baseline_controls(self):
        games=[played(i,i>=40) for i in range(50)]
        for g in games:g.deep=False
        selected=clusters.select_deep_games(games)
        cluster=clusters.find_clusters(games,fast=True)['strongest']
        controls=calibration.representative_controls(calibration.comparable_baseline(cluster,games))
        self.assertEqual(len(selected),10)
        self.assertEqual(len([g for g in selected if g.identity in cluster['ids']]),7)
        self.assertEqual({g.identity for g in selected if g.identity not in cluster['ids']},{g.identity for g in controls})

    def test_deep_baseline_improvement_erases_fast_anomaly(self):
        games=[played(i,i>=40) for i in range(50)]
        cluster=clusters.find_clusters(games,fast=True)['strongest']
        controls=calibration.representative_controls(calibration.comparable_baseline(cluster,games))
        for g in controls:
            g.decisions=copy.deepcopy(played(100,True).decisions);analysis.summarize(g)
        result=report(games)
        self.assertTrue(result.deep_confirmed)
        self.assertFalse(result.clusters['deep']['anomaly_confirmed'])
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_small_normal_account_gets_report_and_extreme_can_high(self):
        ordinary=report([quality(i,.8,15) for i in range(10)])
        self.assertNotEqual(ordinary.priority,'INSUFFICIENT DATA')
        self.assertNotIn(ordinary.priority,('HIGH','VERY HIGH'))
        extreme=report([played(i,True) for i in range(10)])
        self.assertEqual(extreme.priority,'HIGH')
        self.assertEqual(extreme.confidence,'MEDIUM')

    def test_engine_shift_score_never_unlocks_high_directly(self):
        args=dict(games=40,decisions=1000,critical=100,confidence='HIGH',deep_confirmed=True,partial=False,
                  baseline_anomaly=True,baseline_confirmed=True)
        self.assertNotIn(analysis.priority_model((1,1,0,1,0),**args),('HIGH','VERY HIGH'))
        self.assertEqual(analysis.priority_model((.9,.9,.8,0,0),**args),'VERY HIGH')

    def test_ui_explains_rated_sample_baseline_and_blocked_gate(self):
        result=report([quality(i,.8,15) for i in range(40)])
        text=str(ui.result_embed(result).to_dict())
        self.assertIn('Rated context games',text)
        for mode in ('Engine Analysis','Clusters & History'):
            embed=ui.detail_embed(result,mode)
            self.assertIn('Absolute gameplay HIGH — FAIL',str(embed.to_dict()))
            self.assertLessEqual(len(embed),6000)
            self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))
        self.assertIn('Same-control rated baseline',str(ui.detail_embed(result,'Clusters & History').to_dict()))


class CalibrationBoundaries(unittest.TestCase):
    def test_missing_cpl_does_not_manufacture_precision(self):
        metrics={'decisions':1000,'top1':0,'top3':0,'critical':0}
        self.assertEqual(clusters.evidence(metrics)[0],0)

    def test_unique_hit_rate_has_its_own_denominator(self):
        metrics={'decisions':400,'effective_decisions':400,'critical':100,'critical_top1':.7,
                 'unique':2,'unique_hits':2,'weighted_top1':.8,'top3':.95,'robust_cpl':15}
        small=clusters.evidence(metrics)[1]
        large=clusters.evidence({**metrics,'unique':100,'unique_hits':100})[1]
        self.assertLess(small,large)

    def test_deep_confirmation_uses_actual_strength_without_personal_switch(self):
        games=[quality(i,.82,15) for i in range(10)]
        for g in games:g.rating=2700
        row=clusters.group_record(games,'chronological',fast=True)
        with patch.object(clusters,'evidence',wraps=clusters.evidence) as evidence:
            clusters.confirm_cluster(row,games)
        self.assertEqual(len(evidence.call_args_list),2)
        for call in evidence.call_args_list:
            self.assertEqual(call.kwargs['rating'],2700)
            self.assertNotIn('personal',call.kwargs)

    def test_large_baseline_dispersion_reduces_apparent_change(self):
        baseline=[quality(i,.45,25 if i%2 else 105) for i in range(40)]
        members=[quality(i,.9,25) for i in range(40,50)]
        row=clusters.group_record(members,'chronological',fast=True)
        comparison=calibration.baseline_comparison(row,baseline+members)
        self.assertGreater(comparison['deltas']['weighted_top1'],.3)
        self.assertLess(comparison['effect_mad'],CONFIG.baseline_effect_mad)
        self.assertFalse(comparison['established'])

    def test_two_representative_deep_controls_cannot_confirm_personal_anomaly(self):
        games=[played(i,i>=40) for i in range(50)]
        row=clusters.find_clusters(games,fast=True)['strongest']
        controls=calibration.representative_controls(calibration.comparable_baseline(row,games))
        controls[-1].deep=False
        deep=clusters.confirm_cluster(row,games)
        self.assertEqual(deep['baseline_games'],2)
        self.assertFalse(deep['anomaly_confirmed'])

    def test_disjoint_recurrence_supports_high_without_clocks(self):
        result=report([played(i,20<=i<30 or 60<=i<70,clocks=False) for i in range(90)])
        self.assertTrue(result.clusters['recurrence'])
        self.assertTrue(result.clusters['deep']['anomaly_confirmed'])
        self.assertEqual(result.priority,'HIGH')
        self.assertEqual(result.families['Move-Time Pattern'],'Insufficient clock data')

    def test_one_lower_quality_game_does_not_establish_separate_recurrence(self):
        games=[played(i,not i==15,clocks=False) for i in range(31)]
        self.assertFalse(clusters.find_clusters(games)['recurrence'])

    def test_small_account_good_partial_period_cannot_high(self):
        result=report([played(i,i>=5) for i in range(15)])
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertIn('Small sample',str(result.diagnostics['high_blocked']))

    def test_six_game_large_extreme_cluster_can_qualify_exception(self):
        games=[played(i,True) for i in range(6)]
        for g in games:
            useful=[d for d in g.decisions if d.metrics.get('useful')]
            g.decisions.extend(copy.deepcopy(useful))
            analysis.summarize(g);g.fast_metrics={k:v for k,v in g.metrics.items() if k!='timing'}
        row=clusters.group_record(games,'chronological',fast=True)
        self.assertTrue(calibration.high_cluster_qualification(row,games))
        # Six games remain below the overall ten-game report minimum.
        self.assertEqual(report(games).priority,'INSUFFICIENT DATA')

    def test_baseline_filters_unknown_control_and_unrated_even_directly(self):
        baseline=[played(i) for i in range(20)]
        members=[played(i,True) for i in range(20,30)]
        for g in baseline:g.time_control=''
        row=clusters.group_record(members,'chronological',fast=True)
        self.assertFalse(calibration.comparable_baseline(row,baseline+members))
        for g in baseline:g.time_control='600+0';g.rated=False
        self.assertFalse(calibration.comparable_baseline(row,baseline+members))


class ValidationPresentation(unittest.TestCase):
    def test_private_harness_counts_both_normal_thresholds_without_names(self):
        import json
        from types import SimpleNamespace
        import fairplay_validation as validation
        calls=[]
        def analyze(target):
            calls.append(target)
            return SimpleNamespace(priority='MODERATE')
        summary=validation.evaluate_cases([{'username':TARGET,'label':'trusted_normal'}],analyze)
        self.assertEqual(calls,[TARGET])
        self.assertEqual(summary['trusted_normal_completed'],1)
        self.assertEqual(summary['trusted_normal_high_or_higher'],0)
        self.assertEqual(summary['trusted_normal_moderate_or_higher'],1)
        self.assertNotIn(TARGET,json.dumps(summary))

    def test_high_trigger_and_personal_timing_details_fit_discord(self):
        result=report([played(i,i>=40) for i in range(50)])
        self.assertEqual(result.diagnostics['high_path'],'Personal anomaly with independent support')
        card=ui.detail_embed(result,'Clusters & History')
        self.assertIn('Leave-cluster-out timing comparison',str(card.to_dict()))
        self.assertIn('Personal anomaly with independent support',str(card.to_dict()))
        self.assertTrue(all(len(f.value)<=1024 for f in card.fields))
        self.assertLessEqual(len(card),6000)


class CriticalControlCoverage(unittest.TestCase):
    def test_representative_controls_with_few_critical_moves_use_uncertainty(self):
        games=[played(i,i>=40) for i in range(50)]
        # Many baseline critical opportunities collectively, but only two per
        # individual control; counting them as precise/strong games is forbidden.
        for g in games[:40]:
            critical=[d for d in g.decisions if d.metrics.get('critical')]
            for d in critical[2:]:d.metrics.update(critical=False,unique=False)
            for d in critical[:2]:d.metrics.update(top1=False)
            analysis.summarize(g);g.fast_metrics={k:v for k,v in g.metrics.items() if k!='timing'}
        row=clusters.find_clusters(games,fast=True)['strongest']
        deep=clusters.confirm_cluster(row,games)
        self.assertEqual(deep['deep_personal']['baseline']['critical'],6)
        self.assertTrue(deep['deep_personal']['critical'])
        self.assertGreater(deep['deep_personal']['rate_delta_bounds']['critical_top1'],.2)
        self.assertTrue(deep['anomaly_confirmed'])

    def test_perfect_tiny_controls_do_not_artificially_establish_deep_delta(self):
        games=[played(i,i>=40) for i in range(50)]
        row=clusters.find_clusters(games,fast=True)['strongest']
        controls=calibration.representative_controls(calibration.comparable_baseline(row,games))
        for g in controls:
            g.decisions=copy.deepcopy(played(100,True).decisions)
            critical=[d for d in g.decisions if d.metrics.get('critical')]
            for d in critical[2:]:d.metrics.update(critical=False,unique=False)
            analysis.summarize(g)
        deep=clusters.confirm_cluster(row,games)
        self.assertFalse(deep['deep_personal']['critical'])
        self.assertFalse(deep['anomaly_confirmed'])


class DescriptiveCoverage(unittest.TestCase):
    def samples(self,clocks=True):
        games=[played(i,True,clocks=clocks) for i in range(30)]
        for index,g in enumerate(games):
            g.control_index=index*2  # excluded intervening games must break periods
            useful=[d for d in g.decisions if d.metrics.get('useful')]
            critical=[d for d in useful if d.metrics['critical']]
            for d in critical[2:]:d.metrics.update(critical=False,unique=False)
            for i,d in enumerate(useful):
                if d not in critical[:2]:d.metrics.update(top1=i%2==0,cpl=30)
            analysis.summarize(g);g.fast_metrics={k:v for k,v in g.metrics.items() if k!='timing'}
        return games

    def test_aggregate_signals_are_not_erased_by_no_qualifying_cluster(self):
        result=report(self.samples())
        self.assertIsNone(result.clusters['strongest'])
        self.assertGreaterEqual(result.totals['critical'],60)
        self.assertEqual(result.priority,'MODERATE')
        self.assertTrue(result.diagnostics['descriptive_control_fallback'])
        self.assertFalse(result.diagnostics['qualifying_cluster'])

    def test_fallback_does_not_make_critical_agreement_alone_evidence(self):
        result=report(self.samples(clocks=False))
        self.assertEqual(result.priority,'LOW')
        self.assertFalse(result.diagnostics['independent_support'])


class PooledCriticalReplication(unittest.TestCase):
    def test_repeated_large_pool_can_qualify_without_tiny_per_game_certainty(self):
        games=[played(i,i>=40) for i in range(50)]
        for g in games[40:]:
            useful=[d for d in g.decisions if d.metrics.get('useful')]
            critical=[d for d in useful if d.metrics['critical']]
            for d in critical[3:]:d.metrics.update(critical=False,unique=False)
            for i,d in enumerate(useful):
                if d not in critical[:3]:d.metrics.update(top1=i%2==0)
            analysis.summarize(g);g.fast_metrics={k:v for k,v in g.metrics.items() if k!='timing'}
        result=report(games)
        period=result.clusters['strongest']
        self.assertEqual(period['sustained_games'],0)
        self.assertTrue(period['pooled_critical_support'])
        self.assertGreaterEqual(period['metrics']['critical'],30)
        self.assertTrue(result.clusters['deep']['anomaly_confirmed'])
        self.assertIn(result.priority,('HIGH','VERY HIGH'))

    def test_six_two_hit_games_cannot_use_pooled_replication(self):
        games=[played(i) for i in range(6)]
        for g in games:g.metrics.update(critical=2,critical_top1=1,weighted_top1=.5)
        row=clusters.group_record(games,'chronological')
        self.assertFalse(row['pooled_critical_support'])
        self.assertFalse(row['persistent'])

    def test_no_repetition_cannot_be_hidden_inside_a_large_pool(self):
        games=[played(i) for i in range(15)]
        for i,g in enumerate(games):g.metrics.update(critical=10 if i==0 else 0,critical_top1=1,weighted_top1=.5)
        games[0].metrics['critical']=100
        row=clusters.group_record(games,'chronological')
        self.assertFalse(row['pooled_critical_support'])


class SameControlDescriptiveSelection(unittest.TestCase):
    def test_tiny_unsupported_bucket_does_not_hide_larger_corroborated_bucket(self):
        from fairplay_scoring import descriptive_group_rank
        large=DescriptiveCoverage().samples()
        tiny=[played(100+i,True,clocks=False) for i in range(5)]
        for g in tiny:g.time_control='180+2'
        self.assertGreater(descriptive_group_rank(large,{},[]),descriptive_group_rank(tiny,{},[]))
        result=report(large+tiny)
        self.assertIsNone(result.clusters['strongest'])
        self.assertEqual(result.priority,'MODERATE')
        self.assertGreaterEqual(result.diagnostics['gate_scores']['Move-Time Pattern'],.5)
        self.assertFalse(result.deep_confirmed)
