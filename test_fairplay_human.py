"""Synthetic gameplay, difficulty, sample and book regressions; no case labels."""
import copy
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import chess
from fairplay_config import CONFIG
from fairplay_human import annotate_game, period_summary, absolute_qualified
from fairplay_sequence import (class_periods, deep_confirmation, integrate_gameplay, game_structure,
    adaptive_deep_games, coverage_members, sparse_review_blockers, sparse_deep_blockers)
from fairplay_opening import book_status, opening_reference, repertoire
from test_fairplay_v4 import game
from test_fairplay import sample_row, TARGET
import fairplay_analysis as analysis
import fairplay_data as data


def informative(index,rating=1000,rank=1,deep=True,control='180+0',misses=0):
    g=game(index,True,rating=rating,deep=deep,control=control,clocks=False)
    g.time_class='blitz'
    for i,d in enumerate(g.decisions):
        d.phase='middlegame';d.useful=True;d.forced=False;d.trivial_kind=None
        d.legal=30;d.capture=d.check=d.gives_check=False
        m={'useful':True,'before_cp':0,'actual_cp':0,'competitive':True,'critical':True,'unique':True,
           'rank':rank,'candidate_count':5,'candidate_cp':([0,-200,-450,-600,-800] if rank==1 else
               [0,-5,-450,-600,-800] if rank==2 else [0,-5,-10,-450,-800]),
           'cpl':0 if i>=misses else 200,'scaled_loss':0 if i>=misses else .2,
           'gap':200 if rank==1 else 5,'spread':800,'best':'synthetic-best','near_best':i>=misses,
           'nodes':CONFIG.fast_nodes,'weight':1,'top1':rank==1 and i>=misses,'top3':i>=misses,'critical_kind':'quiet'}
        d.metrics=copy.deepcopy(m)
    annotate_game(g);g.fast_metrics={'decisions':len(g.decisions),'human':copy.deepcopy(g.metrics['human'])}
    for d in g.decisions:
        d.fast_engine=copy.deepcopy(d.metrics)
        if deep:d.metrics['nodes']=CONFIG.deep_nodes
    annotate_game(g)
    return g


def result_stub(games):
    return SimpleNamespace(games=games,totals={'decisions':sum(len(g.decisions) for g in games)},
        partial=False,confidence='HIGH',priority='LOW',deep_confirmed=False,diagnostics={},families={},reasons=[])


