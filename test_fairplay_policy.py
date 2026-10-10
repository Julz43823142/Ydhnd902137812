"""Synthetic model/engine distributions only; no private accounts or labels."""
import copy
import math
import random
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import chess
from fairplay_config import CONFIG
from fairplay_maia import policy_evidence,carry_policy_after_deep
import fairplay_policy as policy
from test_fairplay_maia import decision,distribution

def sample(index,*,strong=True,deep=True,count=6):
    decisions=[]
    for ply in range(count):
        d=decision();d.ply=ply+21
        d.human_policy=distribution(d,rare=strong)
        d.metrics.update(nodes=CONFIG.deep_nodes if deep else CONFIG.fast_nodes,actual_cp=0,
            policy_search={'nodes':CONFIG.deep_nodes if deep else CONFIG.fast_nodes,'scores':{}},
            search_stability={'stable':True, 'compared':True,
                              'objective_quality_preserved':True})
        d.fast_policy=policy_evidence(d,d.human_policy)
        decisions.append(d)
    return SimpleNamespace(identity=str(index),ended=index*3600,time_class='blitz',rated=True,
        decisions=decisions,deep=deep,time_control='180',human_reference={},fast_metrics={},metrics={})

def result():
    return SimpleNamespace(priority='LOW',confidence='MEDIUM',partial=False,deep_confirmed=False,
        coverage={'primary_engine_complete':True},diagnostics={},families={},reasons=[])

class CounterfactualMath(unittest.TestCase):
    def test_human_preferred_move_outside_top_five_is_searched(self):
        d=decision();d.human_policy=distribution(d)
        d.human_policy[d.metrics['candidates'][1]]=.01
        missing=list(d.human_policy)[-1];d.human_policy[missing]=.81
        before=policy_evidence(d,d.human_policy)
        self.assertIn(missing,policy.alternatives(d))
        d.metrics.update(nodes=CONFIG.fast_nodes,
            policy_search={'nodes':CONFIG.fast_nodes,'scores':{missing:-250}})
        after=policy_evidence(d,d.human_policy)
        self.assertGreater(after['information'],before['information']+.4)
        self.assertGreater(after['known_policy_mass'],.8)

    def test_root_budget_mismatch_does_not_leak_fast_into_deep(self):
        d=sample(0).decisions[0];d.metrics['policy_search']['nodes']=CONFIG.fast_nodes
        r=policy_evidence(d,d.human_policy)
        self.assertFalse(r['counterfactual_complete'])

    def test_overturning_alternative_invalidates_comparison(self):
        d=sample(0).decisions[0]
        d.metrics['policy_search']['scores']={d.move:150}
        self.assertIsNone(policy_evidence(d,d.human_policy))

    def test_equivalence_and_misses_remain_in_denominator(self):
        g=sample(0);baseline=policy.game_summary(g)
        for d in g.decisions[:4]:
            d.metrics.update(actual_cp=-250,cpl=250,scaled_loss=.3)
        revised=policy.game_summary(g)
        self.assertEqual(revised['positions'],baseline['positions'])
        self.assertLess(revised['signed_excess'],baseline['signed_excess'])
        self.assertFalse(revised['contributor'])

    def test_second_candidate_can_be_informative(self):
        d=sample(0).decisions[0]
        d.move=d.metrics['candidates'][1]
        d.metrics.update(candidate_cp=[0,-4,-250,-350,-450],actual_cp=-4,cpl=4,scaled_loss=.004)
        legal=list(d.human_policy)
        d.human_policy={move:.01 for move in legal}
        d.human_policy[d.metrics['candidates'][2]]=.81
        r=policy_evidence(d,d.human_policy)
        self.assertGreater(r['information'],.4)

    def test_root_selection_is_independent_of_played_quality(self):
        d=sample(0).decisions[0];a=policy.alternatives(d)
        d.metrics.update(cpl=900,scaled_loss=.9,high_information=False)
        self.assertEqual(a,policy.alternatives(d))

