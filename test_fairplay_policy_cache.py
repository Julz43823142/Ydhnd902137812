"""Exact objective root reuse; learned probabilities are never cached."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
import chess
import chess.engine
from fairplay_analysis import SharedEnginePool, exact_root_cache_key
from fairplay_config import CONFIG
from fairplay_policy import search_alternatives, complete
from test_fairplay_candidate_reuse import DepthEngine, position


def policy_decision(root='a2a4'):
    _,d=position('e2e4')
    d.human_policy={root:1.}
    d.metrics={'competitive':True,'useful':True,'candidates':[]}
    return d


class PolicyCache(unittest.TestCase):
    def setUp(self):
        self.engine=DepthEngine()
        self.pool=SharedEnginePool(size=1,factory=lambda:self.engine)
        self.addCleanup(self.pool.close)
        self.limit=chess.engine.Limit(depth=18)
        self.deadline=time.monotonic()+30

    def run_policy(self,d=None,budget=None):
        return self.pool._run_cached(self.deadline,
            lambda scanner:search_alternatives(scanner,d or policy_decision(),budget or self.limit))

    def test_repeated_alternative_preserves_complete_result(self):
        first=self.run_policy();second=self.run_policy()
        self.assertEqual(first,second)
        self.assertEqual(len(self.engine.calls),1)
        self.assertEqual(self.pool.scanners[0].profile['policy_root_cache_hits'],1)
        first['scores']['a2a4']=-9999
        self.assertEqual(self.run_policy()['scores']['a2a4'],200)

    def test_played_root_is_reused_as_human_alternative(self):
        g,d=position('a2a4')
        self.pool.run_decision(g,d,self.limit,self.deadline)
        calls=len(self.engine.calls)
        result=self.run_policy()
        self.assertEqual(result['scores']['a2a4'],d.metrics['actual_cp'])
        self.assertEqual(len(self.engine.calls),calls)

    def test_human_alternative_is_reused_as_played_root(self):
        result=self.run_policy()
        g,d=position('a2a4')
        self.pool.run_decision(g,d,self.limit,self.deadline)
        self.assertEqual(d.metrics['actual_cp'],result['scores']['a2a4'])
        self.assertEqual(len(self.engine.calls),2)  # policy root + unrestricted candidates
        self.assertEqual(self.pool.scanners[0].profile['root_cache_hits'],1)

    def test_budgets_and_selected_roots_are_independent(self):
        self.run_policy();self.run_policy(budget=CONFIG.fast_nodes)
        self.run_policy(policy_decision('h2h4'))
        self.assertEqual(len(self.engine.calls),3)

    def test_policy_change_reselects_roots_instead_of_borrowing_policy(self):
        d=policy_decision();d.human_policy={'a2a4':.8,'h2h4':.2}
        first=self.run_policy(d)
        d.human_policy={'h2h4':1.}
        second=self.run_policy(d)
        self.assertEqual(set(first['scores']),{'a2a4','h2h4'})
        self.assertEqual(set(second['scores']),{'h2h4'})
        self.assertEqual(len(self.engine.calls),2)

    def test_invalid_cached_proof_recomputes(self):
        scanner=self.pool.scanners[0]
        key=exact_root_cache_key(scanner.name,chess.STARTING_FEN,self.limit,'a2a4')
        self.run_policy()
        saved=self.pool.position_cache.get(key)
        for mutation in ('inexact','depth','root','engine','pv_type','budget'):
            with self.subTest(mutation=mutation):
                bad=deepcopy(saved)
                if mutation=='inexact':bad['line']['upperbound']=True
                if mutation=='depth':bad['line']['depth']=17
                if mutation=='root':bad['line']['pv']=[chess.Move.from_uci('h2h4')]
                if mutation=='engine':bad['search_contract']['engine']='wrong engine'
                if mutation=='pv_type':bad['line']['pv']=1
                if mutation=='budget':bad['search_contract']['requested']=True
                self.pool.position_cache.put(key,bad)
                calls=len(self.engine.calls)
                self.assertEqual(self.run_policy()['scores']['a2a4'],200)
                self.assertEqual(len(self.engine.calls),calls+1)

    def test_inexact_fresh_roots_never_enter_cache(self):
        original=self.engine.analyse
        def inexact(*args,**kwargs):
            row=original(*args,**kwargs);row['lowerbound']=True;return row
        self.engine.analyse=inexact
        for _ in range(2):
            with self.assertRaises(chess.engine.EngineError):self.run_policy()
        self.assertEqual(len(self.engine.calls),4)  # pool retries each failed search once
        self.assertEqual(self.pool.position_cache.snapshot()['entries'],0)

    def test_disabling_cache_keeps_policy_searches_cold(self):
        self.pool.position_cache=None
        self.assertEqual(self.run_policy(),self.run_policy())
        self.assertEqual(len(self.engine.calls),2)

    def test_parallel_complete_attaches_cache_and_keeps_probabilities_fresh(self):
        decisions=[policy_decision() for _ in range(3)]
        for d in decisions:
            d.human_policy={m.uci():0. for m in chess.Board().legal_moves}
            d.human_policy['a2a4']=1.
        game=SimpleNamespace(time_class='blitz',decisions=decisions)
        with patch('fairplay_maia.refresh_game'),ThreadPoolExecutor(max_workers=1) as executor:
            result=complete([game],self.limit,self.deadline,executor=executor,pool=self.pool)
        self.assertTrue(result['complete']);self.assertEqual(result['positions'],3)
        self.assertEqual(len(self.engine.calls),1)
        self.assertIsNone(self.pool.scanners[0]._candidate_cache)


if __name__=='__main__':unittest.main()
