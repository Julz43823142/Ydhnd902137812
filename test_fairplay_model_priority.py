"""Synthetic distributions/positions only; no case identities or ban labels."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from fairplay_accuracy_scope import maia_scope
from fairplay_config import CONFIG
from fairplay_human import annotate_game, quality_components, period_raw_excess_floor
from fairplay_model_priority import model_periods, integrate_model_periods
from fairplay_recent_priority import recent_priority
from test_fairplay_maia import decision, distribution

CFG = replace(CONFIG, human_reachable_headroom=True)
REFERENCE = {'available': True}


def sample(index, *, strong=True, count=2, kind='blitz', human=False):
    decisions=[]
    for i in range(8):
        d=decision()
        d.metrics['candidates']=d.metrics['candidates'][:3]
        d.metrics['candidate_cp']=[0,-150,-250]
        d.ply=2*i+1;d.legal=20;d.capture=d.check=d.gives_check=False
        d.human_policy=distribution(d,rare=not human)
        d.fast_engine={}
        d.metrics.update(actual_cp=0,search_inconsistent=False,candidate_count=3,gap=150,spread=250,
                         top1=True,top3=True,weight=1,near_best=True,rank=1,
                         search_depth=12 if kind=='bullet' else 18,
                         search_contract={'mode':'depth','requested':12 if kind=='bullet' else 18,
                             'completed':True,'exact':True,'multipv':CONFIG.deep_multipv})
        if not strong:
            d.move=d.metrics['candidates'][1]
            d.metrics.update(actual_cp=-150,cpl=150,scaled_loss=.15,rank=2,near_best=False)
        if i>=count:d.metrics['easy_conversion']=True
        decisions.append(d)
    g=SimpleNamespace(identity=f'invented-{index}',ended=1700000000+index*600,
        rated=True,rating=1000,opponent_rating=1000,time_class=kind,time_control='180+0',
        accuracy=None,decisions=decisions,deep=True,fast_metrics={},moves=[],
        metrics={'decisions':8},human_reference={})
    annotate_game(g,CFG)
    return g


def assess(rows,reference=REFERENCE,**kwargs):
    return recent_priority(rows,CFG,complete=True,model_reference=reference,**kwargs)


class CoveredModelPeriods(unittest.TestCase):
    def test_nineteen_of_twenty_two_can_high_with_six_replicated_games(self):
        rows=[sample(i,count=n) for i,n in enumerate((4,3,3,0,0,4,4,4,0,0))]
        for i in (0,5,6):
            d=rows[i].decisions[0]
            d.move=d.metrics['candidates'][1]
            d.metrics.update(actual_cp=-150,cpl=150,scaled_loss=.15,rank=2)
            annotate_game(rows[i],CFG)
        result=assess(rows)
        self.assertEqual(result['priority'],'HIGH')
        selected=result['model_periods']['selected']['summary']
        self.assertEqual((selected['hits'],selected['opportunities']),(19,22))
        self.assertEqual(selected['contributors'],6)

    def test_twenty_real_opportunities_across_ten_games_can_high(self):
        rows=[sample(i) for i in range(10)]
        result=assess(rows)
        self.assertEqual(result['priority'],'HIGH')
        self.assertIn('model_engine_period',result['triggered_routes'])
        self.assertEqual(result['classes']['blitz']['qualifying_disjoint_blocks'],0)
        self.assertTrue(result['model_periods']['passed'])
        self.assertLess(result['model_periods']['selected']['summary']['engine_opportunities'],40)

    def test_recent_six_game_anomaly_not_diluted_by_eighty_ordinary_games(self):
        rows=[sample(i,strong=False) for i in range(80)]+[sample(i) for i in range(80,86)]
        self.assertEqual(assess(rows)['priority'],'HIGH')
        self.assertEqual(assess(rows)['model_periods']['selected']['summary']['games'],6)

    def test_sixty_percent_is_not_automatically_high(self):
        rows=[sample(i,count=5) for i in range(10)]
        for g in rows:
            for d in g.decisions[3:5]:
                d.move=d.metrics['candidates'][1]
                d.metrics.update(actual_cp=-150,cpl=150,scaled_loss=.15,rank=2)
            annotate_game(g,CFG)
        result=assess(rows)
        self.assertNotEqual(result['priority'],'HIGH')
        self.assertFalse(result['model_periods']['passed'])

    def test_one_huge_game_cannot_high_or_moderate(self):
        rows=[sample(i,strong=False) for i in range(20)]
        rows[-1]=sample(19)
        rows[-1].decisions*=100
        result=assess(rows)
        self.assertEqual(result['priority'],'LOW')
        self.assertLessEqual(result['model_periods']['selected']['summary']['opportunities'],120)

    def test_two_good_games_or_only_six_total_do_not_override_account_minimum(self):
        rows=[sample(i,strong=i>=18) for i in range(20)]
        self.assertEqual(assess(rows)['priority'],'LOW')
        self.assertEqual(assess([sample(i) for i in range(6)])['priority'],'INSUFFICIENT DATA')

    def test_high_quality_moves_expected_by_maia_are_not_high(self):
        result=assess([sample(i,human=True) for i in range(10)])
        self.assertEqual(result['priority'],'LOW')
        self.assertFalse(result['model_periods']['moderate'])

    def test_missing_or_failed_model_keeps_engine_only_result(self):
        for reference in (None,{'available':False}):
            result=assess([sample(i) for i in range(10)],reference)
            self.assertEqual(result['priority'],'LOW')
            self.assertFalse(result['model_periods']['available'])

    def test_partial_model_data_is_not_positive_evidence(self):
        rows=[sample(i) for i in range(10)]
        for g in rows:g.decisions[-1].human_policy={}
        result=assess(rows)
        self.assertEqual(result['priority'],'LOW')
        self.assertIn('complete required depth and policy coverage',result['model_periods']['selected']['blockers'])

    def test_depth_contract_and_model_nan_fail_closed(self):
        for field in ('depth','policy'):
            rows=[sample(i) for i in range(10)]
            for g in rows:
                if field=='depth':g.decisions[0].metrics['search_contract']['exact']=False
                else:g.decisions[0].human_policy[g.decisions[0].move]=float('nan')
            self.assertFalse(assess(rows)['model_periods']['passed'])

    def test_missing_comparison_cannot_remove_a_difficult_miss(self):
        rows=[sample(i) for i in range(10)]
        for g in rows:g.decisions[0].metrics['candidate_cp']=[]
        self.assertFalse(assess(rows)['model_periods']['passed'])

    def test_research_ablation_can_disable_model_route(self):
        rows=[sample(i) for i in range(10)]
        for feature in ('human','neural'):
            result=recent_priority(rows,replace(CFG,disabled_features=(feature,)),
                                   complete=True,model_reference=REFERENCE)
            self.assertEqual(result['priority'],'LOW')
            self.assertFalse(result['model_periods']['available'])

    def test_unknown_alternatives_are_not_assumed_bad(self):
        rows=[sample(i) for i in range(10)]
        for g in rows:
            for d in g.decisions:
                p=d.human_policy
                other=next(move for move in p if move not in d.metrics['candidates'])
                old=d.metrics['candidates'][1]
                p[other],p[old]=p[old],p[other]
        self.assertEqual(assess(rows)['priority'],'LOW')

    def test_equivalent_second_choice_does_not_evade_by_avoiding_top1(self):
        for rank in (2,):
            rows=[sample(i) for i in range(10)]
            for g in rows:
                for d in g.decisions:
                    d.metrics['candidate_cp']=[0,-4,-250]
                    d.move=d.metrics['candidates'][rank-1]
                    # Most human mass on the genuinely inferior candidate.
                    p={move:.01 for move in d.human_policy};p[d.metrics['candidates'][2]]=.81
                    d.human_policy=p
                    d.metrics.update(actual_cp=-4*(rank-1),cpl=4*(rank-1),scaled_loss=.004*(rank-1),rank=rank)
                annotate_game(g,CFG)
            self.assertEqual(assess(rows)['priority'],'HIGH')

    def test_third_equivalent_candidate_needs_real_human_alternative_evidence(self):
        rows=[sample(i) for i in range(10)]
        for g in rows:
            for d in g.decisions:
                d.metrics['candidate_cp']=[0,-4,-8]
                d.metrics.update(spread=8,actual_cp=-8,cpl=8,scaled_loss=.008,rank=3)
                d.move=d.metrics['candidates'][2]
            annotate_game(g,CFG)
        # MultiPV=3 supplies no worse evaluated alternative here. Never invent
        # a fourth/fifth candidate or assume unknown human moves are bad.
        self.assertNotEqual(assess(rows)['priority'],'HIGH')

    def test_book_forced_or_conversion_cannot_support_new_route(self):
        for kind in ('book','forced','conversion'):
            rows=[sample(i) for i in range(10)]
            for g in rows:
                for d in g.decisions:
                    if kind=='book':d.phase='opening'
                    elif kind=='forced':d.forced=True
                    else:d.metrics['easy_conversion']=True
                annotate_game(g,CFG)
            self.assertFalse(assess(rows)['model_periods']['passed'])

    def test_random_clock_timing_is_not_required(self):
        rows=[sample(i) for i in range(10)]
        for g in rows:
            for i,d in enumerate(g.decisions):d.think=(i*31)%59
        self.assertEqual(assess(rows)['priority'],'HIGH')

    def test_bullet_requires_its_own_longer_period_and_stronger_coverage(self):
        self.assertEqual(assess([sample(i,kind='bullet') for i in range(10)])['priority'],'LOW')
        result=assess([sample(i,kind='bullet') for i in range(20)])
        self.assertEqual(result['priority'],'HIGH')
        self.assertNotEqual(result['priority'],'VERY HIGH')

    def test_missing_optional_archive_does_not_erase_supported_period(self):
        rows=[sample(i) for i in range(10)]
        self.assertEqual(assess(rows,incomplete_archive_months=('2000-01',))['priority'],'HIGH')
        result=assess(rows,incomplete_archive_months=('2023-11',))
        self.assertFalse(result['model_periods']['passed'])

    def test_misses_empty_games_and_duplicates_cannot_manufacture_breadth(self):
        rows=[sample(i,strong=i<3) for i in range(10)]
        self.assertFalse(assess(rows)['model_periods']['passed'])
        rows=[sample(i) for i in range(10)];rows[-1].identity=rows[-2].identity
        self.assertFalse(model_periods(rows,CFG,complete=True,reference=REFERENCE)['passed'])

    def test_accuracy_status_and_username_do_not_enter_this_route(self):
        rows=[sample(i) for i in range(10)]
        before=assess(rows)['model_periods']
        for g in rows:g.accuracy=99;g.status='closed:fair_play_violations';g.username='invented'
        self.assertEqual(assess(rows)['model_periods'],before)

    def test_model_integration_does_not_mutate_original_measurements(self):
        rows=[sample(i) for i in range(10)];before=deepcopy(rows)
        assess(rows)
        self.assertEqual([g.metrics for g in rows],[g.metrics for g in before])
        self.assertEqual([d.human_policy for g in rows for d in g.decisions],
                         [d.human_policy for g in before for d in g.decisions])


class ReachableQualityAndCoverage(unittest.TestCase):
    def test_discord_details_explain_denominators_without_claiming_cheating_odds(self):
        import json
        from fairplay_ui import detail_embed
        from fairplay_v51_shadow import summarize_observations, observations
        rows=[sample(i) for i in range(10)]
        tier=assess(rows)
        for g in rows:
            for d in g.decisions:d.clock_valid=False;d.think=None
        record=summarize_observations(observations(rows))
        record['review_priority']=tier
        result=SimpleNamespace(username='Synthetic',priority=tier['priority'],
            confidence='LOW',reasons=tier['reasons'],classes={},
            diagnostics={'v51_shadow':record})
        for mode in ('Human Moves','Review Gates','Engine Analysis','Clusters & History'):
            card=detail_embed(result,mode)
            value=json.dumps(card.to_dict())
            self.assertIn('Combined engine / Maia period',value)
            self.assertIn('NOT a cheating percentage',value)
            self.assertLessEqual(len(card),6000)

    def test_perfect_quality_is_reachable_without_changing_legacy(self):
        for expected in (.4,.8,.9,.94,.97):
            excess,_,_,_,floor=quality_components(1,expected,3200,CFG)
            self.assertGreaterEqual(excess+1e-12,floor)
            self.assertGreaterEqual(excess+1e-12,period_raw_excess_floor({'quality_reference':expected},CFG))
        self.assertGreater(quality_components(1,.97,3200)[-1],.03)

    def test_ordinary_elite_quality_and_tiny_excess_are_not_high(self):
        self.assertGreater(period_raw_excess_floor({'quality_reference':.97},CFG),.01)
        rows=[sample(i) for i in range(20)]
        for g in rows:
            g.rating=3200
            for d in g.decisions:d.metrics.update(actual_cp=-20,cpl=20,scaled_loss=.018)
            annotate_game(g,CFG)
        self.assertNotEqual(assess(rows)['priority'],'HIGH')

    def test_full_model_scope_does_not_depend_on_published_scores(self):
        rows=[sample(i) for i in range(200)]
        selected,scope=maia_scope(rows,complete_reference=True)
        self.assertEqual(len(selected),200)
        self.assertTrue(scope['all_rated_games'])
        self.assertEqual(scope['selected_positions'],1600)
        for g in rows:g.accuracy=99
        self.assertEqual([g.identity for g in maia_scope(rows,complete_reference=True)[0]],
                         [g.identity for g in selected])


if __name__=='__main__':unittest.main()