class DistributedEvidence(unittest.TestCase):
    def test_general_learned_discrepancy_can_reach_high_without_timing(self):
        r=policy.integrate(result(),[sample(i) for i in range(6)])
        self.assertEqual(r.priority,'HIGH')
        self.assertEqual(r.confidence,'MEDIUM')
        self.assertTrue(r.diagnostics['learned_gameplay']['passed'])
        self.assertNotEqual(r.priority,'VERY HIGH')

    def test_one_huge_game_cannot_reach_high(self):
        self.assertEqual(policy.integrate(result(),[sample(0,count=100)]).priority,'LOW')

    def test_two_perfect_games_are_not_enough_for_learned_route(self):
        self.assertEqual(policy.integrate(result(),[sample(i) for i in range(2)]).priority,'LOW')

    def test_strong_human_expected_play_is_not_high(self):
        self.assertEqual(policy.integrate(result(),[sample(i,strong=False) for i in range(20)]).priority,'LOW')

    def test_sparse_isolated_hits_do_not_satisfy_distribution(self):
        games=[sample(i,strong=i%4==0) for i in range(24)]
        self.assertEqual(policy.integrate(result(),games).priority,'LOW')

    def test_deep_confirmation_and_primary_coverage_required(self):
        for count in (0,1,3):
            games=[sample(i,deep=i<count) for i in range(6)]
            self.assertEqual(policy.integrate(result(),games).priority,'LOW')
        r=result();r.coverage['primary_engine_complete']=False
        self.assertEqual(policy.integrate(r,[sample(i) for i in range(6)]).priority,'LOW')

    def test_missing_optional_context_does_not_erase_evidence(self):
        r=result();r.partial=True;r.coverage['optional_context_partial']=True
        self.assertEqual(policy.integrate(r,[sample(i) for i in range(6)]).priority,'HIGH')

    def test_deep_quality_drift_blocks(self):
        games=[sample(i) for i in range(6)]
        for g in games:
            for d in g.decisions:
                d.metrics['search_stability']['stable']=False
                d.metrics['search_stability']['objective_quality_preserved']=False
        self.assertEqual(policy.integrate(result(),games).priority,'LOW')

    def test_bullet_and_mixed_classes_cannot_manufacture_period(self):
        games=[sample(i) for i in range(6)]
        for g in games:g.time_class='bullet'
        self.assertEqual(policy.periods(games),[])
        for i,g in enumerate(games):g.time_class='rapid' if i%2 else 'blitz'
        self.assertEqual(policy.periods(games),[])

    def test_blitz_controls_combine_only_gameplay(self):
        games=[sample(i) for i in range(6)]
        for i,g in enumerate(games):g.time_control=('180','180+2','300')[i%3]
        self.assertEqual(policy.integrate(result(),games).priority,'HIGH')

    def test_ablation_and_missing_model_are_safe(self):
        games=[sample(i) for i in range(6)]
        self.assertEqual(policy.integrate(result(),games,replace(CONFIG,disabled_features=('neural',))).priority,'LOW')
        for g in games:
            for d in g.decisions:d.human_policy={};d.fast_policy={}
        self.assertEqual(policy.integrate(result(),games).priority,'LOW')

    def test_model_policy_null_not_just_synthetic_positives(self):
        # Deterministic model-drawn choices, with mistakes, across many accounts.
        # This checks aggregation under a model null, not real-world specificity.
        rng=random.Random(1801)
        for account in range(60):
            games=[sample(i,count=8) for i in range(12)]
            for g in games:
                for d in g.decisions:
                    move=rng.choices(list(d.human_policy),list(d.human_policy.values()))[0]
                    scores=dict(zip(d.metrics['candidates'],d.metrics['candidate_cp']))
                    d.move=move;d.metrics['actual_cp']=scores.get(move,0)
                    d.fast_policy=policy_evidence(d,d.human_policy)
            self.assertNotEqual(policy.integrate(result(),games).priority,'HIGH')

