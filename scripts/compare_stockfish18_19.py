"""Like-for-like Stockfish 18 vs 19 depth-18 throughput on the CI CPU.

Both official sf_18 and sf_19 are compiled with ARCH=x86-64-avx2 and no
profile-guided optimization. The same six synthetic positions are alternated
between engines to reduce ordering artifacts. Not a production change.
"""
import json
import statistics
import sys
import time
from pathlib import Path
import chess
import chess.engine

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fairplay_config import CONFIG
from fairplay_data import parse_game
from scripts.smoke_stockfish_reviews import TARGET, fixture


def main():
    if len(sys.argv)!=3:raise SystemExit('Usage: compare_stockfish18_19.py SF18 SF19')
    game=parse_game(fixture(),TARGET)
    decisions=[d for d in game.decisions if d.useful]
    positions=[decisions[round(i*(len(decisions)-1)/5)] for i in range(6)]
    engines={}
    try:
        for version,path in zip(('sf18','sf19'),sys.argv[1:]):
            e=chess.engine.SimpleEngine.popen_uci(path,timeout=90)
            if f'Stockfish {version[2:]}' not in e.id.get('name',''):
                raise RuntimeError(f'Wrong Stockfish version in {path}: {e.id}')
            e.configure({'Threads':1,'Hash':CONFIG.hash_mb,'UCI_LimitStrength':False})
            engines[version]=e
        rows=[]
        for index,decision in enumerate(positions):
            board=chess.Board(decision.fen)
            versions=('sf18','sf19') if index%2==0 else ('sf19','sf18')
            row={'ply':decision.ply}
            for version in versions:
                engine=engines[version]
                engine.configure({'Clear Hash':None})
                started=time.monotonic()
                info=engine.analyse(board,chess.engine.Limit(depth=18),multipv=5)
                row[version]={'seconds':round(time.monotonic()-started,4),
                              'nodes':info[0].get('nodes'),'depth':info[0].get('depth'),
                              'best':info[0]['pv'][0].uci()}
            rows.append(row)
            print('ENGINE_VERSION_POSITION '+json.dumps(row,sort_keys=True),flush=True)
        a=sum(r['sf18']['seconds'] for r in rows)
        b=sum(r['sf19']['seconds'] for r in rows)
        result={'positions':len(rows),'sf18_total_s':round(a,3),'sf19_total_s':round(b,3),
                'sf18_median_s':statistics.median(r['sf18']['seconds'] for r in rows),
                'sf19_median_s':statistics.median(r['sf19']['seconds'] for r in rows),
                'sf18_over_sf19_time_ratio':round(a/b,3),'identical_best_moves':sum(r['sf18']['best']==r['sf19']['best'] for r in rows)}
        print('ENGINE_VERSION_COMPARISON '+json.dumps(result,sort_keys=True),flush=True)
    finally:
        for e in engines.values():
            try:e.quit()
            except Exception:e.close()


if __name__=='__main__':main()
