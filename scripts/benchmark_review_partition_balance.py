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
    if not weighted:
        return [len(moves) * i // WORKERS for i in range(WORKERS + 1)]
    # Benchmark the exact same implementation that the live workers use.
    from game_review_remote import _balanced_cuts
    return _balanced_cuts(moves, chess.Board())


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
