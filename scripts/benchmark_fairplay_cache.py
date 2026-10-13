"""Whole-review cache benchmark with synthetic games; no network/account data.

Run manually with a prepared Stockfish 19. Four cold-pool depth-18 reviews can
be expensive. The one-worker fixture isolates whole-game fallback reuse; it
cannot predict production queues, Maia deployment or account-specific overlap.
"""
import copy
import io
import json
import os
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chess
import chess.pgn
from fairplay_analysis import SharedEnginePool, _game_cache, review
from fairplay_opening import opening_reference
from scripts.smoke_stockfish_reviews import FixtureAPI, TARGET, fixture


def synthetic_rows():
    base=fixture()
    source=chess.pgn.read_game(io.StringIO(base['pgn']))
    common=list(source.mainline_moves())[:24]
    rows=[]
    for index in range(3):
        board=chess.Board();game=chess.pgn.Game()
        game.headers.update(White=TARGET,Black='synthetic-opponent',Result='1/2-1/2')
        node=game
        for move in common:
            node=node.add_variation(move);board.push(move)
        for ply in range(16):
            moves=sorted(board.legal_moves,key=lambda m:m.uci())
            if not moves:break
            move=moves[(index*7+ply*3)%len(moves)]
            node=node.add_variation(move);board.push(move)
        row=copy.deepcopy(base)
        row.update(uuid=f'cache-benchmark-{index}',end_time=1790812800+index*600,pgn=str(game))
        rows.append(row)
    return rows


class SyntheticAPI(FixtureAPI):
    def get(self,target,suffix='',**kwargs):
        if suffix and not suffix.endswith('/archives'):
            return {'games':synthetic_rows()}
        return super().get(target,suffix,**kwargs)


def analytical_evidence(result):
    return (result.priority,result.totals,result.classes,result.deep_coverage,
            [(g.identity,g.metrics,g.fast_metrics,
              [(d.move,d.metrics,d.fast_engine) for d in g.decisions])
             for g in result.games])


def main():
    os.environ['FAIRPLAY_FULL_DEPTH18']='1'
    os.environ['FAIRPLAY_V21']='0'  # no mandatory trained-policy deployment
    os.environ['FAIRPLAY_FULL_SCAN_DEADLINE_SECONDS']='600'
    opening_reference()  # same opening initialization for all measurements
    reference=None;measurements={'uncached':[],'cached':[]}
    for mode in ('uncached','cached','cached','uncached'):
        _game_cache.clear()
        pool=SharedEnginePool(size=1)
        if mode=='uncached':pool.position_cache=None
        try:
            started=time.perf_counter()
            result=review(TARGET,lambda _:None,api_factory=SyntheticAPI,engine_pool=pool)
            elapsed=time.perf_counter()-started
            evidence=analytical_evidence(result)
            if reference is None:reference=evidence
            assert evidence==reference,'Caching changed complete analytical results'
            measurements[mode].append(elapsed)
            print(json.dumps({'mode':mode,'seconds':round(elapsed,3),
                              'runtime':result.diagnostics['runtime'],
                              'analytical_parity':True}),flush=True)
        finally:
            pool.close();_game_cache.clear()
    medians={key:statistics.median(value) for key,value in measurements.items()}
    print(json.dumps({'median_seconds':medians,
                      'elapsed_reduction_percent':100*(1-medians['cached']/medians['uncached']),
                      'scope':'synthetic one-worker review, not production speedup'}))


if __name__=='__main__':main()
