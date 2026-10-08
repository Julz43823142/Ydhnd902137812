"""Regressions for the fixed-node transport and failed engine-pool recovery."""
import threading
import queue
import time
import unittest
from unittest.mock import Mock, patch
import chess
import chess.engine

from fairplay_engine import CompactNodeEngine
from fairplay_analysis import SharedEnginePool
from fairplay_data import ScanDeadline, DeadlineReached, ReviewError


class CompactInfoTests(unittest.TestCase):
    def test_cp_and_first_pv_are_player_pov(self):
        index, row=CompactNodeEngine.parse_info(
            'info depth 10 multipv 2 score cp -31 nodes 24000 pv e2e4 e7e5 g1f3',chess.WHITE)
        self.assertEqual(index,2)
        self.assertEqual(row['score'].pov(chess.WHITE).score(),-31)
        self.assertEqual(row['score'].pov(chess.BLACK).score(),31)
        self.assertEqual(row['pv'],[chess.Move.from_uci('e2e4')])

    def test_mate_and_partial_info(self):
        _,row=CompactNodeEngine.parse_info('info score mate -3 pv a7a8q',chess.BLACK)
        self.assertEqual(row['score'].pov(chess.BLACK).mate(),-3)
        self.assertEqual(row['pv'][0],chess.Move.from_uci('a7a8q'))
        self.assertIsNone(CompactNodeEngine.parse_info('info string score cp 900 pv a1a8',True))
        self.assertIsNone(CompactNodeEngine.parse_info('info depth 9 nodes 24',True))
        self.assertIsNone(CompactNodeEngine.parse_info('info score cp broken',True))


class PoolFailureTests(unittest.TestCase):
    def empty_pool(self):
        pool=SharedEnginePool.__new__(SharedEnginePool)
        pool.closed=False;pool.failed=threading.Event();pool.available=queue.LifoQueue()
        pool.scanners=[];pool.name='Synthetic'
        return pool

    def test_empty_pool_observes_deadline(self):
        pool=self.empty_pool()
        with self.assertRaises(DeadlineReached):
            pool._run_with_scanner(ScanDeadline(time.monotonic()-.1),lambda _:None)

    def test_failed_pool_never_blocks_on_empty_queue(self):
        pool=self.empty_pool();pool.failed.set()
        with self.assertRaises(ReviewError):
            pool._run_with_scanner(ScanDeadline(time.monotonic()+10),lambda _:None)

    def test_replacement_failure_fails_waiters_cleanly(self):
        pool=self.empty_pool();pool.config=Mock();pool.factory=None
        scanner=Mock();pool.scanners=[scanner];pool.available.put(scanner)
        def fail(_):raise chess.engine.EngineTerminatedError('Synthetic failure')
        with patch('fairplay_analysis.EngineScanner',side_effect=OSError('Synthetic capacity error')):
            with self.assertRaises(chess.engine.EngineError):
                pool._run_with_scanner(ScanDeadline(time.monotonic()+10),fail)
        self.assertTrue(pool.failed.is_set())
        with self.assertRaises(ReviewError):
            pool._run_with_scanner(ScanDeadline(time.monotonic()+10),lambda _:None)



class PartialBatchTests(unittest.TestCase):
    def test_deadline_preserves_complete_game_and_excludes_partial_game(self):
        from concurrent.futures import Future
        from types import SimpleNamespace
        from fairplay_analysis import run_position_batch
        one=SimpleNamespace();two=SimpleNamespace()
        decisions=[object(),object(),object(),object()]
        work=[(one,decisions[0]),(one,decisions[1]),(two,decisions[2]),(two,decisions[3])]
        class InlineExecutor:
            def submit(self,fn,game,decision,*args):
                future=Future()
                if decision is decisions[3]:future.set_exception(DeadlineReached())
                else:future.set_result(True)
                return future
        done,partial=run_position_batch(InlineExecutor(),SimpleNamespace(run_decision=None),
            work,24000,None,lambda _:None,'Fast engine scan')
        self.assertTrue(partial)
        self.assertIn(id(one),done)
        self.assertNotIn(id(two),done)

    def test_large_cancelled_batch_has_linear_cleanup_cost(self):
        from concurrent.futures import Future, CancelledError
        from types import SimpleNamespace
        from fairplay_analysis import run_position_batch
        pending=[]
        class CountedFuture(Future):
            def __init__(self):
                super().__init__();self.cancel_calls=0
            def cancel(self):
                self.cancel_calls+=1
                return super().cancel()
        class InlineExecutor:
            def submit(self,*args):
                future=CountedFuture()
                future.set_exception(DeadlineReached() if not pending else CancelledError())
                pending.append(future)
                return future
        done,partial=run_position_batch(InlineExecutor(),SimpleNamespace(run_decision=None),
            [(SimpleNamespace(),object()) for _ in range(200)],24000,None,lambda _:None,'Fast engine scan')
        self.assertTrue(partial);self.assertEqual(done,set())
        self.assertLessEqual(sum(f.cancel_calls for f in pending),2*len(pending))

    def test_completed_batch_is_not_marked_partial(self):
        from concurrent.futures import Future
        from types import SimpleNamespace
        from fairplay_analysis import run_position_batch
        game=SimpleNamespace()
        class InlineExecutor:
            def submit(self,*args):
                future=Future();future.set_result(True);return future
        done,partial=run_position_batch(InlineExecutor(),SimpleNamespace(run_decision=None),
            [(game,object())],24000,None,lambda _:None,'Fast engine scan')
        self.assertFalse(partial);self.assertEqual(done,{id(game)})



class CoherentRoundTests(unittest.TestCase):
    def rows(self,depth=8):
        return [f'info depth {depth} multipv {i+1} score cp {score} pv {move}'
                for i,(score,move) in enumerate([(50,'e2e4'),(20,'d2d4'),(-40,'g1f3')])]
    def collect(self,lines,count=3):
        from fairplay_engine import CoherentCandidates
        c=CoherentCandidates(count)
        for line in lines:
            parsed=CompactNodeEngine.parse_info(line,chess.WHITE)
            if parsed:c.add(*parsed)
        return c.result()
    def test_partial_new_iteration_does_not_mix_depths(self):
        lines=self.rows()+['info depth 9 multipv 1 score cp -10 pv d2d4']
        rows=self.collect(lines)
        self.assertEqual([r['depth'] for r in rows],[8,8,8])
        self.assertEqual([r['score'].relative.score() for r in rows],[50,20,-40])
    def test_new_complete_iteration_replaces_previous(self):
        rows=self.collect(self.rows()+self.rows(9))
        self.assertEqual([r['depth'] for r in rows],[9,9,9])
    def test_bounds_and_unsorted_rounds_do_not_become_exact_evidence(self):
        for last in ('info depth 9 multipv 3 score cp -40 lowerbound pv g1f3',
                     'info depth 9 multipv 3 score cp 100 pv g1f3'):
            rows=self.collect(self.rows()+self.rows(9)[:2]+[last])
            self.assertEqual([r['depth'] for r in rows],[8,8,8])
    def test_duplicate_candidates_are_rejected(self):
        with self.assertRaises(chess.engine.EngineError):
            self.collect(self.rows()[:2]+['info depth 8 multipv 3 score cp -40 pv e2e4'])
    def test_root_search_retains_last_exact_score(self):
        rows=self.collect(['info depth 8 score cp 20 pv e2e4',
                           'info depth 9 score cp 80 lowerbound pv e2e4'],count=1)
        self.assertEqual(rows[0]['score'].relative.score(),20)


if __name__=='__main__':unittest.main()
