"""Bounded, process-local optimizations; no case data on disk or in logs."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import chess

@lru_cache(maxsize=4096)
def legal_uci(fen):
    return frozenset(move.uci() for move in chess.Board(fen).legal_moves)

_summary_cache = ContextVar('fairplay_final_policy_summaries', default=None)

@contextmanager
def final_summary_scope():
    # Enabled only after all engine/policy mutations have finished. Never
    # memoize across the fast/deep boundary or across different scans.
    token = _summary_cache.set({})
    try:
        yield
    finally:
        _summary_cache.reset(token)

def summary_cache():
    return _summary_cache.get()

class BoardPreparationCache:
    """Per-model token/mask reuse; probabilities are still inferred afresh."""
    def __init__(self, tokenize, mask, capacity=2048):
        self.tokenize, self.mask, self.capacity = tokenize, mask, capacity
        self.entries = OrderedDict()
    def get(self, fen):
        if fen not in self.entries:
            board = chess.Board(fen)
            self.entries[fen] = (self.tokenize(board), self.mask(board))
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
        self.entries.move_to_end(fen)
        return self.entries[fen]


def encode_policy_request(items):
    """Deduplicate repeated FEN strings in a local IPC batch, not history."""
    boards=[];indices={};positions=[]
    for item in items:
        history=[]
        for fen in item['history']:
            if fen not in indices:
                indices[fen]=len(boards);boards.append(fen)
            history.append(indices[fen])
        positions.append({**item,'history':history})
    return {'positions':positions,'boards':boards}


def decode_policy_request(request):
    if 'boards' not in request:return request['positions']  # legacy protocol
    boards=request['boards'];positions=request['positions']
    if (not isinstance(boards,list) or not 0<len(boards)<=6400
            or any(not isinstance(fen,str) or len(fen)>128 for fen in boards)
            or not isinstance(positions,list) or len(positions)>800):
        raise ValueError('Invalid local policy batch')
    rows=[]
    for item in positions:
        history=item.get('history')
        if (not isinstance(history,list) or not 1<=len(history)<=8
                or any(type(i) is not int or not 0<=i<len(boards) for i in history)):
            raise ValueError('Invalid local board history')
        rows.append({**item,'history':[boards[i] for i in history]})
    return rows
