"""Synthetic architecture and safety regressions; no external account labels."""
import copy
import json
import time
import unittest
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import chess
import fairplay_analysis as a
import fairplay_clusters as clusters
import fairplay_data as data
import fairplay_history as history
import fairplay_ui as ui
import fairplay_validation as validation
from fairplay_config import CONFIG, VERSION
from test_fairplay import TARGET, sample_row, FakeEngine
from test_fairplay_baseline import personal_game

_TEMPLATES = {}

def game(index, high=False, *, deep=True, control='600+0', rating=1500, clocks=True):
    if high not in _TEMPLATES:_TEMPLATES[high]=personal_game(0,high)
    result=copy.deepcopy(_TEMPLATES[high]);result.identity=f'synthetic-v4-{index}'
    result.ended=1700000000+index*1800;result.time_control=control;result.deep=deep
    result.rating=rating;result.opponent_rating=rating
    if not clocks:
        for d in result.decisions:d.clock_valid=d.clock_reliable=False;d.think=None
        a.summarize(result)
        result.fast_metrics={k:v for k,v in result.metrics.items() if k!='timing'}
    return result


def report(games,profile=None,partial=False):
    return a.score_review(TARGET,games,len(games),{},partial,'Synthetic',profile or {},1)


class ClusterSafety(unittest.TestCase):
    def test_normal_history_is_low(self):
        self.assertEqual(report([game(i) for i in range(100)]).priority,'LOW')

    def test_one_or_two_perfect_games_never_high(self):
        for count in (1,2):
            result=report([game(i,i>=100-count) for i in range(100)])
            self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_ten_game_cluster_not_diluted_by_ninety_ordinary(self):
        result=report([game(i,i>=90) for i in range(100)])
        self.assertIn(result.priority,('HIGH','VERY HIGH'))
        self.assertLess(result.totals['top1'],.5)
        self.assertGreater(result.clusters['strongest']['metrics']['top1'],.95)
        self.assertTrue(result.deep_confirmed)

    def test_imperfect_elevated_cluster_is_not_blocked_by_old_quantized_gate(self):
        games=[game(i,i>=30) for i in range(40)]
        for g in games[30:]:
            ordinary=[d for d in g.decisions if d.metrics.get('useful') and not d.metrics.get('critical')]
            critical=[d for d in g.decisions if d.metrics.get('critical')]
            for d in ordinary[:3]+critical[:2]:d.metrics.update(top1=False,cpl=40)
            a.summarize(g);g.fast_metrics={k:v for k,v in g.metrics.items() if k!='timing'}
        result=report(games)
        self.assertEqual(result.priority,'HIGH')
        self.assertLess(result.clusters['strongest']['metrics']['top1'],.9)
        self.assertLess(result.clusters['strongest']['metrics']['critical_top1'],.9)

    def test_eight_game_intermittent_period_is_found(self):
        result=report([game(i,30<=i<38) for i in range(68)])
        self.assertEqual(result.clusters['strongest']['metrics']['games'],8)
        self.assertIn(result.priority,('HIGH','VERY HIGH'))

    def test_two_independent_six_game_periods(self):
        result=report([game(i,20<=i<26 or 50<=i<56) for i in range(80)])
        self.assertTrue(result.clusters['recurrence'])
        self.assertGreaterEqual(len(result.clusters['independent']),2)
        # Discovery recurrence survives; short periods lack HIGH coverage.
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_ranked_nonchronological_subset_cannot_establish_persistence(self):
        result=report([game(i,i%10==0) for i in range(100)])
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_stable_elite_has_no_automatic_high(self):
        games=[game(i,True,rating=2700) for i in range(40)]
        for g in games:
            for d,reference in zip(g.decisions,game(0,False).decisions):
                d.think=reference.think;d.clock_valid=reference.clock_valid;d.clock_reliable=reference.clock_reliable
            a.summarize(g)
        result=report(games,{'title':'GM'})
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertFalse(result.clusters['recurrence'])

    def test_title_does_not_hide_personal_shift(self):
        games=[game(i,i>=30,rating=2600) for i in range(40)]
        result=report(games,{'title':'GM'})
        self.assertIn(result.priority,('HIGH','VERY HIGH'))

    def test_shallow_cluster_without_deep_never_high(self):
        result=report([game(i,i>=30,deep=False) for i in range(40)])
        self.assertFalse(result.deep_confirmed)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_deep_weakening_downgrades_fast_cluster(self):
        games=[game(i,i>=30) for i in range(40)]
        for g in games[30:]:
            g.decisions=game(0,False).decisions;a.summarize(g)
        result=report(games)
        self.assertFalse(result.deep_confirmed)
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))

    def test_missing_clocks_does_not_destroy_engine_confidence(self):
        result=report([game(i,i>=30,clocks=False) for i in range(40)])
        self.assertEqual(result.confidence,'HIGH')
        # Engine-only regime shifts no longer supply independent support.
        self.assertNotIn(result.priority,('HIGH','VERY HIGH'))
        self.assertFalse(result.diagnostics['timing_available'])

    def test_bullet_only_cannot_high(self):
        games=[game(i,i>=30) for i in range(40)]
        for g in games:g.time_class='bullet'
        self.assertNotIn(report(games).priority,('HIGH','VERY HIGH'))

    def test_different_increments_never_form_cluster(self):
        games=[game(i,i>=30,control=f'300+{i}') for i in range(40)]
        self.assertIsNone(clusters.find_clusters(games)['strongest'])
        self.assertNotIn(report(games).priority,('HIGH','VERY HIGH'))

    def test_continuous_evidence_has_no_eighty_percent_cliff(self):
        m={'decisions':500,'critical':100,'unique':100,'unique_hits':80,'top1':.799,'top3':.95,'robust_cpl':10,'critical_top1':.799}
        before=clusters.evidence(m);after=clusters.evidence({**m,'top1':.801,'critical_top1':.801})
        self.assertTrue(all(0<y-x<.02 for x,y in zip(before,after)))

    def test_small_denominators_shrink_evidence(self):
        m=clusters.summary([game(1,True)])
        e,c=clusters.evidence(m)
        large=clusters.evidence(clusters.summary([game(i,True) for i in range(15)]))
        self.assertLess(e,large[0]);self.assertLess(c,large[1])

    def test_critical_only_can_support_high_with_independent_signal(self):
        result=a.priority_model((.45,.92,.8,0,0),games=60,decisions=1000,critical=80,confidence='HIGH',deep_confirmed=True,partial=False,baseline_anomaly=True,baseline_confirmed=True)
        self.assertEqual(result,'HIGH')

    def test_forced_timing_and_new_account_never_high(self):
        for scores in ((0,0,1,0,0),(0,0,0,0,1),(1,0,0,0,0)):
            self.assertNotIn(a.priority_model(scores,games=100,decisions=2000,critical=100,confidence='HIGH',deep_confirmed=True,partial=False),('HIGH','VERY HIGH'))

    def test_eighty_five_used_out_of_hundred_fast_is_explicit(self):
        games=[game(i) for i in range(100)]
        for g in games[:15]:g.metrics['decisions']=0
        result=report(games)
        self.assertEqual(result.coverage['fast_scanned'],100);self.assertEqual(result.coverage['used'],85)
        self.assertEqual(result.coverage['excluded_after_fast'],15)
        self.assertIn('**100**',ui.result_embed(result).fields[2].value)
        self.assertIn('**85**',ui.result_embed(result).fields[2].value)

    def test_all_diagnostic_views_fit_discord_and_remain_neutral(self):
        result=report([game(i,i>=30) for i in range(40)])
        for mode in ('Clusters & History','Engine Analysis','Timing','Performance','Highest-Signal Games'):
            embed=ui.detail_embed(result,mode)
            self.assertLessEqual(len(embed),6000)
            self.assertNotIn('cheating probability',json.dumps(embed.to_dict()).lower())


