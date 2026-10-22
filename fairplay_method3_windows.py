"""Class-first chronological batches for the bounded Method 3 search.

Games in a different rating pool must not fragment a subject's Rapid/Blitz/
Bullet sequence. Batches are an execution detail, not ranked selections.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    block_index: int
    candidates: list
    context: list
    context_start: int  # index in the original complete chronological history
    boundary: int | None = None  # global index of the first game after the boundary


def segments(games, block_size=200):
    """Return class-local blocks, then bridges, plus the number of blocks.

    Each block carries up to one strictly earlier block as Accuracy context.
    Bridges span half a block on each side; downstream candidate filtering
    retains only windows crossing that boundary. Every period preserves actual
    chronological class order. Rated and unrated games share the same sequence.
    """
    positions = {game.identity: index for index, game in enumerate(games)}
    blocks, bridges = [], []
    for kind in ('rapid', 'blitz', 'bullet'):
        rows = [game for game in games if game.time_class == kind]
        class_block_offset = len(blocks)
        for start in range(0, len(rows), block_size):
            end = min(len(rows), start + block_size)
            context = rows[max(0, start - block_size):end]
            blocks.append(Segment(len(blocks), rows[start:end], context,
                                  positions[context[0].identity]))
        radius = block_size // 2
        for boundary in range(block_size, len(rows), block_size):
            start, end = boundary - radius, min(len(rows), boundary + radius)
            context = rows[max(0, start - block_size):end]
            bridges.append(Segment(class_block_offset + boundary // block_size - 1,
                                   rows[start:end], context,
                                   positions[context[0].identity],
                                   positions[rows[boundary].identity]))
    return blocks + bridges, len(blocks)
