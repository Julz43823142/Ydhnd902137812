"""Evidence calibration regressions using synthetic games only, never accounts."""
import copy
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import chess
import fairplay_analysis as analysis
import fairplay_baseline as baseline
import fairplay_clusters as clusters
import fairplay_data as data
import fairplay_timing as timing
import fairplay_ui as ui
from fairplay_config import CONFIG
from test_fairplay import TARGET, sample_row
from test_fairplay_v4 import game, report
import test_fairplay_v4 as fixtures
from test_fairplay_timing import timed_game


class RatedComparisons(unittest.TestCase):
    def test_rated_flag_is_explicit_not_guessed(self):
        for value,expected in [(True,True),(False,False),(None,None),('true',None),(1,None)]:
            row=sample_row();row['rated']=value
            self.assertIs(data.parse_game(row,TARGET).rated,expected)

    def test_all_three_rated_states_have_separate_comparison_groups(self):
        games=[game(i) for i in range(3)]
        for g,rated in zip(games,(True,False,None)):g.rated=rated
        self.assertEqual(len(clusters.buckets(games)),3)

    def test_casual_to_rated_improvement_is_not_a_personal_change(self):
        games=[game(i,i>=6) for i in range(12)]
        for i,g in enumerate(games):g.rated=i>=6
        self.assertFalse(clusters.regime_changes(games))
        self.assertTrue(all(r['state']=='Insufficient Data' for r in baseline.personal_timing(games)))

    def test_elo_context_does_not_call_casual_or_unknown_games_rated(self):
        games=[game(i,True) for i in range(30)]
        for g in games:g.rated=False
        for g in games[:10]:g.rated=None
        context=analysis.context_metrics(games,{})['classes']['blitz']
        self.assertEqual(context['rated_games'],0)
        self.assertIsNone(context['rating_gain']);self.assertIsNone(context['excess_z'])

    def test_primary_prioritizes_rated_but_preserves_remaining_casual_slots(self):
        games=[game(i,deep=False) for i in range(8)]
        for i,g in enumerate(games):g.rated=i<2
        calls=[]
        class Scanner:
            name='Synthetic'
            def __init__(self,*args):pass
            def analyse(self,g,nodes):calls.append((g.identity,nodes))
            def close(self):pass
        api=Mock();api.get.return_value={'username':TARGET}
        config=replace(CONFIG,primary_engine_games=3,deep_games=0,historical_target_games=0)
        with patch.object(analysis,'collect_games',return_value=(games,{},False)),patch.object(analysis,'EngineScanner',Scanner):
            result=analysis.review(TARGET,lambda _:None,config,api_factory=lambda _:api)
        self.assertEqual([row[0] for row in calls[:3]],[games[7].identity,games[1].identity,games[0].identity])
        self.assertEqual(result.coverage['rated_primary'],2)
        self.assertEqual(result.coverage['casual_primary'],1)
        self.assertEqual(result.coverage['history_probed'],5)