class HumanEvidence(unittest.TestCase):
    def test_overwhelming_gameplay_can_high_with_no_clocks_or_personal_shift(self):
        games=[informative(i) for i in range(20)]
        result=integrate_gameplay(result_stub(games),games)
        self.assertEqual(result.priority,'HIGH');self.assertTrue(result.deep_confirmed)

    def test_whole_ten_game_sample_with_distributed_opportunities_can_high(self):
        games=[informative(i) for i in range(10)]
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'HIGH')

    def test_stable_elite_precision_is_not_absolute_human_anomaly(self):
        games=[informative(i,rating=2850) for i in range(40)]
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'LOW')

    def test_ceiling_residual_alone_cannot_establish_absolute_high(self):
        games=[informative(i,rating=2300) for i in range(20)]
        summary=period_summary(games)
        self.assertTrue(absolute_qualified(summary))
        compressed=dict(summary)
        compressed.update(quality_reference=.90,quality_excess=.10,anomaly_strength=.50)
        self.assertFalse(absolute_qualified(compressed))

    def test_deep_confirmation_rechecks_ceiling_raw_excess(self):
        games=[informative(i,rating=1000) for i in range(20)]
        period=next(p for p in class_periods(games) if p.get('absolute'))
        for g in games:
            g.metrics['human']['quality_reference']=.90
            g.metrics['human']['quality_excess']=.10
            g.metrics['human']['anomaly_strength']=.50
        proof=deep_confirmation(period,games)
        self.assertFalse(proof['absolute'])
        self.assertIn('deep raw quality excess beyond ceiling guard',proof['blockers'])

    def test_opportunities_do_not_disappear_above_2050_rating(self):
        for rating in (2100,2300,2850):
            g=informative(0,rating=rating)
            self.assertGreater(g.metrics['human']['opportunities'],0)
        games=[informative(i,rating=2300) for i in range(20)]
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'HIGH')

    def test_deep_personal_gameplay_change_can_high_without_clocks(self):
        games=[informative(i,rating=2850,misses=25 if i<30 else 0) for i in range(40)]
        r=integrate_gameplay(result_stub(games),games)
        self.assertEqual(r.priority,'HIGH')
        self.assertIn('personal',r.diagnostics['high_path'])

    def test_disjoint_confirmed_periods_need_lower_anomaly_games_between(self):
        games=[informative(i,misses=45 if 10<=i<20 else 0) for i in range(30)]
        r=integrate_gameplay(result_stub(games),games)
        self.assertTrue(r.diagnostics['gameplay']['replicated_disjoint_periods'])
        self.assertEqual(r.priority,'VERY HIGH')
        continuous=[informative(i) for i in range(30)]
        r=integrate_gameplay(result_stub(continuous),continuous)
        self.assertFalse(r.diagnostics['gameplay']['replicated_disjoint_periods'])
        self.assertEqual(r.priority,'HIGH')

    def test_research_ablation_can_disable_new_gameplay_without_changing_inputs(self):
        games=[informative(i) for i in range(20)]
        config=replace(CONFIG,disabled_features=('human',))
        self.assertEqual(integrate_gameplay(result_stub(games),games,config).priority,'LOW')

    def test_second_rank_equivalent_best_set_does_not_evade_gameplay_layer(self):
        for rank in (2,3):
            games=[informative(i,rank=rank) for i in range(20)]
            self.assertTrue(class_periods(games)[0]['qualified'])
            self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'HIGH')

    def test_one_huge_perfect_game_cannot_dominate(self):
        g=informative(0);g.decisions*=8;annotate_game(g)
        self.assertEqual(g.metrics['human']['opportunities'],CONFIG.human_game_cap)
        games=[g]+[informative(i,rating=2850) for i in range(1,20)]
        self.assertFalse(absolute_qualified(period_summary(games)))

    def test_no_opportunity_games_do_not_dilute_information_or_create_hits(self):
        games=[informative(i) for i in range(10)]
        informative_summary=period_summary(games)
        filler=[informative(i) for i in range(10,30)]
        for g in filler:
            for d in g.decisions:d.metrics['easy_conversion']=True
            annotate_game(g)
        combined=period_summary(games+filler)
        self.assertEqual(combined['information'],informative_summary['information'])
        self.assertEqual(combined['hits'],informative_summary['hits'])
        self.assertEqual(combined['contributors'],10)
        self.assertEqual(combined['opportunity_games'],10)
        self.assertTrue(absolute_qualified(combined))

    def test_three_exceptional_games_after_seven_weak_can_acute_high(self):
        games=[informative(i,misses=45 if i<7 else 0) for i in range(10)]
        result=integrate_gameplay(result_stub(games),games)
        self.assertEqual(result.priority,'HIGH')
        self.assertIn('Acute',result.diagnostics['high_path'])

    def test_deep_selection_covers_opportunities_not_only_uniform_easy_games(self):
        games=[informative(i) for i in range(50)]
        for i,g in enumerate(games):
            if i%3:
                for d in g.decisions:d.metrics['easy_conversion']=True
                annotate_game(g);g.fast_metrics['human']=copy.deepcopy(g.metrics['human'])
        chosen=coverage_members(games,11)
        self.assertIn(games[0],chosen);self.assertIn(games[-1],chosen)
        self.assertGreaterEqual(sum(g.fast_metrics['human']['opportunities'] for g in chosen),20)
        # Selection uses opportunity geometry, not whether those moves succeed.
        before=[g.identity for g in chosen]
        for g in games:g.fast_metrics['human']['hits']=0
        self.assertEqual(before,[g.identity for g in coverage_members(games,11)])

    def test_quiet_unique_move_example_has_high_evidential_difficulty(self):
        g=informative(0,rating=1800)
        for d in g.decisions:
            d.legal=22
            d.metrics.update(candidate_cp=[65,-30,-75,-120],candidate_count=4,
                             before_cp=65,actual_cp=65,gap=95,spread=185)
        annotate_game(g)
        self.assertGreater(g.decisions[0].metrics['difficulty'],CONFIG.human_difficulty_floor)
        self.assertTrue(g.decisions[0].metrics['high_information'])
        # Nearly equivalent alternatives remain uninformative.
        for d in g.decisions:d.metrics.update(candidate_cp=[121,119,117,114],gap=2,spread=7)
        annotate_game(g)
        self.assertFalse(g.decisions[0].metrics['high_information'])

    def test_obvious_quiet_piece_escape_is_deweighted(self):
        g=informative(0)
        for d in g.decisions:
            d.fen='6k1/8/8/8/8/8/3rQ3/6K1 w - - 0 1'
            d.move='e2e3'
        annotate_game(g)
        self.assertTrue(g.decisions[0].metrics['simple_threat_response'])
        self.assertFalse(g.decisions[0].metrics['high_information'])

    def test_extreme_distributed_bullet_gameplay_is_not_categorically_blocked(self):
        games=[informative(i) for i in range(60)]
        for g in games:g.time_class='bullet'
        result=integrate_gameplay(result_stub(games),games)
        self.assertEqual(result.priority,'HIGH')
        self.assertTrue(result.deep_confirmed)

    def test_contiguous_cluster_is_not_hidden_by_arbitrary_block_edges(self):
        games=[informative(i,misses=0 if 7<=i<17 else 45) for i in range(30)]
        periods=class_periods(games)
        expected=[g.identity for g in games[7:17]]
        aligned=[p for p in periods if p['ids']==expected]
        self.assertEqual(len(aligned),1)
        self.assertTrue(aligned[0]['qualified'])
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'HIGH')

    def test_misses_stay_in_capped_denominator(self):
        g=informative(0,misses=20);s=g.metrics['human']
        self.assertEqual(s['opportunities'],CONFIG.human_game_cap)
        self.assertLess(s['hits'],s['opportunities'])

    def test_one_perfect_game_never_high(self):
        games=[informative(0)]+[informative(i,rating=2850) for i in range(1,20)]
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'LOW')

    def test_three_exceptional_games_are_detected_outside_latest_ten(self):
        games=[informative(i,misses=45) for i in range(100)]
        for i in (30,31,32):games[i]=informative(i,rating=1000)
        result=integrate_gameplay(result_stub(games),games)
        self.assertEqual(result.priority,'HIGH')
        self.assertIn('Acute',result.diagnostics['high_path'])

    def test_easy_conversion_and_opponent_error_offer_no_human_evidence(self):
        g=informative(0)
        for d in g.decisions:d.metrics['easy_conversion']=True
        annotate_game(g);self.assertEqual(g.metrics['human']['opportunities'],0)
        for d in g.decisions:d.metrics['easy_conversion']=False;d.metrics['post_opponent_error']=True
        annotate_game(g);self.assertEqual(g.metrics['human']['opportunities'],0)

    def test_many_equivalent_moves_reduce_information(self):
        g=informative(0)
        for d in g.decisions:d.metrics['candidate_cp']=[0,-1,-2,-3,-4]
        annotate_game(g);self.assertEqual(g.metrics['human']['opportunities'],0)

    def test_deep_rank_instability_blocks_confirmation(self):
        games=[informative(i) for i in range(20)];period=class_periods(games)[0]
        for g in games:
            for d in g.decisions:d.metrics.update(rank=4,best='different',cpl=150,scaled_loss=.2,near_best=False)
            annotate_game(g)
        self.assertFalse(deep_confirmation(period,games)['qualified'])

    def test_missing_deep_and_partial_scans_cannot_high(self):
        games=[informative(i,deep=False) for i in range(20)]
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'LOW')
        games=[informative(i) for i in range(20)];r=result_stub(games);r.partial=True
        self.assertEqual(integrate_gameplay(r,games).priority,'LOW')

    def test_class_period_combines_blitz_controls_without_clock_pooling(self):
        games=[informative(i,control=['180+0','180+2','300+0'][i%3]) for i in range(12)]
        p=class_periods(games)[0];self.assertEqual(len(p['controls']),3)
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'HIGH')

    def test_casual_duplicate_and_probe_games_cannot_inflate_period(self):
        games=[informative(i) for i in range(9)];extra=informative(10);extra.rated=False
        probe=informative(11);probe.probe_only=True
        periods=class_periods(games+games+[extra,probe])
        self.assertTrue(all(p['kind']=='acute_candidate' for p in periods))
        self.assertTrue(all(len(p['ids'])==len(set(p['ids'])) for p in periods))
        self.assertTrue(all(extra.identity not in p['ids'] and probe.identity not in p['ids'] for p in periods))

    def test_small_bullet_sample_does_not_supply_absolute_high(self):
        games=[informative(i) for i in range(20)]
        for g in games:g.time_class='bullet'
        self.assertEqual(integrate_gameplay(result_stub(games),games).priority,'LOW')

    def test_missing_rating_cannot_be_absolute_anomaly(self):
        games=[informative(i) for i in range(20)]
        for g in games:g.rating=None;annotate_game(g)
        self.assertFalse(absolute_qualified(period_summary(games)))

    def test_model_failure_gracefully_uses_deterministic_fallback(self):
        class Broken:
            name='unavailable'
            def expectedness(self,*args):raise RuntimeError('unavailable')
        g=informative(0);expected=copy.deepcopy(g.metrics['human']);actual=annotate_game(g,model=Broken())
        self.assertEqual(actual['hits'],expected['hits']);self.assertGreater(actual['model_failures'],0)

    def test_within_game_change_requires_two_adequate_segments(self):
        g=informative(0)
        for d in g.decisions[:15]:d.metrics['human_information']=0
        self.assertIsNotNone(game_structure(g)['change'])
        g.decisions=g.decisions[:10];self.assertIsNone(game_structure(g)['change'])

    def test_difficulty_inversion_is_support_not_independent_high(self):
        g=informative(0)
        for d in g.decisions[:10]:d.metrics.update(candidate_cp=[0,-1,-2],gap=1,spread=2,cpl=150,near_best=False)
        annotate_game(g);self.assertTrue(g.metrics['human']['difficulty_inversion'])
        self.assertEqual(integrate_gameplay(result_stub([g]),[g]).priority,'LOW')

    def test_period_summary_tracks_hit_bearing_distribution(self):
        games=[informative(i) for i in range(6)]
        for i,g in enumerate(games):
            row=g.metrics['human']
            row['hits']=0 if i==0 else 1 if i<4 else 2
            g.fast_metrics['human']=copy.deepcopy(row)
        summary=period_summary(games,fast=True)
        self.assertEqual(summary['hit_games'],5)
        self.assertEqual(summary['single_hit_games'],3)
        self.assertEqual(summary['contributors'],2)

    def test_distributed_sparse_evidence_is_moderate_only_after_deep_retention(self):
        games=[informative(i,rating=2300) for i in range(30)]
        summary=period_summary(games)
        summary.update(games=30,opportunities=40,hits=16,hit_games=12,single_hit_games=8,
                       opportunity_games=20,rating_coverage=1.0,information=.20,
                       quality_excess=.09,anomaly_strength=.25)
        period={'qualified':False,'acute':False,'absolute':False,'personal':False,
                'class':'blitz','kind':'class','ids':[g.identity for g in games],
                'start':games[0].ended,'end':games[-1].ended,'controls':['180+0'],
                'summary':summary,'blockers':['distributed contributor games'],'baseline_ids':[]}
        deep_summary=dict(summary)
        deep_summary.update(games=5,opportunities=15,hits=6,hit_games=4,single_hit_games=3,
                            stable_opportunities=12,stable_hits=5,information=.20,
                            quality_excess=.09,anomaly_strength=.25)
        proof={'qualified':False,'absolute':False,'personal':False,'games':5,
               'summary':deep_summary,'paired_fast':{'hits':8},'retention':.75,
               'stability_fraction':.8,'blockers':['deep contributor games']}
        self.assertEqual(sparse_review_blockers(period),[])
        self.assertEqual(sparse_deep_blockers(period,proof),[])
        with patch('fairplay_sequence.class_periods',return_value=[period]), \
             patch('fairplay_sequence.deep_confirmation',return_value=proof):
            result=integrate_gameplay(result_stub(games),games)
        self.assertEqual(result.priority,'MODERATE')
        self.assertTrue(result.diagnostics['gameplay']['distributed_moderate']['passed'])
        self.assertNotIn('HIGH',result.diagnostics.get('moderate_path',''))

    def test_short_concentrated_cluster_cannot_use_distributed_moderate_route(self):
        games=[informative(i,rating=2300) for i in range(10)]
        summary=period_summary(games)
        summary.update(games=10,opportunities=40,hits=20,hit_games=10,single_hit_games=8,
                       opportunity_games=10,rating_coverage=1.0,information=.30,
                       quality_excess=.12,anomaly_strength=.40)
        period={'class':'blitz','summary':summary}
        self.assertIn('broad chronological sample',sparse_review_blockers(period))

    def test_sparse_broad_candidate_gets_unbiased_deep_coverage_without_high_prequalification(self):
        games=[informative(i,rating=2300,deep=False) for i in range(30)]
        summary=period_summary(games,fast=True)
        summary.update(games=30,opportunities=40,hits=16,hit_games=12,single_hit_games=8,
                       opportunity_games=20,rating_coverage=1.0,information=.20,
                       quality_excess=.09,anomaly_strength=.25)
        candidate={'qualified':False,'acute':False,'absolute':False,'personal':False,
                   'class':'blitz','kind':'class','ids':[g.identity for g in games],
                   'summary':summary,'baseline_ids':[]}
        with patch('fairplay_sequence.class_periods',return_value=[candidate]), \
             patch('fairplay_clusters.representative_controls',return_value=[]), \
             patch('fairplay_clusters.select_deep_games',return_value=[]):
            chosen=adaptive_deep_games(games)
        self.assertEqual(len(chosen),CONFIG.deep_normal_games)
        self.assertTrue(all(g.identity in candidate['ids'] for g in chosen))

    def test_failed_high_report_prefers_broad_candidate_over_tiny_acute_window(self):
        games=[informative(i,rating=2300) for i in range(30)]
        broad_summary=period_summary(games)
        broad_summary.update(games=30,opportunities=20,hits=4,hit_games=4,single_hit_games=4,
                             opportunity_games=12,information=.08,quality_excess=.06,anomaly_strength=.12)
        acute_summary=dict(broad_summary)
        acute_summary.update(games=2,opportunities=5,hits=5,hit_games=1,single_hit_games=0,
                             opportunity_games=1,information=.40,quality_excess=.10,anomaly_strength=.60)
        broad={'qualified':False,'acute':False,'absolute':False,'personal':False,'class':'blitz',
               'kind':'class','ids':[g.identity for g in games],'start':games[0].ended,'end':games[-1].ended,
               'controls':['180+0'],'summary':broad_summary,'blockers':['distributed opportunity coverage'],'baseline_ids':[]}
        acute={'qualified':False,'acute':True,'absolute':False,'personal':False,'class':'blitz',
               'kind':'acute_candidate','ids':[g.identity for g in games[:2]],'start':games[0].ended,'end':games[1].ended,
               'controls':['180+0'],'summary':acute_summary,'blockers':['hard opportunity denominator'],'baseline_ids':[]}
        proof={'qualified':False,'absolute':False,'personal':False,'games':0,'summary':{},
               'paired_fast':{},'retention':0,'stability_fraction':0,'blockers':['deep contributor games']}
        with patch('fairplay_sequence.class_periods',return_value=[acute,broad]), \
             patch('fairplay_sequence.deep_confirmation',return_value=proof):
            result=integrate_gameplay(result_stub(games),games)
        self.assertEqual(result.diagnostics['gameplay']['best']['kind'],'class')

    def test_adaptive_selection_is_bounded_and_reproducible(self):
        games=[informative(i) for i in range(40)]
        # Real legacy summaries are intentionally not required for a qualified
        # whole-class candidate: it reserves a deterministic chronological plan.
        with patch('fairplay_clusters.select_deep_games',return_value=[]):
            first=adaptive_deep_games(games);second=adaptive_deep_games(copy.deepcopy(games))
        self.assertLessEqual(len(first),CONFIG.deep_max_games)
        self.assertEqual([g.identity for g in first],[g.identity for g in second])

    def test_adaptive_selection_reserves_human_anomaly_slots(self):
        games=[informative(i,deep=False) for i in range(30)]
        candidate={'qualified':True,'acute':False,'absolute':True,'personal':False,
                   'class':'blitz','ids':[g.identity for g in games[:10]]}
        controls=games[10:13]
        standout=games[20]
        standout.fast_metrics['human']['anomaly_strength']=.99
        standout.fast_metrics['human']['information']=.99
        standout.fast_metrics['human']['difficulty_inversion_strength']=.8
        with patch('fairplay_sequence.class_periods',return_value=[candidate]), \
             patch('fairplay_clusters.representative_controls',return_value=controls), \
             patch('fairplay_clusters.select_deep_games',return_value=[]):
            chosen=adaptive_deep_games(games)
        self.assertEqual(len(chosen),CONFIG.deep_normal_games)
        self.assertIn(standout,chosen)
        self.assertGreaterEqual(sum(g.identity in candidate['ids'] for g in chosen),CONFIG.human_deep_games)


