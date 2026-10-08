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


if __name__=='__main__':unittest.main()
