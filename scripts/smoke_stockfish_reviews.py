"""Real engine + synthetic PubAPI/PGN; no Discord, network accounts or state."""
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
    game.headers.update(White=TARGET,Black='synthetic-opponent',Result='1/2-1/2')
    board,node=chess.Board(),game
    # A fixed balanced synthetic continuation rather than random losing moves.
    # Deeper engines correctly excluded most of the old random game's positions,
    # leaving too little evidence to exercise the complete summary pipeline.
    moves = (
        'd4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O Nf3 h6 Bh4 b6 Bd3 Bb7 O-O Nbd7 Rc1 c5 '
        'h3 cxd4 exd4 Re8 Ne5 dxc4 Bb1 Nxe5 dxe5 Nd7 Bg3 Rc8 Qc2 Nf8 Rcd1 Bd5 '
        'Rd4 Qc7 Nb5 Qd7 Qa4 Bc5 Rdd1 f5 Qa6 Qf7 Nd6 Qc7 Rxd5 exd5 Nxc8 f4 '
        'Bh2 Qxc8 Qxa7 Re7 Qa4 Ne6 Bf5 Qf8 Bg6 Nd4 Re1 Kh8 Kh1 b5 Qd1 b4 '
        'e6 c3 Bf7 g5 h4 Nxe6 Rxe6 Rxe6 Bxe6 cxb2 Qb1 Qg7'
    )
    for san in moves.split():
        move=board.parse_san(san);node=node.add_variation(move);board.push(move)
    return {'uuid':'synthetic-engine-smoke','end_time':1790812800,
            'rules':'chess','rated':True,'time_class':'blitz','time_control':'300+0','pgn':str(game),
            'white':{'username':TARGET,'rating':1400,'result':'agreed'},
            'black':{'username':'synthetic-opponent','rating':1400,'result':'agreed'}}


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
