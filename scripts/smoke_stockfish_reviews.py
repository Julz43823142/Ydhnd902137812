"""Real engine + synthetic PubAPI/PGN; no Discord, network accounts or state."""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess
import chess.pgn
import chess_play
from fairplay_analysis import review

TARGET = 'engine-smoke-fixture'


def fixture():
    game=chess.pgn.Game()
    game.headers.update(White=TARGET,Black='synthetic-opponent',Result='1-0')
    board,node=chess.Board(),game
    rng=random.Random(51)
    for _ in range(90):
        if board.is_game_over():break
        move=rng.choice(list(board.legal_moves));node=node.add_variation(move);board.push(move)
    return {'uuid':'synthetic-engine-smoke','end_time':1790812800,
            'rules':'chess','time_class':'blitz','time_control':'300+0','pgn':str(game),
            'white':{'username':TARGET,'rating':1400,'result':'win'},
            'black':{'username':'synthetic-opponent','rating':1400,'result':'resigned'}}


class FixtureAPI:
    def __init__(self,deadline):self.deadline=deadline;self.closed=False
    def get(self,target,suffix='',**kwargs):
        if not suffix:return {'username':TARGET}
        if suffix.endswith('/archives'):
            return {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']}
        return {'games':[fixture()]}
    def close(self):self.closed=True


def smoke():
    started=time.monotonic()
    result=review(TARGET,lambda stage:None,api_factory=FixtureAPI)
    if (not result.engine.startswith('Stockfish 19') or not result.totals['decisions']
            or result.deep_coverage['games']!=1 or not result.games[0].fast_metrics):
        raise RuntimeError('The real two-pass engine review did not complete.')
    print(f'Real Stockfish two-pass synthetic review passed: {result.totals["decisions"]} decisions, {time.monotonic()-started:.2f}s.')
    try:
        game=chess_play.analyse_game_moves(['e4','e5','Nf3','Nc6','Bb5','a6'])
        if game['analysed_plies']!=6 or any(row['loss_cp']!=0 for row in game['moves'] if row['played']==row['best']):
            raise RuntimeError('Normal Game Review smoke test failed.')
        print('Real Stockfish normal Game Review passed: 6 plies.')
    finally:chess_play._close_analysis_engine()


if __name__=='__main__':smoke()
