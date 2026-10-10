"""Synthetic opposition and decision-difficulty controls; no real account labels."""
import copy
import unittest
from dataclasses import replace
import fairplay_analysis as a
import fairplay_calibration as calibration
import fairplay_clusters as clusters
from fairplay_positions import opponent_context, position_context
from fairplay_config import CONFIG
from test_fairplay_v6 import played
from test_fairplay_v4 import report


def annotated(index,high=False):
    g=played(index,high)
    for d in g.decisions:
        d.metrics.update(before_cp=80,actual_cp=80-d.metrics['cpl'],nodes=CONFIG.fast_nodes)
    a.summarize(g);g.fast_metrics=copy.deepcopy(g.metrics)
    return g


class Opposition(unittest.TestCase):
    def test_baseline_matches_opponents_player_and_gap(self):
        baseline=[played(i) for i in range(30)]
        for g in baseline[:10]:g.opponent_rating=1400
        for g in baseline[10:20]:g.opponent_rating=1800
        for g in baseline[20:]:g.rating=2300;g.opponent_rating=1900
        block=[played(i,True) for i in range(30,40)]
        for g in block:g.opponent_rating=1400
        row=clusters.group_record(block,'chronological')
        matched=calibration.comparable_baseline(row,baseline+block)
        self.assertEqual([g.identity for g in matched],[g.identity for g in baseline[:10]])
        comparison=calibration.baseline_comparison(row,baseline+block)
        self.assertTrue(comparison['sufficient'])
        self.assertEqual(comparison['opponent_context']['baseline']['elo_difference'],400)

    def test_insufficient_matching_opposition_never_silently_fills(self):
        games=[played(i,i>=30) for i in range(40)]
        for g in games[:30]:g.opponent_rating=2200
        for g in games[30:]:g.opponent_rating=1200
        row=clusters.group_record(games[30:],'chronological')
        comparison=calibration.baseline_comparison(row,games)
        self.assertFalse(comparison['sufficient']);self.assertFalse(comparison['established'])
        self.assertEqual(comparison['baseline']['games'],0)

    def test_unknown_opponent_cannot_establish_rating_matched_baseline(self):
        games=[played(i,i>=20) for i in range(30)]
        for g in games[:20]:g.opponent_rating=None
        row=clusters.group_record(games[20:],'chronological')
        self.assertFalse(calibration.baseline_comparison(row,games)['sufficient'])

    def test_expected_clean_wins_differ_from_upsets(self):
        games=[played(i) for i in range(20)]
        for g in games:g.rating=1800;g.opponent_rating=1300;g.score=.9
        easy=a.context_metrics(games,{})['classes']['blitz']['excess_z']
        for g in games:g.opponent_rating=1950
        upset=a.context_metrics(games,{})['classes']['blitz']['excess_z']
        self.assertLess(easy,0);self.assertGreater(upset,4)

    def test_opponent_rating_does_not_directly_reduce_engine_precision(self):
        games=[annotated(i,True) for i in range(10)]
        before=clusters.evidence(clusters.summary(games),1800)
        for g in games:g.opponent_rating=900
        self.assertEqual(before,clusters.evidence(clusters.summary(games),1800))


