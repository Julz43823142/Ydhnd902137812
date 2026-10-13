"""Synthetic resume-certification and single-validation regressions."""
import unittest
from copy import deepcopy
from unittest.mock import patch
import chess.engine
from fairplay_config import CONFIG
from fairplay_checkpoint import valid_counterfactual
import test_fairplay_checkpoint as checkpoint_fixture
from test_fairplay_checkpoint import game_with_positions, metrics
from test_fairplay_policy import sample
import fairplay_maia as maia
from fairplay_evidence_audit import audit_maia_funnel


class ResumeCertification(unittest.TestCase):
    setUp = checkpoint_fixture.CheckpointTests.setUp
    store = checkpoint_fixture.CheckpointTests.store
    bind = checkpoint_fixture.CheckpointTests.bind
    def test_missing_exact_or_inconsistent_search_is_not_recorded(self):
        store=self.store();self.bind(store);g=game_with_positions(1);d=g.decisions[0]
        for value in (None,False,1,'true'):
            d.metrics=metrics('deep')
            if value is None:d.metrics['search_contract'].pop('exact')
            else:d.metrics['search_contract']['exact']=value
            self.assertFalse(store.record('privateaccount',g,d,'deep',persist=False))
        d.metrics=metrics('deep');d.metrics['search_inconsistent']=True
        self.assertFalse(store.record('privateaccount',g,d,'deep',persist=False))

    def test_restore_rejects_uncertified_or_malformed_metrics_without_mutation(self):
        store=self.store();self.bind(store);g=game_with_positions(1);d=g.decisions[0]
        key=store._key(g,d,'deep')
        for change in ('missing_exact','missing_depth','string_depth','boolean_depth','inconsistent','null_contract','null_metrics'):
            row=metrics('deep')
            if change=='missing_exact':row['search_contract'].pop('exact')
            elif change=='missing_depth':row.pop('search_depth')
            elif change=='string_depth':row['search_depth']='18'
            elif change=='boolean_depth':row['search_depth']=True
            elif change=='inconsistent':row['search_inconsistent']=True
            elif change=='null_contract':row['search_contract']=None
            elif change=='null_metrics':row=None
            store.state['jobs']['privateaccount']['positions'][key]={'metrics':row}
            d.metrics={'sentinel':True}
            self.assertFalse(store.restore('privateaccount',g,d,'deep',CONFIG,True))
            self.assertEqual(d.metrics,{'sentinel':True})

    def test_counterfactual_declared_depth_does_not_replace_achieved_depth(self):
        store=self.store();self.bind(store);g=game_with_positions(1);d=g.decisions[0]
        base={'depth':18,'scores':{'e2e4':0},'search_contract':{
            'mode':'depth','requested':18,'completed':True,'exact':True}}
        self.assertTrue(store.record_counterfactual('privateaccount',g,d,'policy-deep',base,persist=False))
        self.assertEqual(store.restore_counterfactual('privateaccount',g,d,'policy-deep',chess.engine.Limit(depth=18)),base)
        for value in (17,None,'18',True):
            broken=deepcopy(base);broken['depth']=value
            key=store._key(g,d,'policy-deep')
            store.state['jobs']['privateaccount']['positions'][key]={'search':broken}
            self.assertIsNone(store.restore_counterfactual('privateaccount',g,d,'policy-deep',chess.engine.Limit(depth=18)))
            self.assertFalse(store.record_counterfactual('privateaccount',g,d,'policy-deep',broken,persist=False))


class SinglePolicyValidation(unittest.TestCase):
    def test_alignment_and_engine_validity_remain_distinct(self):
        d=sample(0).decisions[0]
        expected=maia.policy_evidence(d,d.human_policy)
        with patch.object(maia,'valid_policy',wraps=maia.valid_policy) as valid:
            self.assertEqual(maia.inspect_policy(d,d.human_policy),(True,expected))
        self.assertEqual(valid.call_count,1)
        d.metrics['actual_cp']=float('nan')
        self.assertEqual(maia.inspect_policy(d,d.human_policy),(True,None))
        self.assertEqual(maia.inspect_policy(d,{}),(False,None))

    def test_audit_validates_each_existing_policy_once(self):
        g=sample(0);g.rating=1400;g.opponent_rating=1400
        with patch.object(maia,'valid_policy',wraps=maia.valid_policy) as valid:
            report=audit_maia_funnel([g])
        self.assertEqual(valid.call_count,len(g.decisions))
        self.assertEqual(report['whole_engine_sample']['decisions'],len(g.decisions))

    def test_nonstring_positions_fail_validation_cleanly(self):
        d=sample(0).decisions[0]
        for value in (None,123,[]):
            d.fen=value
            self.assertEqual(maia.inspect_policy(d,d.human_policy),(False,None))

    def test_daily_context_status_is_explicit_without_scoring_unrated(self):
        from types import SimpleNamespace
        from fairplay_daily import integrate
        from test_fairplay_daily import game
        rows=[game(i) for i in range(4)]
        rows[1].rated=False;rows[2].rated=None;rows[3].time_class='blitz'
        result=integrate(SimpleNamespace(diagnostics={}),[],context_games=rows)
        audit=result.diagnostics['daily_review']
        self.assertEqual(audit['context_games'],3)
        self.assertEqual(audit['context_rated_games'],1)
        self.assertEqual(audit['context_unrated_games'],1)
        self.assertEqual(audit['context_unknown_rated_games'],1)
        self.assertEqual(audit['rated_games'],0)
        self.assertFalse(audit['supported'])

    def test_unknown_counterfactual_scores_cannot_be_cached(self):
        base={'nodes':CONFIG.fast_nodes,'scores':{'e2e4':0},'search_contract':{
            'mode':'nodes','requested':CONFIG.fast_nodes,'completed':True,'exact':True}}
        self.assertTrue(valid_counterfactual(base))
        for value in (None,[],{'e2e4':True},{'e2e4':float('nan')}):
            row=deepcopy(base);row['scores']=value
            self.assertFalse(valid_counterfactual(row))


if __name__=='__main__':unittest.main()
