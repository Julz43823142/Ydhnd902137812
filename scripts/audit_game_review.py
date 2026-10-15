"""Private, local Chess.com-vs-SharkBot review audit.

Read the owner's own PGN from disk; never download arbitrary PGN-header URLs,
write player identities to reports or add a real match to committed fixtures.

Example:
  python scripts/audit_game_review.py --pgn /private/game.pgn \\
      --reference-white 78.1 --reference-black 84.0 \\
      --previous-white 65.2 --previous-black 87.1
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chess.pgn

from game_review_service import game_review_service


def parse_pgn_file(path):
    raw = Path(path).read_text(encoding="utf-8-sig")
    game = chess.pgn.read_game(io.StringIO(raw))
    if game is None or game.errors:
        raise ValueError("Invalid source PGN; no analysis was performed.")
    board = game.board()
    if not board.is_valid() or board.uci_variant != "chess":
        raise ValueError("Only standard chess and Chess960 are supported.")
    chess960 = bool(board.chess960)
    start_fen = board.fen()
    moves = []
    for move in game.mainline_moves():
        moves.append(board.san(move))
        board.push(move)
    if not moves:
        raise ValueError("PGN contains no mainline moves.")
    return moves, start_fen, chess960


def audit_pgn(path, reference_white=None, reference_black=None,
              previous_white=None, previous_black=None):
    moves, fen, chess960 = parse_pgn_file(path)
    started = time.perf_counter()
    result = game_review_service.analyse(moves, None, fen, chess960)
    elapsed = time.perf_counter() - started
    white = float(result["white"]["accuracy"])
    black = float(result["black"]["accuracy"])
    summary = {
        "engine": result.get("engine"),
        "depth": result.get("analysis_depth"),
        "version": result.get("analysis_version"),
        "accuracy_model": result.get("accuracy_model"),
        "white_accuracy": white,
        "black_accuracy": black,
        "analysed_plies": result.get("analysed_plies"),
        "skipped_plies": result.get("skipped_plies"),
        "elapsed_seconds": round(elapsed, 3),
        "labels": {
            side: {label: result[side].get(label) for label in (
                "brilliants", "greats", "books", "inaccuracies", "mistakes",
                "misses", "blunders"
            )}
            for side in ("white", "black")
        },
    }
    if reference_white is not None and reference_black is not None:
        summary["chesscom_reference"] = {
            "white": reference_white, "black": reference_black,
        }
        summary["abs_difference_from_chesscom"] = {
            "white": round(abs(white - reference_white), 1),
            "black": round(abs(black - reference_black), 1),
        }
    if previous_white is not None and previous_black is not None:
        summary["previous_sharkbot_accuracy"] = {
            "white": previous_white, "black": previous_black,
        }
        if "abs_difference_from_chesscom" in summary:
            summary["change_in_abs_error_vs_previous"] = {
                "white": round(abs(white - reference_white) -
                               abs(previous_white - reference_white), 1),
                "black": round(abs(black - reference_black) -
                               abs(previous_black - reference_black), 1),
            }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgn", type=Path, required=True,
                        help="Private local PGN file. It is never uploaded.")
    for field in ("reference_white", "reference_black", "previous_white", "previous_black"):
        parser.add_argument("--" + field.replace("_", "-"), type=float)
    args = parser.parse_args()
    print(json.dumps(audit_pgn(
        args.pgn, args.reference_white, args.reference_black,
        args.previous_white, args.previous_black,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
