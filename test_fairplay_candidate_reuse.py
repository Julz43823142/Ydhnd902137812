"""Synthetic exact candidate reuse; every played move remains separately scored."""
from types import SimpleNamespace
import time
import unittest
import chess
import chess.engine
from test_fairplay import FakeEngine
from fairplay_analysis import EngineScanner, SharedEnginePool
from fairplay_config import CONFIG


class DepthEngine(FakeEngine):
    def analyse(self, board, limit, multipv=None, root_moves=None):
        rows=super().analyse(board,limit,multipv=multipv,root_moves=root_moves)
        for row in rows if isinstance(rows,list) else [rows]:
            row.update(depth=limit.depth or 10,nodes=limit.nodes or 350000)
        return rows


def position(move, *, phase='middlegame', fen=chess.STARTING_FEN):
    board=chess.Board(fen);played=chess.Move.from_uci(move)
    decision=SimpleNamespace(fen=fen,move=move,useful=True,forced=False,trivial_kind=None,
        phase=phase,legal=board.legal_moves.count(),capture=board.is_capture(played),
        gives_check=board.gives_check(played),check=board.is_check(),metrics={},fast_engine={})
    return SimpleNamespace(color=board.turn),decision


class CandidateReuse(unittest.TestCase):
    def setUp(self):
        self.engine=DepthEngine()
        self.pool=SharedEnginePool(size=1,factory=lambda:self.engine)
        self.addCleanup(self.pool.close)
        self.moves=[m.uci() for m in chess.Board().legal_moves]
        self.deadline=time.monotonic()+30
        self.depth=chess.engine.Limit(depth=18)

    def run_move(self,index,*,phase='middlegame',nodes=None):
        g,d=position(self.moves[index],phase=phase)
        self.pool.run_decision(g,d,self.depth if nodes is None else nodes,self.deadline)
        return d

    def test_different_played_moves_reuse_candidates_without_reusing_quality(self):
        first=self.run_move(0);second=self.run_move(1)
        self.assertEqual(len(self.engine.calls),1)
        self.assertEqual(first.metrics['rank'],1)
        self.assertEqual(second.metrics['rank'],2)
        self.assertNotEqual(first.metrics['cpl'],second.metrics['cpl'])
        self.assertEqual(self.pool.scanners[0].profile['candidate_cache_hits'],1)

    def test_move_outside_candidates_still_gets_its_own_root_search(self):
        self.run_move(0);outside=self.run_move(-1)
        self.assertEqual(len(self.engine.calls),2)
        self.assertIsNone(outside.metrics['rank'])
        self.assertEqual(self.pool.scanners[0].profile['root_searches'],1)

    def test_opening_context_is_recomputed_even_when_candidates_are_shared(self):
        mid=self.run_move(0);book=self.run_move(1,phase='opening')
        self.assertTrue(mid.metrics['useful'])
        self.assertFalse(book.metrics['useful'])
        self.assertEqual(len(self.engine.calls),1)

    def test_cache_does_not_mix_budgets_or_multipv(self):
        self.run_move(0,nodes=CONFIG.fast_nodes)
        self.run_move(1)
        g,d=position(self.moves[2])
        self.pool.run_decision(g,d,CONFIG.fast_nodes,self.deadline,fast_multipv_override=3)
        self.assertEqual(len(self.engine.calls),3)

    def test_different_positions_do_not_share_candidates(self):
        self.run_move(0)
        board=chess.Board()
        board.push_uci('e2e4')
        g,d=position(next(iter(board.legal_moves)).uci(),fen=board.fen())
        self.pool.run_decision(g,d,self.depth,self.deadline)
        self.assertEqual(len(self.engine.calls),2)

    def test_disabled_position_cache_disables_candidate_reuse_too(self):
        self.pool.position_cache=None
        self.run_move(0);self.run_move(1)
        self.assertEqual(len(self.engine.calls),2)

    def test_full_metric_results_match_fresh_sequential_search(self):
        baseline=EngineScanner(self.deadline,factory=DepthEngine)
        self.addCleanup(baseline.close)
        for index,phase in ((0,'middlegame'),(1,'opening'),(-1,'middlegame')):
            g,d=position(self.moves[index],phase=phase)
            baseline.analyse_decision(g,d,self.depth)
            measured=self.run_move(index,phase=phase)
            self.assertEqual(measured.metrics,d.metrics)

    def test_cached_lines_are_isolated_from_mutation(self):
        first=self.run_move(0)
        first.metrics['candidate_cp'][0]=-9999
        second=self.run_move(1)
        self.assertEqual(second.metrics['before_cp'],200)

    def test_inexact_candidates_are_not_reused(self):
        original=self.engine.analyse
        def bounded(*args,**kwargs):
            rows=original(*args,**kwargs)
            for row in rows if isinstance(rows,list) else [rows]:row['upperbound']=True
            return rows
        self.engine.analyse=bounded
        first=self.run_move(0);second=self.run_move(1)
        self.assertTrue(first.metrics['search_inconsistent'])
        self.assertTrue(second.metrics['search_inconsistent'])
        self.assertEqual(len(self.engine.calls),2)


if __name__=='__main__':unittest.main()