class ExtendedHistory(unittest.TestCase):
    def test_five_hundred_history_with_twelve_game_block_not_averaged_away(self):
        games=[game(i,200<=i<212) for i in range(500)]
        found=clusters.find_clusters(games)
        self.assertIsNotNone(found['strongest'])
        self.assertGreater(found['strongest']['metrics']['top1'],.95)
        self.assertLess(clusters.summary(games)['top1'],.4)

    def test_stable_history_selects_no_extra_full_scans(self):
        games=[game(i) for i in range(80)]
        probes={g.identity:g.metrics for g in games}
        self.assertEqual(history.historical_candidates(games,probes),[])

    def test_historical_cluster_selects_period_and_comparison_games(self):
        games=[game(i,30<=i<42) for i in range(80)]
        targets=history.historical_candidates(games,{g.identity:g.metrics for g in games})
        selected={g.identity for g in targets}
        self.assertTrue({g.identity for g in games[30:42]}<=selected)
        self.assertTrue(any(g.identity in selected for g in games[24:30]))
        self.assertLessEqual(len(targets),CONFIG.historical_target_games)

    def test_adapter_is_capped_to_200_context_and_100_full_engine(self):
        games=[game(i,deep=False) for i in range(500)]
        calls=[]
        class Scanner:
            name='Synthetic'
            def __init__(self,*args):pass
            def analyse(self,g,nodes):
                calls.append(nodes)
                if nodes==CONFIG.fast_nodes:g.fast_metrics=copy.deepcopy(g.metrics)
            def close(self):pass
        api=Mock();api.get.return_value={'username':TARGET}
        a._game_cache.clear()
        try:
            with patch.object(a,'collect_games',return_value=(games,{},False)),patch.object(a,'EngineScanner',Scanner):
                result=a.review(TARGET,lambda _:None,api_factory=lambda _:api)
            self.assertEqual(calls.count(CONFIG.fast_nodes),100)
            self.assertEqual(calls.count(CONFIG.historical_probe_nodes),0)
            self.assertLessEqual(calls.count(CONFIG.deep_nodes),CONFIG.deep_max_games)
            self.assertEqual(result.coverage['history_fast_scanned'],0)
            self.assertEqual(result.coverage['collected'],200)
            self.assertEqual(result.coverage['used'],100)
            self.assertEqual(result.priority,'LOW')
        finally:a._game_cache.clear()

    def test_probes_are_bounded_and_deterministic(self):
        g=game(1)
        self.assertEqual(len(history.probe_decisions(g)),4)
        self.assertEqual(history.probe_decisions(g),history.probe_decisions(copy.deepcopy(g)))

    def test_sessions_split_at_configured_inactivity_gap(self):
        games=[game(i) for i in range(5)]
        games[-1].ended+=86400
        sessions=history.session_history(games)
        self.assertEqual([s['games'] for s in sessions],[4,1])

    def test_eligibility_counts_do_not_consume_slots(self):
        rows=[sample_row(i) for i in range(1,111)]
        for index,rules in enumerate(('bughouse','chess960','duck')):rows.append({**sample_row(900+index),'rules':rules})
        rows.append({**sample_row(910),'time_class':'daily'})
        rows.append({**sample_row(911),'pgn':'malformed'})
        api=SimpleNamespace(deadline=time.monotonic()+60,get=Mock(side_effect=[{'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']},{'games':rows}]))
        found,skipped,partial=data.collect_games(api,TARGET,lambda _:None,replace(CONFIG,history_games=100))
        self.assertEqual(len(found),100);self.assertEqual(skipped['variant'],3)
        self.assertEqual(skipped['daily'],1);self.assertEqual(skipped['invalid_pgn'],1)
        self.assertFalse(partial)

    def test_configurable_collection_limit_and_primary_limit(self):
        self.assertEqual(data.collection_limit(CONFIG),200);self.assertEqual(data.primary_limit(CONFIG),100)
        self.assertEqual(data.collection_limit(replace(CONFIG,history_games=250)),250)
        self.assertEqual(data.collection_limit(replace(CONFIG,max_games=2)),2)
        self.assertEqual(data.collection_limit(replace(CONFIG,history_games=900)),500)

    def test_primary_fast_completes_before_any_history_or_deep(self):
        games=[game(i,deep=False) for i in range(12)]
        calls=[]
        class Scanner:
            name='Synthetic'
            def __init__(self,*args):pass
            def analyse(self,g,nodes):
                calls.append((g.identity,nodes))
                if nodes==CONFIG.fast_nodes:g.fast_metrics=copy.deepcopy(g.metrics)
            def close(self):pass
        api=Mock();api.get.return_value={'username':TARGET}
        config=replace(CONFIG,primary_engine_games=3,deep_games=2)
        with patch.object(a,'collect_games',return_value=(games,{},False)),patch.object(a,'EngineScanner',Scanner):
            result=a.review(TARGET,lambda _:None,config,api_factory=lambda _:api)
        self.assertEqual(calls[:3],[(g.identity,CONFIG.fast_nodes) for g in reversed(games[-3:])])
        self.assertEqual(result.coverage['primary_fast_scanned'],3)
        self.assertEqual(result.coverage['history_probed'],0)
        self.assertEqual(result.coverage['history_fast_scanned'],0)