class OpportunityWeighting(unittest.TestCase):
    def test_equal_evidence_distributed_over_short_games_can_qualify(self):
        for games,count in ((6,4),(8,3),(12,2)):
            group=[sample(i,count=count) for i in range(games)]
            with self.subTest(games=games):
                summary=policy.summarize(group,fast=True)
                self.assertEqual(summary['contributor_weight'],6)
                self.assertEqual(summary['effective_positions'],24)
                self.assertEqual(policy.integrate(result(),group).priority,'HIGH')

    def test_no_credit_created_by_partitioning_or_padding(self):
        for games,count in ((6,2),(11,2),(23,1)):
            group=[sample(i,count=count) for i in range(games)]
            self.assertEqual(policy.integrate(result(),group).priority,'LOW')
        group=[sample(0,count=100)]+[sample(i,count=1) for i in range(1,12)]
        self.assertEqual(policy.integrate(result(),group).priority,'LOW')

    def test_sparse_deep_requires_same_total_evidence(self):
        for reviewed in (4,7,8):
            group=[sample(i,count=2,deep=i<reviewed) for i in range(12)]
            self.assertEqual(policy.integrate(result(),group).priority,
                             'HIGH' if reviewed==8 else 'LOW')

    def test_sparse_misses_cannot_be_filtered_out(self):
        group=[sample(i,count=2,strong=i%2==0) for i in range(24)]
        self.assertEqual(policy.integrate(result(),group).priority,'LOW')

    def test_single_decision_noise_gets_only_fractional_weight(self):
        group=[sample(0,count=4),sample(1,count=1,strong=False)]
        a,b=[policy.game_summary(g) for g in group]
        s=policy.summarize(group)
        self.assertEqual(s['opportunity_weight'],1.25)
        self.assertAlmostEqual(s['signed_excess'],(a['signed_excess']+.25*b['signed_excess'])/1.25)

    def test_sparse_policy_draws_do_not_manufacture_high(self):
        # Aggregation null only, not an estimate of real-world specificity.
        rng=random.Random(18101)
        for count in (2,3):
            for trial in range(40):
                group=[sample(i,count=count) for i in range(24)]
                for g in group:
                    for d in g.decisions:
                        d.move=rng.choices(list(d.human_policy),list(d.human_policy.values()))[0]
                        scores=dict(zip(d.metrics['candidates'],d.metrics['candidate_cp']))
                        d.metrics['actual_cp']=scores.get(d.move,0)
                        d.fast_policy=policy_evidence(d,d.human_policy)
                self.assertEqual(policy.integrate(result(),group).priority,'LOW')

    def test_deep_coverage_keeps_early_and_late_anchors(self):
        group=[sample(i,count=4) for i in range(20)]
        controls=[sample(100),sample(101)]
        # Force one full-period candidate so its temporal anchors are tested.
        with patch('fairplay_policy.periods',return_value=[{'ids':tuple(g.identity for g in group),'blockers':[]}]):
            plan=policy.allocate(group,controls)
        self.assertIn(group[0],plan)
        self.assertIn(group[-1],plan)
        self.assertEqual(len(plan),8)

    def test_deep_allocation_counts_opportunities_and_keeps_controls(self):
        group=[sample(i,count=2) for i in range(12)]
        controls=[sample(100),sample(101)]
        plan=policy.allocate(group,controls)
        self.assertEqual(plan[:2],controls)
        self.assertEqual(len(plan),14)
        self.assertEqual({g.identity for g in plan[2:]},{g.identity for g in group})

class Lifecycle(unittest.TestCase):
    def test_optional_failure_clears_stale_priority_evidence(self):
        games=[sample(0)]
        with patch('fairplay_policy.search_alternatives',side_effect=RuntimeError('failure')):
            status=policy.complete(games,CONFIG.fast_nodes,math.inf,scanner=object(),fast=True)
        self.assertFalse(status['complete'])
        self.assertTrue(all(not d.fast_policy and 'policy_search' not in d.metrics for d in games[0].decisions))

    def test_deep_cache_clears_old_counterfactual_and_keeps_fast_comparison(self):
        source=sample(0);deep=copy.deepcopy(source)
        carry_policy_after_deep(source,deep)
        for a,b in zip(source.decisions,deep.decisions):
            self.assertEqual(a.fast_policy,b.fast_policy)
            self.assertNotIn('policy_search',b.metrics)

    def test_confirmation_preserves_original_plan_and_hard_cap(self):
        games=[sample(i) for i in range(20)]
        initial=games[:10]
        plan=policy.allocate(games,initial)
        self.assertEqual(plan[:10],initial)
        self.assertLessEqual(len(plan),CONFIG.deep_max_games)

if __name__=='__main__':unittest.main()