class ClockShapes(unittest.TestCase):
    def test_occasional_pause_does_not_hide_delayed_cadence(self):
        row=timing.cadence([5]*18+[1,60])
        self.assertTrue(row['elevated'])
        self.assertGreater(row['cv'],1)
        self.assertEqual(row['mad'],0)

    def test_short_delay_uses_proportionate_band_not_a_five_second_rule(self):
        row=timing.cadence([1.3,1.4,1.5]*10)
        self.assertTrue(row['elevated']);self.assertLess(row['band_halfwidth'],.4)
        self.assertFalse(timing.cadence([.1]*40)['elevated'])
        self.assertFalse(timing.cadence([0,1,2,3]*10)['elevated'])

    def test_premoves_cannot_be_removed_to_create_a_delayed_pattern(self):
        self.assertFalse(timing.cadence([.1]*15+[5]*15)['elevated'])
        profile=baseline.profile_stats([.1]*15+[5]*15)
        self.assertEqual(profile['near_instant_fraction'],.5)
        self.assertEqual(profile['median'],2.55)
        self.assertEqual(profile['count'],30)

    def test_already_won_and_trivial_moves_retain_timing_data(self):
        g=timed_game()
        for d in g.decisions:d.metrics['useful']=False
        result=analysis.summarize(g)
        self.assertEqual(result['decisions'],0)
        self.assertEqual(result['timing']['count'],24)
        self.assertTrue(result['timing']['elevated'])

    def test_clock_exclusions_remain_excluded(self):
        g=timed_game()
        for d in g.decisions:d.clock_valid=False;d.clock_reliable=False
        self.assertFalse(timing.clock_values(g))
        self.assertFalse(timing.cadence_recurrence([g]*10)['recurrent'])

    def test_short_games_pool_coverage_without_one_long_game_dominating(self):
        games=[timed_game(i) for i in range(10)]
        for g in games:g.decisions=g.decisions[:8]
        self.assertFalse(timing.cadence_recurrence(games[:5])['recurrent'])
        row=timing.cadence_recurrence(games)
        self.assertTrue(row['recurrent']);self.assertEqual(row['moves'],80)

    def test_low_anomaly_background_does_not_dilute_timing_period(self):
        normal=[timed_game(i,trivial=.1,normal=[1,2,5,20],critical=[2,5,10,25]) for i in range(40)]
        fixed=[timed_game(i+40) for i in range(6)]
        row=timing.cadence_recurrence(normal+fixed)
        self.assertTrue(row['recurrent']);self.assertEqual(row['games'],6)

    def test_few_critical_clocks_per_game_can_accumulate_reliable_coverage(self):
        games=[timed_game(i) for i in range(5)]
        for g in games:
            trivial=[d for d in g.decisions if d.trivial_kind][:2]
            normal=[d for d in g.decisions if d.metrics['useful'] and not d.metrics['critical']]
            critical=[d for d in g.decisions if d.metrics['critical']][:2]
            g.decisions=trivial+normal+critical;analysis.summarize(g)
            self.assertFalse(g.metrics['timing']['trivial_delay']['sufficient'])
        self.assertTrue(timing.trivial_delay_summary(games)['recurrent'])

    def test_delayed_floor_survives_pauses_without_claiming_every_move_is_fixed(self):
        games=[timed_game(i,trivial=[1.1,1.6,1.9],normal=[1.0,1.3,1.6,1.9,2.2,20],critical=[1.3,1.7,2]) for i in range(6)]
        row=timing.delay_floor_profile(games)
        self.assertTrue(row['elevated'])
        self.assertFalse(timing.cadence([d.think for g in games for d in g.decisions])['elevated'])
        self.assertEqual(row['score'],.55)

    def test_natural_fast_recaptures_do_not_create_uniform_delay(self):
        games=[timed_game(i,trivial=[.1,.2,1],normal=[1,2,5,12],critical=[1,5,12,20]) for i in range(12)]
        self.assertFalse(timing.delay_floor_profile(games)['elevated'])

    def test_one_game_cannot_supply_all_trivial_evidence(self):
        games=[timed_game(i) for i in range(6)]
        games[0].decisions*=4
        for g in games[1:]:g.decisions=[d for d in g.decisions if not d.trivial_kind]
        self.assertFalse(timing.delay_floor_profile(games)['sufficient'])