class StructuralEvidence(unittest.TestCase):
    def decision(self):return next(d for d in data.parse_game(sample_row(),TARGET).decisions if d.useful and d.legal>=6)
    def lines(self,d):
        board=chess.Board(d.fen);move=chess.Move.from_uci(d.move)
        moves=[move]+[m for m in board.legal_moves if m!=move][:2]
        return [{'pv':[m],'score':chess.engine.PovScore(chess.engine.Cp(v),True)} for m,v in zip(moves,(250,100,0))]

    def test_tactical_capture_and_check_can_be_critical(self):
        for attribute in ('capture','gives_check','check'):
            d=self.decision();setattr(d,attribute,True)
            metrics=a.engine_metrics(d,self.lines(d),{},True)
            self.assertTrue(metrics['critical']);self.assertEqual(metrics['critical_kind'],'tactical')

    def test_only_legal_move_zero_engine_weight(self):
        d=self.decision();d.legal=1;d.forced=True;d.useful=False
        metrics=a.engine_metrics(d,self.lines(d),{},True)
        self.assertFalse(metrics['useful']);self.assertEqual(metrics['weight'],0)

    def test_parse_retains_nontrivial_check_and_recapture(self):
        parsed=data.parse_game(sample_row(color=False),TARGET)
        checks=[d for d in parsed.decisions if d.check and d.legal>2 and d.phase!='opening' and not d.trivial_kind]
        self.assertTrue(checks)
        self.assertTrue(all(d.useful and not d.forced for d in checks))

    def test_equivalent_candidates_have_little_weight(self):
        d=self.decision();lines=self.lines(d)
        for line in lines:line['score']=chess.engine.PovScore(chess.engine.Cp(250),True)
        metrics=a.engine_metrics(d,lines,{},True)
        self.assertFalse(metrics['critical']);self.assertLessEqual(metrics['weight'],.25)


