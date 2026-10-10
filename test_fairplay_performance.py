"""Focused throughput/safety regressions. No real account data."""
from concurrent.futures import Future
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
import chess
import chess.engine
from fairplay_analysis import (ExactPositionCache,SharedEnginePool,
                               position_cache_key,run_position_batch)
from fairplay_config import CONFIG
from fairplay_data import ScanDeadline,DeadlineReached
from fairplay_policy import search_alternatives


def move():
    return SimpleNamespace(fen=chess.Board().fen(),move='e2e4',
        phase='middlegame',legal=20,useful=True,forced=False,trivial_kind=None,
        capture=False,gives_check=False,check=False,metrics={})


class PositionCacheTests(unittest.TestCase):
    def test_never_cache_incomplete_evidence(self):
        cache=ExactPositionCache(capacity=2)
        cache.put('bad',{'search_contract':{'completed':True,'exact':False}})
        self.assertIsNone(cache.get('bad'))
        cache.put('ok',{'before_cp':25,'search_contract':{'completed':True,'exact':True}})
        got=cache.get('ok');got['before_cp']=999
        self.assertEqual(cache.get('ok')['before_cp'],25)

    def test_key_keeps_depth_and_move_separate(self):
        sample=SimpleNamespace(color=chess.WHITE)
        d=move()
        fast=position_cache_key(sample,d,CONFIG.fast_nodes)
        self.assertNotEqual(fast,position_cache_key(sample,d,chess.engine.Limit(depth=18)))
        d.move='d2d4'
        self.assertNotEqual(fast,position_cache_key(sample,d,CONFIG.fast_nodes))

    def test_repeated_exact_position_bypasses_second_engine_search(self):
        pool=SharedEnginePool.__new__(SharedEnginePool)
        pool.config=CONFIG
        pool.position_cache=ExactPositionCache()
        calls=[]
        def fake(deadline,fn):
            class Worker:
                def analyse_decision(self,game,decision,nodes):
                    calls.append(1)
                    decision.metrics={'before_cp':30,
                        'search_contract':{'completed':True,'exact':True}}
                    return True
            return fn(Worker())
        pool._run_with_scanner=fake
        game=SimpleNamespace(color=chess.WHITE)
        a,b=move(),move()
        deadline=ScanDeadline(time.monotonic()+30)
        pool.run_decision(game,a,CONFIG.fast_nodes,deadline)
        pool.run_decision(game,b,CONFIG.fast_nodes,deadline)
        self.assertEqual(len(calls),1)
        self.assertEqual(a.metrics,b.metrics)


class SchedulingTests(unittest.TestCase):
    def test_cancelled_work_does_not_queue_all_5000_positions(self):
        count=[]
        class Inline:
            _max_workers=2
            def submit(self,*args):
                count.append(1)
                future=Future()
                future.set_exception(DeadlineReached())
                return future
        result,interrupted=run_position_batch(Inline(),
            SimpleNamespace(run_decision=None),
            [(SimpleNamespace(),object()) for _ in range(5000)],
            CONFIG.fast_nodes,None,lambda _:None,'Fast engine scan')
        self.assertTrue(interrupted)
        self.assertFalse(result)
        self.assertLessEqual(len(count),6)


class PolicySearchTests(unittest.TestCase):
    def test_deep_root_search_has_depth_timeout_not_fast_timeout(self):
        class Engine:
            options={}
            timeout=8
            def analyse(self,board,limit,root_moves):
                self.observed=self.timeout
                return {'pv':[root_moves[0]],'depth':18,
                    'score':chess.engine.PovScore(chess.engine.Cp(42),board.turn)}
        engine=Engine()
        scanner=SimpleNamespace(engine=engine,config=CONFIG,name='synthetic',
            deadline=ScanDeadline(time.monotonic()+30),
            profile={'root_seconds':0.,'root_searches':0,
                     'deep_root_seconds':0.,'fast_root_seconds':0.})
        d=move()
        d.human_policy={'e2e4':1.}
        d.metrics={'competitive':True,'useful':True,'candidates':[]}
        result=search_alternatives(scanner,d,chess.engine.Limit(depth=18))
        self.assertEqual(result['scores']['e2e4'],42)
        self.assertGreaterEqual(engine.observed,1200)

    def test_policy_roots_escalate_only_on_retry_after_timeout(self):
        class Engine:
            options={}
            timeout=8
            def analyse(self,board,limit,root_moves):
                self.observed=self.timeout
                return {'pv':[root_moves[0]],'depth':18,
                    'score':chess.engine.PovScore(chess.engine.Cp(42),board.turn)}
        engine=Engine()
        scanner=SimpleNamespace(engine=engine,config=CONFIG,name='synthetic',
            retry_after_timeout=True,deadline=ScanDeadline(time.monotonic()+30),
            profile={'root_seconds':0.,'root_searches':0,
                     'deep_root_seconds':0.,'fast_root_seconds':0.})
        d=move()
        d.human_policy={'e2e4':1.}
        d.metrics={'competitive':True,'useful':True,'candidates':[]}
        result=search_alternatives(scanner,d,chess.engine.Limit(depth=18))
        self.assertEqual(result['scores']['e2e4'],42)
        self.assertEqual(engine.observed,3600)
        self.assertEqual(scanner.last_search['timeout_seconds'],3600)


class PolicyCheckpointBatchTests(unittest.TestCase):
    def test_counterfactual_checkpoint_flushes_once_for_small_batch(self):
        from fairplay_policy import complete
        from unittest.mock import patch
        class Checkpoint:
            def __init__(self):self.records=[];self.flushes=0
            def restore_counterfactual(self,*args):return None
            def record_counterfactual(self,*args,**kwargs):
                self.records.append((args,kwargs));return True
            def flush(self,**kwargs):self.flushes+=1;return True
        checkpoint=Checkpoint()
        game=SimpleNamespace(decisions=[])
        for _ in range(10):
            d=move();d.human_policy={'e2e4':1.0};d.fast_policy={}
            game.decisions.append(d)
        contract={'mode':'nodes','requested':CONFIG.fast_nodes,'completed':True,'exact':True}
        def search(*_):
            return {'nodes':CONFIG.fast_nodes,'scores':{},'search_contract':contract}
        with patch('fairplay_policy.search_alternatives',side_effect=search), \
             patch('fairplay_maia.refresh_game'), \
             patch('fairplay_maia.policy_evidence',return_value={}):
            status=complete([game],CONFIG.fast_nodes,ScanDeadline(time.monotonic()+60),
                scanner=object(),checkpoint=checkpoint,target='synthetic',fast=True)
        self.assertTrue(status['complete'])
        self.assertEqual(len(checkpoint.records),10)
        self.assertTrue(all(kw.get('persist') is False for _,kw in checkpoint.records))
        self.assertEqual(checkpoint.flushes,1)


if __name__=='__main__':unittest.main()