class Positions(unittest.TestCase):
    def capture(self):
        d=copy.deepcopy(next(d for d in played(0).decisions if d.useful))
        d.fen='6k1/8/8/8/8/8/q7/R5K1 w - - 0 15';d.move='a1a2';d.capture=True
        d.metrics.update(before_cp=450,actual_cp=450,weight=1,top1=True,cpl=0,
                         critical=True,unique=True,gap=300,nodes=CONFIG.fast_nodes)
        return d

    def test_hanging_queen_is_near_zero_engine_evidence(self):
        g=played(0);g.decisions=[self.capture()];a.summarize(g)
        self.assertEqual(g.metrics['decisions'],0);self.assertEqual(g.metrics['critical'],0)
        self.assertEqual(g.metrics['easy_conversion_decisions'],1)
        self.assertEqual(g.metrics['competitive_decisions'],0)

    def test_obvious_material_gain_retains_trivial_timing_and_premoves(self):
        from fairplay_baseline import timing_values
        from fairplay_timing import trivial_delay_metrics
        for think in (.1,5):
            g=played(0);d=self.capture();d.think=think;d.clock_valid=True
            d.phase='middlegame';d.trivial_kind=None;g.decisions=[d];a.summarize(g)
            self.assertEqual(timing_values(g)['trivial'],[think])
            timing=trivial_delay_metrics(g.decisions)
            self.assertEqual(timing['samples']['trivial']['count'],1)
            self.assertEqual(timing['samples']['trivial']['near_instant'],int(think==.1))

    def test_adjacent_opponent_error_uses_same_budget_player_pov(self):
        g=played(0);first,second=copy.deepcopy(g.decisions[:2])
        first.ply=21;second.ply=23
        first.metrics.update(before_cp=0,actual_cp=0,nodes=CONFIG.fast_nodes)
        second.metrics.update(before_cp=450,actual_cp=400,nodes=CONFIG.fast_nodes)
        g.decisions=[first,second];position_context(g)
        self.assertEqual(second.metrics['opponent_swing_cp'],450)
        self.assertTrue(second.metrics['equal_to_winning'])
        self.assertFalse(second.metrics['competitive'])

    def test_verified_depth_contract_detects_opponent_error_despite_different_nodes(self):
        g=played(0)
        first,second=copy.deepcopy(g.decisions[:2])
        first.ply=21;second.ply=23
        contract={'mode':'depth','requested':18,'completed':True,'exact':True,
                  'engine':'Stockfish 19','multipv':3}
        first.metrics.update(before_cp=0,actual_cp=0,nodes=111_111,
                             search_depth=18,search_contract=dict(contract))
        second.metrics.update(before_cp=450,actual_cp=420,nodes=983_233,
                              search_depth=18,search_contract=dict(contract))
        g.decisions=[first,second]
        position_context(g)
        self.assertEqual(second.metrics['opponent_swing_cp'],450)
        self.assertTrue(second.metrics['post_opponent_error'])
        self.assertFalse(second.metrics['competitive'])
        again=copy.deepcopy(second.metrics)
        position_context(g)
        self.assertEqual(again,second.metrics)

    def test_depth12_bullet_is_a_verified_same_budget(self):
        g=played(0)
        first,second=copy.deepcopy(g.decisions[:2])
        first.ply=21;second.ply=23
        contract={'mode':'depth','requested':12,'completed':True,'exact':True,
                  'engine':'Stockfish 19','multipv':3}
        first.metrics.update(before_cp=0,actual_cp=0,nodes=121_000,
                             search_depth=12,search_contract=dict(contract))
        second.metrics.update(before_cp=400,actual_cp=300,nodes=789_000,
                              search_depth=12,search_contract=dict(contract))
        g.decisions=[first,second]
        position_context(g)
        self.assertTrue(second.metrics['post_opponent_error'])

    def test_incomplete_mismatched_or_unreached_depth_never_invents_opponent_error(self):
        contract={'mode':'depth','requested':18,'completed':True,'exact':True,
                  'engine':'Stockfish 19','multipv':3}
        for field,value in [('completed',False),('exact',False),
                            ('requested',12),('engine','other'),('multipv',1)]:
            with self.subTest(field=field):
                g=played(0)
                first,second=copy.deepcopy(g.decisions[:2])
                first.ply=21;second.ply=23
                first.metrics.update(before_cp=0,actual_cp=0,nodes=111,
                                     search_depth=18,search_contract=dict(contract))
                altered=dict(contract);altered[field]=value
                second.metrics.update(before_cp=450,actual_cp=400,nodes=222,
                                      search_depth=18,search_contract=altered)
                g.decisions=[first,second];position_context(g)
                self.assertIsNone(second.metrics['opponent_swing_cp'])
                self.assertFalse(second.metrics['post_opponent_error'])
        g=played(0)
        first,second=copy.deepcopy(g.decisions[:2])
        first.ply=21;second.ply=23
        first.metrics.update(before_cp=0,actual_cp=0,nodes=111,
                             search_depth=18,search_contract=dict(contract))
        second.metrics.update(before_cp=450,actual_cp=400,nodes=222,
                              search_depth=16,search_contract=dict(contract))
        g.decisions=[first,second];position_context(g)
        self.assertIsNone(second.metrics['opponent_swing_cp'])

    def test_gaps_missing_and_mate_scores_do_not_manufacture_opponent_errors(self):
        for nodes,ply,actual in ((CONFIG.deep_nodes,23,0),(CONFIG.fast_nodes,25,0),(CONFIG.fast_nodes,23,-10000)):
            g=played(0);first,second=copy.deepcopy(g.decisions[:2]);first.ply=21;second.ply=ply
            first.metrics.update(actual_cp=actual,before_cp=0,nodes=CONFIG.fast_nodes)
            second.metrics.update(before_cp=450,actual_cp=400,nodes=nodes)
            g.decisions=[first,second];position_context(g)
            self.assertIsNone(second.metrics['opponent_swing_cp']);self.assertFalse(second.metrics['post_opponent_error'])

    def test_annotation_idempotent_and_deep_replaces_shallow_context(self):
        g=played(0);d=self.capture();g.decisions=[d]
        a.summarize(g);before=copy.deepcopy(d.metrics);a.summarize(g)
        self.assertEqual(before,d.metrics)
        d.metrics.update(before_cp=80,actual_cp=80)
        a.summarize(g)
        self.assertTrue(d.metrics['useful']);self.assertTrue(d.metrics['competitive'])

    def test_difficult_quiet_winning_moves_not_discarded(self):
        g=annotated(0,True)
        for d in g.decisions:
            if d.useful:d.capture=False;d.metrics.update(before_cp=450,actual_cp=450)
        a.summarize(g)
        self.assertGreater(g.metrics['decisions'],20);self.assertGreater(g.metrics['critical'],5)
        self.assertEqual(g.metrics['easy_conversion_decisions'],0)

    def test_easy_conversion_cluster_does_not_create_high(self):
        games=[annotated(i) for i in range(40)]
        for g in games[30:]:
            g.opponent_rating=1000
            for i,d in enumerate(g.decisions):
                if d.useful:
                    capture=self.capture();capture.ply=21+2*i
                    d.fen=capture.fen;d.move=capture.move;d.capture=True;d.metrics=capture.metrics
            a.summarize(g);g.fast_metrics=copy.deepcopy(g.metrics)
        self.assertNotIn(report(games).priority,('HIGH','VERY HIGH'))

    def test_competitive_critical_cluster_with_independent_timing_can_high(self):
        games=[annotated(i,i>=30) for i in range(40)]
        result=report(games)
        self.assertIn(result.priority,('HIGH','VERY HIGH'))
        self.assertGreater(result.clusters['strongest']['metrics']['competitive_decisions'],160)
        self.assertGreater(result.clusters['strongest']['metrics']['competitive_critical_top1'],.95)

    def test_difficult_decisions_in_mixed_game_remain_analyzed(self):
        g=annotated(0,True);g.decisions.append(self.capture());a.summarize(g)
        self.assertEqual(g.metrics['decisions'],36)
        self.assertEqual(g.metrics['easy_conversion_decisions'],1)


if __name__=='__main__':unittest.main()