class AdditionalSafety(unittest.TestCase):
    def test_within_game_transition_retains_quality_and_timing_segments(self):
        g=game(1)
        moves=[d for d in g.decisions if d.metrics.get('useful')]
        for d in moves[len(moves)//2:]:
            d.metrics.update(top1=True,cpl=5);d.think=5;d.clock_valid=d.clock_reliable=True
        a.summarize(g)
        timing=g.metrics['timing']
        self.assertTrue(timing['regime_shift'])
        self.assertGreater(timing['segments']['earlier']['median_cpl'],40)
        self.assertLess(timing['segments']['later']['median_cpl'],15)
        self.assertGreater(timing['segments']['later']['top1'],.9)

    def test_targeted_history_does_not_bridge_unscanned_games(self):
        games=[game(i,True) for i in range(12)]
        for i,g in enumerate(games):g.control_index=i*10
        self.assertIsNone(clusters.find_clusters(games)['strongest'])
        self.assertFalse(clusters.regime_changes(games))

    def test_one_long_plateau_is_not_multiple_independent_periods(self):
        found=clusters.find_clusters([game(i,20<=i<50) for i in range(80)])
        self.assertFalse(found['recurrence'])

    def test_nontrivial_recapture_with_alternatives_retains_engine_evidence(self):
        from fairplay_timing import trivial_move_kind
        board=chess.Board('4k3/8/8/4p3/3r4/2P1PN2/8/4K3 w - - 0 1')
        move=chess.Move.from_uci('c3d4')
        self.assertIn(move,board.legal_moves)
        self.assertIsNone(trivial_move_kind(board,move,board.legal_moves.count(),recapture=True))

    def test_obvious_recapture_keeps_timing_but_zero_engine_evidence(self):
        from fairplay_timing import trivial_move_kind
        board=chess.Board('4k3/8/8/8/3p4/2P5/8/4K3 w - - 0 1')
        move=chess.Move.from_uci('c3d4')
        kind=trivial_move_kind(board,move,board.legal_moves.count(),recapture=True)
        self.assertEqual(kind,'obvious recapture')
        d=copy.deepcopy(game(1).decisions[20]);d.trivial_kind=kind;d.think=5;d.clock_valid=True
        lines=[{'pv':[move],'score':chess.engine.PovScore(chess.engine.Cp(150),True)}]
        d.move=move.uci()
        m=a.engine_metrics(d,lines,{},True)
        self.assertFalse(m['useful']);self.assertEqual(m['weight'],0)
        self.assertTrue(d.clock_valid);self.assertEqual(d.think,5)

    def test_only_legal_move_is_excluded_even_with_inconsistent_caller_flag(self):
        d=StructuralEvidence().decision();d.legal=1;d.useful=True
        m=a.engine_metrics(d,StructuralEvidence().lines(d),{},True)
        self.assertFalse(m['useful']);self.assertEqual(m['weight'],0)

    def test_missing_archive_gap_stays_partial_even_when_target_reached(self):
        api=SimpleNamespace(deadline=time.monotonic()+60,get=Mock(side_effect=[
            {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10',f'https://api.chess.com/pub/player/{TARGET}/games/2026/09']},
            None,{'games':[sample_row()]}]))
        found,skipped,partial=data.collect_games(api,TARGET,lambda _:None,replace(CONFIG,max_games=1))
        self.assertEqual(len(found),1);self.assertTrue(partial);self.assertEqual(skipped['unavailable_archive'],1)

    def test_win_loss_contrast_cannot_be_created_by_different_search_budgets(self):
        games=[game(i) for i in range(16)]
        for i,g in enumerate(games):
            g.result='Win' if i<8 else 'Loss'
            if i<8:g.metrics['robust_cpl']=5  # deep result, same fast baseline remains 80
        self.assertFalse(a.performance_metrics(games)['contrasts'])

    def test_exact_increment_performance_comparison(self):
        games=[game(i,i>=10,control='600+0' if i<10 else '600+5') for i in range(20)]
        self.assertFalse(a.performance_metrics(games)['shifts'])

    def test_phase_and_clock_profile_survives_without_historical_engine_metrics(self):
        from fairplay_baseline import timing_profile
        g=game(1)
        for d in g.decisions:d.metrics={}
        p=timing_profile([g])
        self.assertGreater(p['categories']['ordinary']['count'],0)
        self.assertEqual(p['categories']['critical']['count'],0)
        self.assertGreater(p['categories']['opening']['count'],0)

    def test_tiny_sample_never_misleading_low(self):
        result=report([game(i,True) for i in range(4)])
        self.assertEqual(result.priority,'INSUFFICIENT DATA');self.assertEqual(result.confidence,'LOW')

    def test_deep_strength_not_shallow_strength_controls_very_high(self):
        games=[game(i,i>=30) for i in range(40)]
        for g in games[30:]:
            for d in g.decisions:
                if d.metrics.get('critical'):d.metrics['top1']=False
            a.summarize(g)
        result=report(games)
        self.assertNotEqual(result.priority,'VERY HIGH')


class ValidationPrivacy(unittest.TestCase):
    def test_local_validation_inputs_are_confined_to_ignored_paths(self):
        from pathlib import Path
        root=Path(validation.__file__).parent
        with self.assertRaises(ValueError):validation.private_input_path(root/'public_cases.json')
        self.assertEqual(validation.private_input_path(root/'validation_cases.local.json'),root/'validation_cases.local.json')
        self.assertEqual(validation.private_input_path(root/'fairplay_validation.local'),root/'fairplay_validation.local')
        self.assertEqual(validation.private_input_path('/tmp/private-validation.json'),Path('/tmp/private-validation.json'))

    def test_labels_are_not_passed_to_analyzer(self):
        analyze=Mock(return_value=SimpleNamespace(priority='LOW'))
        cases=[{'username':TARGET,'label':label} for label in validation.LABELS]
        summary=validation.evaluate_cases(cases,analyze)
        self.assertEqual(analyze.call_args_list,[unittest.mock.call(TARGET) for _ in validation.LABELS])
        self.assertNotIn(TARGET,json.dumps(summary));self.assertEqual(summary['high_or_higher_recall'],0)

    def test_only_explicit_external_labels_accepted(self):
        with self.assertRaises(ValueError):validation.evaluate_cases([{'username':TARGET,'label':'not_banned'}],Mock())

    def test_failed_scans_not_fabricated_as_low(self):
        summary=validation.evaluate_cases([{'username':TARGET,'label':'known_fair_play_closed'}],Mock(side_effect=RuntimeError('private details')))
        self.assertEqual(summary['failed']['known_fair_play_closed'],1)
        self.assertIsNone(summary['high_or_higher_recall'])
        self.assertNotIn('private details',json.dumps(summary))


if __name__=='__main__':unittest.main()