class EvidenceGates(unittest.TestCase):
    def test_critical_precision_alone_is_not_moderate(self):
        value=analysis.priority_model((.1,.99,0,0,.1),games=100,decisions=2000,critical=200,
                                     confidence='HIGH',deep_confirmed=True,partial=False)
        self.assertEqual(value,'LOW')

    def test_stable_elite_precision_has_no_behavioral_anomaly(self):
        games=[game(i,True,rating=2700) for i in range(40)]
        for g in games:
            for d,reference in zip(g.decisions,game(0,False).decisions):
                d.think=reference.think;d.clock_valid=reference.clock_valid;d.clock_reliable=reference.clock_reliable
            analysis.summarize(g)
        self.assertEqual(report(games,{'title':'GM'}).priority,'LOW')

    def test_timing_habit_with_ordinary_engine_quality_never_high(self):
        result=report([timed_game(i) for i in range(40)])
        self.assertEqual(result.priority,'LOW')
        self.assertGreaterEqual(result.diagnostics['scores']['Move-Time Pattern'],.5)

    def test_other_time_control_timing_cannot_confirm_engine_period(self):
        strong=[game(i,True,clocks=False) for i in range(40)]
        weak=[timed_game(i+40) for i in range(15)]
        for g in weak:g.time_control='180+2'
        result=report(strong+weak)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertEqual(result.diagnostics['gate_scores']['Move-Time Pattern'],0)

    def test_other_rated_mode_timing_cannot_confirm_engine_period(self):
        strong=[game(i,True,clocks=False) for i in range(40)]
        weak=[timed_game(i+40) for i in range(15)]
        for g in strong:g.rated=True;g.score=.5;g.result='Draw'
        for g in weak:g.rated=False
        result=report(strong+weak)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertEqual(result.diagnostics['gate_scores']['Move-Time Pattern'],0)

    def test_other_control_recurrence_cannot_confirm_the_main_period(self):
        main=[game(i,i<40,clocks=False) for i in range(46)]
        other=[game(i+50,i<6 or i>=12,clocks=False,control='180+2') for i in range(18)]
        result=report(main+other)
        self.assertTrue(clusters.find_clusters(main+other)['recurrence'])
        self.assertFalse(result.clusters['recurrence'])
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_effective_sample_is_not_penalized_twice(self):
        metrics={'decisions':10000,'effective_decisions':2500,'top1':1,'weighted_top1':1,'top3':1,'robust_cpl':0}
        self.assertGreater(clusters.evidence(metrics)[0],.95)
        self.assertLess(clusters.evidence({**metrics,'effective_decisions':5})[0],.2)

    def test_weighted_agreement_uses_weighted_denominator_across_games(self):
        games=[game(i) for i in range(2)]
        games[0].metrics.update(decisions=100,effective_decisions=10,weighted_top1=1)
        games[1].metrics.update(decisions=10,effective_decisions=10,weighted_top1=0)
        self.assertEqual(clusters.summary(games)['weighted_top1'],.5)

    def test_persistence_does_not_require_three_critical_opportunities_every_game(self):
        games=[game(i) for i in range(6)]
        for g in games:g.metrics.update(critical=2,critical_top1=1,weighted_top1=.5)
        self.assertTrue(clusters.group_record(games,'chronological')['persistent'])
        for g in games[:3]:g.metrics.update(critical_top1=0)
        self.assertFalse(clusters.group_record(games,'chronological')['persistent'])

    def test_deep_selection_covers_recent_and_earlier_parts(self):
        games=[game(i,True,deep=False) for i in range(40)]
        selected=clusters.select_deep_games(games)
        self.assertIn(games[0],selected);self.assertIn(games[-1],selected)
        self.assertLessEqual(len(selected),CONFIG.deep_games)

    def test_post_engine_exclusions_do_not_erase_the_period_selected_for_deep_review(self):
        games=[game(i,True) for i in range(40)]
        for i,g in enumerate(games):
            g.control_index=i
            if i%2:
                useful=[d for d in g.decisions if d.metrics.get('useful')]
                for d in useful[4:]:d.metrics['useful']=False
                analysis.summarize(g)
                g.fast_metrics={k:v for k,v in g.metrics.items() if k!='timing'}
        discovered=clusters.find_clusters(games,fast=True)['strongest']
        self.assertIsNotNone(discovered)
        result=report(games)
        self.assertEqual(result.clusters['strongest']['ids'],discovered['ids'])
        self.assertEqual(result.coverage['excluded_after_fast'],20)
        self.assertEqual(len(result.games),40)
        self.assertTrue(result.deep_confirmed)

    def test_title_does_not_exempt_repeated_delayed_trivial_and_critical_choices(self):
        result=report([game(i,True,rating=2700) for i in range(40)],{'title':'GM'})
        self.assertIn(result.priority,('HIGH','VERY HIGH'))
        self.assertFalse(result.diagnostics['stable_strong_play'])

    def test_deep_retention_is_not_blocked_by_a_second_sample_shrink(self):
        games=[game(i,True) for i in range(15)]
        for i,g in enumerate(games):
            g.deep=i<7
            for d in g.decisions:d.metrics['critical']=d.metrics['unique']=False
            moves=[d for d in g.decisions if d.metrics['useful']]
            for d in moves[:3]:d.metrics['critical']=d.metrics['unique']=True
            if i%3==0:moves[0].metrics['top1']=False
            analysis.summarize(g);g.fast_metrics={k:v for k,v in g.metrics.items() if k!='timing'}
        cluster=clusters.group_record(games,'chronological',fast=True)
        cluster['engine_score']=0;cluster['critical_score']=.8
        confirmed=clusters.confirm_cluster(cluster,games)
        self.assertEqual(confirmed['critical_count'],21)
        self.assertTrue(confirmed['critical']);self.assertTrue(confirmed['confirmed'])
        # The coverage minimum still applies even to perfect effect retention.
        games[6].deep=False
        self.assertFalse(clusters.confirm_cluster(cluster,games)['critical'])

    def test_missing_clock_family_is_distinct_from_not_elevated(self):
        result=report([game(i,clocks=False) for i in range(30)])
        self.assertEqual(result.families['Move-Time Pattern'],'Insufficient clock data')
        normal=report([game(i) for i in range(30)])
        self.assertEqual(normal.families['Move-Time Pattern'],'Not elevated')


