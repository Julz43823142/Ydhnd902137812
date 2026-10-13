"""Real Stockfish counterfactual cache parity with synthetic policies only."""
import copy
from pathlib import Path
import sys
import time
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chess
import chess.engine
from fairplay_analysis import EngineScanner, SharedEnginePool
from fairplay_config import CONFIG
from fairplay_policy import search_alternatives
from scripts.smoke_fairplay_candidate_cache import position


def decision(fen,root):
    _,d=position('a2a4',fen)
    d.metrics={'useful':True,'competitive':True,'candidates':[]}
    # These synthetic probabilities exercise root selection, not a human model.
    d.human_policy={root:.95,'h2h4':.05}
    return d


def main():
    deadline=time.monotonic()+300
    baseline=EngineScanner(deadline)
    pool=SharedEnginePool(size=1)
    try:
        board=chess.Board()
        for san in ('d4','d5','c4','e6','Nc3','Nf6'):board.push_san(san)
        tasks=[(budget,decision(board.fen(),root))
               for budget in (CONFIG.fast_nodes,chess.engine.Limit(depth=18))
               for root in ('a2a4','g1f3','a2a4','g1f3')]
        times=[];reference=None
        for cached in (False,True):
            results=[];started=time.monotonic()
            for budget,source in tasks:
                d=copy.deepcopy(source)
                result=(pool._run_cached(deadline,lambda scanner:search_alternatives(scanner,d,budget))
                        if cached else search_alternatives(baseline,d,budget))
                results.append(result)
            times.append(time.monotonic()-started)
            if reference is None:reference=results
            else:assert results==reference,'Root reuse changed counterfactual scores or contracts'
        hits=pool.scanners[0].profile['policy_root_cache_hits']
        assert hits==4,'Repeated policy alternatives were not reused'
        # Played-root provenance must also preserve the same objective score.
        game,played=position('a2a4',board.fen())
        pool.run_decision(game,played,chess.engine.Limit(depth=18),deadline)
        assert pool.scanners[0].profile['root_cache_hits']>=1
        fresh=search_alternatives(baseline,decision(board.fen(),'a2a4'),chess.engine.Limit(depth=18))
        assert played.metrics['actual_cp']==fresh['scores']['a2a4']
        print(f'Exact policy-root parity: {len(tasks)} queries, {hits} reused; '
              f'cold {times[0]:.3f}s, reuse {times[1]:.3f}s '
              '(synthetic duplicate queries, not full-review speed).')
    finally:
        baseline.close();pool.close()


if __name__=='__main__':main()
