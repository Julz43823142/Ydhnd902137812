"""Offline public theory and leave-one-game-out personal repertoire context.

The CC0 reference contains generic opening lines, never player games. Familiarity
is supporting context, not evidence of wrongdoing. Unknown theory is not
necessarily difficult: the normal position/forced-move filters still apply.
"""
import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import chess
from fairplay_config import CONFIG


def position_key(board):
    return ' '.join(board.fen().split()[:4])


@lru_cache(maxsize=1)
def opening_reference():
    reference = defaultdict(set)
    try:
        data = json.loads((Path(__file__).parent/'assets/fairplay/opening-lines.json').read_text())
        for line in data['lines']:
            board = chess.Board()
            for text in line.split()[:CONFIG.opening_max_plies]:
                move = chess.Move.from_uci(text)
                if move not in board.legal_moves:break
                reference[position_key(board)].add(text)
                board.push(move)
    except (OSError, ValueError, KeyError, TypeError):
        return {}  # no download during review; bounded conservative fallback
    return dict(reference)


def book_status(board, move, ply, config=CONFIG):
    reference = opening_reference()
    known = ply<=config.opening_max_plies and move.uci() in reference.get(position_key(board),())
    # Only the first three move pairs are universally protected. Off-book move
    # seven can now enter normal analysis; known theory can remain protected later.
    fallback = not reference and ply<=config.opening_plies
    book = bool(known or ply<=config.opening_min_plies or fallback)
    return {'book':book,'source':'reference' if known else 'fallback' if fallback else 'early' if book else 'off-book',
            'reference_available':bool(reference)}


def repertoire(games):
    counts=Counter(); game_keys={}; result={}
    for game in games:
        keys=[]
        for d in game.decisions:
            if d.ply>60:continue
            key=(game.color,' '.join(d.fen.split()[:4]),d.move)
            keys.append(key)
        game_keys[game.identity]=set(keys)
        counts.update(set(keys))
    for color in (True,False):
        group=[g for g in games if g.color==color]
        novelty=[];transitions=[]
        for game in group:
            moves=[d for d in game.decisions if 6<d.ply<=30]
            familiar=sum(counts[(color,' '.join(d.fen.split()[:4]),d.move)]>1 for d in moves)
            novelty.append(1-familiar/len(moves) if moves else 0)
            transitions.append(next((d.fullmove for d in game.decisions if not d.opening.get('book',d.phase=='opening')),None))
        result['White' if color else 'Black']={'games':len(group),'novelty_by_game':novelty,
            'off_book_moves':transitions,'sufficient':len(group)>=10}
    return result