class EngineAndReports(unittest.TestCase):
    def test_root_search_contradiction_is_not_perfect_precision(self):
        helper=fixtures.StructuralEvidence();d=helper.decision();lines=helper.lines(d)
        board=chess.Board(d.fen)
        d.move=next(m.uci() for m in board.legal_moves if m not in [r['pv'][0] for r in lines])
        actual={'score':chess.engine.PovScore(chess.engine.Cp(400),chess.WHITE)}
        result=analysis.engine_metrics(d,lines,actual,chess.WHITE)
        self.assertTrue(result['search_inconsistent']);self.assertFalse(result['useful'])
        self.assertFalse(result['near_best']);self.assertFalse(result['critical'])

    def test_equivalent_choices_are_visible_without_claiming_top_one_agreement(self):
        helper=fixtures.StructuralEvidence();d=helper.decision();lines=helper.lines(d)
        lines[1]['score']=chess.engine.PovScore(chess.engine.Cp(240),chess.WHITE)
        d.move=lines[1]['pv'][0].uci()
        row=analysis.engine_metrics(d,lines,{},chess.WHITE)
        self.assertFalse(row['top1']);self.assertTrue(row['near_best'])
        self.assertEqual(row['equivalent_candidates'],2)
        self.assertGreater(row['scaled_loss'],0)

    def test_scaled_evaluation_loss_is_smaller_in_decisive_positions(self):
        helper=fixtures.StructuralEvidence();d=helper.decision();lines=helper.lines(d)
        d.move=lines[1]['pv'][0].uci()
        for line,value in zip(lines,(100,0,-100)):line['score']=chess.engine.PovScore(chess.engine.Cp(value),True)
        balanced=analysis.engine_metrics(d,lines,{},True)
        for line,value in zip(lines,(1000,900,800)):line['score']=chess.engine.PovScore(chess.engine.Cp(value),True)
        decisive=analysis.engine_metrics(d,lines,{},True)
        self.assertLess(decisive['scaled_loss'],balanced['scaled_loss'])
        self.assertFalse(decisive['useful'])

    def test_reports_explain_coverage_and_priority_gates(self):
        result=report([game(i,i>=30) for i in range(40)])
        for mode in ('Timing','Engine Analysis','Performance','Clusters & History','Highest-Signal Games'):
            embed=ui.detail_embed(result,mode)
            self.assertLessEqual(len(embed),6000)
            self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))
        timing_text=str(ui.detail_embed(result,'Timing').to_dict())
        self.assertIn('Clock coverage',timing_text)
        self.assertIn('Cross-category delay',timing_text)
        self.assertIn('Priority checks',str(ui.detail_embed(result,'Engine Analysis').to_dict()))


if __name__=='__main__':unittest.main()
