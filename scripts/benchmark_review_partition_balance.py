"""Measure whether position-complexity weighting improves the 14-shard critical path.

Same legal 76-ply synthetic game, same Stockfish 19 fixed depth 18,
same 14 physical workers; serializes shard trials on one runner for
repeatable per-shard times. Report *projected* parallel critical path,
not Discord wall time. Never use this heuristic in production without
evidence and verification of full original FEN/move coverage.
"""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chess
import chess_play
from scripts.benchmark_review_hash import TACTICAL

WORKERS = 14


def boundaries(moves, weighted):
    n = len(moves)
    if not weighted:
        return [n * i // WORKERS for i in range(WORKERS + 1)]
    board = chess.Board()
    weights = []
    for san in moves:
        legal = list(board.legal_moves)
        captures = sum(board.is_capture(move) for move in legal)
        # Search cost generally grows with legal branching and forcing moves.
        weights.append(1.0 + .045 * len(legal) + .22 * captures
                       + (.6 if board.is_check() else 0))
        board.push_san(san)
    total = sum(weights)
    result = [0]
    acc = 0.0
    next_boundary = 1
    for index, weight in enumerate(weights, 1):
        acc += weight
        while (next_boundary < WORKERS and
               acc >= total * next_boundary / WORKERS and
               index >= result[-1] + 1 and
               index <= n - (WORKERS - next_boundary)):
            result.append(index)
            next_boundary += 1
    while len(result) < WORKERS:
        slot = max(range(len(result) - 1), key=lambda k:
                   result[k + 1] - result[k])
        cut = (result[slot] + result[slot + 1]) // 2
        if cut <= result[slot] or cut >= result[slot + 1]:
            raise RuntimeError("Failed to build nonempty weighted sections")
        result.insert(slot + 1, cut)
    result.append(n)
    return result


def analyse(moves, cuts):
    board = chess.Board()
    results = []
    for worker in range(WORKERS):
        start, end = cuts[worker:worker + 2]
        segment = moves[start:end]
        if not segment:
            raise AssertionError("Empty worker")
        start_fen = board.fen(en_passant="fen")
        for san in segment:
            board.push_san(san)
        chess_play._close_analysis_engine()
        t = time.perf_counter()
        result = chess_play.analyse_game_moves(segment, start_fen=start_fen)
        elapsed = time.perf_counter() - t
        if (result["analysed_plies"] != len(segment)
                or result["analysis_depth"] != 18
                or result["engine_configuration"]["threads"] != 2):
            raise RuntimeError("Incorrect fixed depth-18 review analysis")
        results.append(round(elapsed, 3))
    return results


def main():
    chess_play.STOCKFISH_THREADS = 2
    chess_play.STOCKFISH_HASH_MB = 64
    try:
        times = {}
        for name, weighted in (("uniform", False), ("weighted", True)):
            cuts = boundaries(TACTICAL, weighted)
            times[name] = analyse(TACTICAL, cuts)
            print("Review partition trial " + json.dumps({
                "algorithm": name,
                "sizes": [b - a for a, b in zip(cuts, cuts[1:])],
                "seconds": times[name],
                "max_worker_seconds": max(times[name]),
                "total_worker_seconds": round(sum(times[name]), 3)
            }), flush=True)
        print("REVIEW_PARTITION_BENCHMARK " + json.dumps({
            "uniform_max": max(times["uniform"]),
            "weighted_max": max(times["weighted"]),
            "critical_path_speedup": round(
                max(times["uniform"]) / max(times["weighted"]), 3),
            "note": "projected compute-only; no remote runner or git timings"
        }), flush=True)
    finally:
        chess_play._close_analysis_engine()


if __name__ == "__main__":
    main()
