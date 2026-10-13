"""Real Stockfish parity for complete synthetic games split/deduplicated remotely.

No network or real accounts. Each exact depth-18 query remains reproducible.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chess.engine
from fairplay_analysis import SharedEnginePool,run_position_batch,summarize
from fairplay_config import CONFIG
from fairplay_data import parse_game
from fairplay_distributed import fragment_work,reassemble_fragments


def main():
    rows=[]
    for index in range(2):
        row={'url':f'https://www.chess.com/game/live/{800000000+index}',
             'pgn':'[White "synthetic-player"]\n[Black "synthetic-opponent"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 1-0',
             'rules':'chess','time_class':'blitz','time_control':'300','rated':True,'end_time':1700000000+index,
             'white':{'username':'synthetic-player','rating':1500,'result':'win'},
             'black':{'username':'synthetic-opponent','rating':1500,'result':'resigned'}}
        game=parse_game(row,'synthetic-player',allow_short=True)
        assert game is not None
        rows.append(game)
    limit=chess.engine.Limit(depth=18);deadline=time.monotonic()+300
    baseline=copy.deepcopy(rows)
    pool=SharedEnginePool(CONFIG,size=2)
    pool.position_cache=None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            ids,interrupted=run_position_batch(executor,pool,[(g,d) for g in baseline for d in g.decisions],limit,deadline,lambda _:None,'Synthetic depth')
        assert not interrupted and len(ids)==2
        for g in baseline:summarize(g);g.deep=True
    finally:pool.close()
    parts,mapping,sources,aliases=fragment_work(rows,size=2,deduplicate=True)
    pool=SharedEnginePool(CONFIG,size=2)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            ids,interrupted=run_position_batch(executor,pool,[(g,d) for g in parts for d in g.decisions],limit,deadline,lambda _:None,'Synthetic depth')
        assert not interrupted and len(ids)==len(parts)
        for g in parts:g.deep=True
        rebuilt=reassemble_fragments({'fragments':mapping,'sources':sources,'aliases':aliases},{g.identity:g for g in parts})
        for original in baseline:
            restored=rebuilt[original.identity];summarize(restored)
            assert [d.metrics for d in original.decisions]==[d.metrics for d in restored.decisions]
            assert original.metrics==restored.metrics
        print(f'v48 real Stockfish parity passed: {len(rows)} complete synthetic games; {len(aliases)} exact duplicate decisions reused; all decision and game metrics identical.')
    finally:pool.close()

if __name__=='__main__':main()