class OpeningAndCoverage(unittest.TestCase):
    def test_reference_is_offline_and_theory_protected_past_twenty_plies(self):
        self.assertGreater(len(opening_reference()),1000)
        import json
        from pathlib import Path
        lines=json.loads(Path('assets/fairplay/opening-lines.json').read_text())['lines']
        longest=max(lines,key=lambda x:len(x.split()));board=chess.Board()
        for ply,text in enumerate(longest.split(),1):
            move=chess.Move.from_uci(text);self.assertTrue(book_status(board,move,ply)['book']);board.push(move)
        self.assertGreater(len(longest.split()),20)

    def test_known_twenty_five_move_line_remains_protected(self):
        parsed=data.parse_game(sample_row(),TARGET)
        reference={}
        # A synthetic supplied reference proves there is no fixed 20-ply cap;
        # the curated production reference is explicitly not exhaustive theory.
        for d in parsed.decisions:reference.setdefault(' '.join(d.fen.split()[:4]),set()).add(d.move)
        with patch('fairplay_opening.opening_reference',return_value=reference):
            for d in parsed.decisions:
                if d.ply<=50:self.assertTrue(book_status(chess.Board(d.fen),chess.Move.from_uci(d.move),d.ply)['book'])

    def test_early_off_book_moves_are_no_longer_blindly_excluded(self):
        board=chess.Board();sequence='a2a3 a7a6 h2h3 h7h6 b2b3 b7b6 g2g3 g7g6 c2c3 c7c6 f2f3 f7f6'.split()
        for ply,text in enumerate(sequence,1):
            move=chess.Move.from_uci(text)
            if ply>6:self.assertFalse(book_status(board,move,ply)['book'])
            board.push(move)

    def test_repertoire_novelty_is_context_only(self):
        games=[informative(i) for i in range(20)];p=repertoire(games)
        self.assertTrue(p['White']['sufficient'] or p['Black']['sufficient'])
        self.assertIn('off_book_moves',p['White'])

    def test_newest_200_context_games_with_latest_100_engine_quota(self):
        self.assertEqual(data.collection_limit(),200);self.assertEqual(data.primary_limit(),100)
        rows=[sample_row(i) for i in range(240)]
        for i in range(20):rows.append({**sample_row(300+i),'rules':'chess960'})
        for i in range(20):rows.append({**sample_row(400+i),'rated':False})
        import time
        class API:
            deadline=time.monotonic()+120
            def get(self,name,suffix):return {'archives':[f'https://api.chess.com/pub/player/{name}/games/2026/10']} if suffix.endswith('archives') else {'games':rows}
        found,skipped,partial=data.collect_games(API(),TARGET,lambda _:None)
        self.assertEqual(len(found),200);self.assertEqual(found[0].identity,'synthetic-40')
        self.assertEqual(found[-1].identity,'synthetic-239');self.assertEqual(skipped['unrated'],20);self.assertEqual(skipped['variant'],20)
        self.assertFalse(partial)


if __name__=='__main__':unittest.main()
