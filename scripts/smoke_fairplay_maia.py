"""Verify real local policy inference, legality, repeatability and Black POV."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chess
from fairplay_maia import LocalPolicyWorker


def main():
    worker=LocalPolicyWorker(os.environ['FAIRPLAY_MAIA_CHECKPOINT'])
    try:
        board=chess.Board();history=[board.fen()]
        board.push_uci('e2e4');history.append(board.fen())
        items=[{'history':[chess.STARTING_FEN],'rating':1500,'opponent_rating':1600},
               {'history':history,'rating':1600,'opponent_rating':1500}]
        first=worker.predict(items)
        assert first==worker.predict(items),'Local policy inference was not deterministic'
        for item,row in zip(items,first):
            legal={m.uci() for m in chess.Board(item['history'][-1]).legal_moves}
            assert set(row)==legal and abs(sum(row.values())-1)<1e-5
            assert all(0<=p<=1 for p in row.values())
        low=worker.predict([dict(items[0],rating=800)])[0]
        high=worker.predict([dict(items[0],rating=2600)])[0]
        assert sum(abs(low[m]-high[m]) for m in low)>1e-4, 'Rating conditioning is inactive'
        print('Local Maia policy smoke passed: legal moves, deterministic repeat, Black orientation.')
    finally:worker.close()

if __name__=='__main__':main()
