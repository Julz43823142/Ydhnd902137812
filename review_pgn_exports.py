"""Portable PGN exports for ordinary chess game reviews.

Source annotations are not engine evidence. Review labels are supplied separately,
and clocks are copied only from genuine per-move [%clk] source comments.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from enum import Enum

import chess.pgn


class PgnExportMode(str, Enum):
    MOVES_ONLY = "moves_only"
    WITH_CLOCKS = "with_clocks"
    CLOCKS_AND_ANALYSIS = "clocks_and_analysis"


LABEL_TEXT = {
    "brilliant": "Brilliant",
    "great": "Great",
    "book": "Book",
    "best": "Best",
    "excellent": "Excellent",
    "good": "Good",
    "inaccuracy": "Inaccuracy",
    "mistake": "Mistake",
    "miss": "Miss",
    "blunder": "Blunder",
}


def _validated_labels(labels: Mapping[int, str] | None, count: int) -> dict[int, str]:
    clean = {}
    for ply, label in (labels or {}).items():
        if type(ply) is not int or not 1 <= ply <= count:
            raise ValueError("Review label has an invalid mainline ply.")
        if not isinstance(label, str):
            raise ValueError("Review label must be a string.")
        name = label.strip().casefold()
        if name not in LABEL_TEXT:
            raise ValueError("Unsupported review move label.")
        clean[ply] = LABEL_TEXT[name]
    return clean


def has_move_clocks(game: chess.pgn.Game) -> bool:
    """Return whether any original mainline move has a genuine clock."""
    return any(node.clock() is not None for node in game.mainline())


def export_review_pgn(
    game: chess.pgn.Game,
    mode: PgnExportMode | str,
    *,
    labels: Mapping[int, str] | None = None,
) -> str:
    """Build a clean mainline PGN without changing the source game.

    Labels are keyed by 1-based ply, including Black-to-move custom starts.
    """
    mode = PgnExportMode(mode)
    if game.errors:
        raise ValueError("Cannot export a game with PGN parsing errors.")
    nodes = list(game.mainline())
    clean_labels = _validated_labels(labels, len(nodes))
    result = game.headers.get("Result", "*")
    if result not in {"*", "1-0", "0-1", "1/2-1/2"}:
        raise ValueError("Invalid PGN result.")

    exported = chess.pgn.Game()
    exported.headers.clear()
    if mode is not PgnExportMode.MOVES_ONLY:
        exported.headers.update(
            (key, value) for key, value in game.headers.items()
            if key != "Annotator"
        )
    exported.setup(game.board())
    exported.headers["Result"] = result

    board = game.board()
    destination = exported
    for ply, node in enumerate(nodes, start=1):
        if not board.is_legal(node.move):
            raise ValueError("The PGN contains an illegal mainline move.")
        destination = destination.add_variation(node.move)
        board.push(node.move)
        if mode is not PgnExportMode.MOVES_ONLY:
            clock = node.clock()
            if clock is not None and math.isfinite(clock) and clock >= 0:
                destination.set_clock(clock)
        if mode is PgnExportMode.CLOCKS_AND_ANALYSIS and ply in clean_labels:
            destination.comment = clean_labels[ply]

    needs_position_headers = any(
        key in exported.headers for key in ("SetUp", "FEN", "Variant")
    )
    exporter = chess.pgn.StringExporter(
        headers=mode is not PgnExportMode.MOVES_ONLY or needs_position_headers,
        variations=False,
        comments=mode is not PgnExportMode.MOVES_ONLY,
        columns=None,
    )
    return exported.accept(exporter).strip()
