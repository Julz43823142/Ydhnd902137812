"""Source-aware, synthetic-only integration regressions for Astra's audit."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import chess
from fairplay_config import CONFIG
from fairplay_data import parse_game
from fairplay_evidence_audit import audit_engine_sample, audit_maia_funnel
from fairplay_analysis import EngineScanner
from test_fairplay import TARGET, sample_row, FakeEngine
from fairplay_maia import policy_evidence
from test_fairplay_maia import decision, distribution


class ProductionAudit(unittest.TestCase):
    def sample(self, index=1):
        return parse_game(sample_row(index),TARGET)

    def test_overlapping_high_information_and_critical_not_nested(self):
        g=self.sample()
        for d in g.decisions:
            d.metrics={'useful':d.useful,'competitive':True,'difficulty':.9,
                       'search_inconsistent':False,'candidates':['e2e4'],'cpl':0,
                       'critical':False,'unique':False,'high_information':True}
        result=audit_engine_sample([g])['whole_engine_sample']
        self.assertEqual(result['branches']['critical']['eligible_survivors']['pass'],0)
        self.assertGreater(result['branches']['high_information']['eligible_survivors']['pass'],0)
        self.assertGreater(result['survivors'],0)
        self.assertNotIn('critical',[stage['gate'] for stage in result['stages']])

    def test_unknown_depth_metadata_separate_from_instability(self):
        g=self.sample()
        d=next(d for d in g.decisions if d.useful)
        d.metrics={'useful':True,'competitive':True,'difficulty':.8,
                   'search_inconsistent':False,'candidates':['e2e4'],'cpl':0}
        r=audit_engine_sample([g])['whole_engine_sample']['branches']['search_stable']['whole_scope']
        self.assertEqual(r['fail'],0)
        self.assertGreater(r['unknown'],0)

    def test_fast_snapshot_survives_deep_search(self):
        game=self.sample()
        engine=FakeEngine()
        scanner=EngineScanner(float('inf'),factory=lambda:engine)
        try:
            scanner.analyse(game,CONFIG.fast_nodes)
            original=[copy.deepcopy(d.fast_engine) for d in game.decisions]
            scanner.analyse(game,CONFIG.deep_nodes)
            self.assertEqual([d.fast_engine for d in game.decisions],original)
            self.assertTrue(any(d.metrics is not d.fast_engine for d in game.decisions if d.useful))
        finally:scanner.close()

    def test_maia_scope_is_full_not_selected_sample(self):
        g1,g2=self.sample(1),self.sample(2)
        result=audit_maia_funnel([g1,g2],selected_ids=[g2.identity])
        self.assertEqual(result['whole_engine_sample']['scope_games'],2)
        self.assertEqual(result['selected_period']['scope_games'],1)
        self.assertEqual(result['whole_engine_sample']['decisions'],
                         len(g1.decisions)+len(g2.decisions))
        stage=result['whole_engine_sample']['stages'][1]
        self.assertGreater(stage['unknown'],0)

    def test_same_engine_budget_metadata_only_supplies_counterfactual_if_matching(self):
        d=decision();d.human_policy=distribution(d)
        d.metrics.update(nodes=24000,search_contract={
            'engine':'Stockfish 19','mode':'nodes','requested':24000,
            'completed':True,'exact':True})
        d.metrics['policy_search']={'nodes':24000,'scores':{},
            'search_contract':{'engine':'Stockfish 18','mode':'nodes',
                               'requested':24000,'completed':True,'exact':True}}
        self.assertFalse(policy_evidence(d,d.human_policy)['counterfactual_complete'])
        d.metrics['policy_search']['search_contract']['engine']='Stockfish 19'
        self.assertTrue(policy_evidence(d,d.human_policy)['counterfactual_complete'])

    def test_completed_depth18_matched_counterfactuals(self):
        d=decision();d.human_policy=distribution(d)
        d.metrics.update(nodes=480000,search_depth=18,search_contract={
            'engine':'Stockfish 19','mode':'depth','requested':18,
            'completed':True,'exact':True})
        d.metrics['policy_search']={'depth':18,'scores':{},
            'search_contract':dict(d.metrics['search_contract'])}
        self.assertTrue(policy_evidence(d,d.human_policy)['counterfactual_complete'])
        d.metrics['policy_search']['depth']=16
        self.assertFalse(policy_evidence(d,d.human_policy)['counterfactual_complete'])

    def test_status_or_closure_does_not_change_audit(self):
        g=self.sample()
        for d in g.decisions:
            d.metrics={'useful':bool(d.useful),'competitive':True,'difficulty':.6,
                       'search_inconsistent':False,'candidates':['e2e4'],'cpl':0}
        a=audit_engine_sample([g])
        g.closed=True;g.status='closed';g.username='not-an-input'
        self.assertEqual(a,audit_engine_sample([g]))


if __name__=="__main__":unittest.main()
