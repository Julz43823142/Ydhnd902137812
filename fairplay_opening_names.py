"""Offline descriptive opening families from the pinned Lichess CC0 reference."""
import json
from pathlib import Path
from functools import lru_cache

@lru_cache(maxsize=1)
def reference():
    try:
        data=json.loads((Path(__file__).parent/'assets/fairplay/opening-families.json').read_text())
        return {tuple(moves):name for moves,name in data['lines']}
    except (OSError,ValueError,KeyError):return {}

def opening_family(game):
    moves=tuple((getattr(game,'moves',None) or [])[:40]);known=reference()
    for length in range(len(moves),1,-1):
        if moves[:length] in known:return known[moves[:length]]
    return 'Unknown'


def opening_pattern(game):
    """First five own SAN moves + opponent's first move; descriptive, not position identity."""
    import chess
    from types import SimpleNamespace
    color=getattr(game,'color',None)
    if type(color) is not bool:return 'Unknown'
    board=chess.Board();own=[];opponent=None;prefix=[]
    for value in (getattr(game,'moves',None) or [])[:10]:
        try:
            move=chess.Move.from_uci(value)
            if move not in board.legal_moves:return 'Unknown'
            san=board.san(move)
        except (ValueError,TypeError):return 'Unknown'
        if board.turn==color:own.append(san)
        elif opponent is None:opponent=san
        prefix.append(value);board.push(move)
    family=opening_family(SimpleNamespace(moves=prefix))
    if len(own)<5:return family+' (short game)'
    # Keep full move pattern; shorten only the secondary family name for mobile legends.
    name=family if len(family)<=22 else family[:21]+'…'
    return f"{name} · vs {opponent or '?'} · "+' '.join(own)
