"""Benchmark Stockfish 19 depth-18 per-worker hash on realistic legal shards.

Offline, fully synthetic, no external API or private PGNs. Uses the same
production analysis function, exact depth, threads and profile-guided binary.
"""
from pathlib import Path
import json
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chess
import chess_play

OPENING = (
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 "
    "c3 O-O h3 Nb8 d4 Nbd7"
).split()

TACTICAL = (
    "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O Nf3 h6 Bh4 b6 "
    "Bd3 Bb7 O-O Nbd7 Rc1 c5 h3 cxd4 exd4 Re8 Ne5 dxc4 "
    "Bb1 Nxe5 dxe5 Nd7 Bg3 Rc8 Qc2 Nf8 Rcd1 Bd5 "
    "Rd4 Qc7 Nb5 Qd7 Qa4 Bc5 Rdd1 f5 Qa6 Qf7 Nd6 Qc7 "
    "Rxd5 exd5 Nxc8 f4 Bh2 Qxc8 Qxa7 Re7 Qa4 Ne6 "
    "Bf5 Qf8 Bg6 Nd4 Re1 Kh8 Kh1 b5 Qd1 b4 "
    "e6 c3 Bf7 g5 h4 Nxe6 Rxe6 Rxe6 Bxe6 cxb2 Qb1 Qg7"
).split()


def shard(moves, offset, count):
    board = chess.Board()
    for san in moves[:offset]:
        board.push_san(san)
    return board.fen(), moves[offset:offset + count]


def main():
    chess_play.STOCKFISH_THREADS = 2
    workloads = (
        ("opening", shard(OPENING, 8, 12)),
        ("tactical", shard(TACTICAL, 38, 10)),
    )
    summary = {}
    try:
        for name, (fen, moves) in workloads:
            samples = {str(value): [] for value in (64, 128, 256)}
            # Forward and reverse order to expose warmup and runner variance.
            for hash_mb in (64, 128, 256, 256, 128, 64):
                chess_play._close_analysis_engine()
                chess_play.STOCKFISH_HASH_MB = hash_mb
                started = time.perf_counter()
                result = chess_play.analyse_game_moves(moves, start_fen=fen)
                elapsed = time.perf_counter() - started
                if (result["analysis_depth"] != 18 or
                        result["analysed_plies"] != len(moves) or
                        result["engine_configuration"]["hash_mb"] != hash_mb or
                        result["engine_configuration"]["threads"] != 2 or
                        not result["engine"].startswith("Stockfish 19")):
                    raise RuntimeError("Review hash benchmark violated engine contract")
                samples[str(hash_mb)].append(round(elapsed, 3))
                print(f"Review hash test {name} threads=2 hash={hash_mb}MB "
                      f"depth=18 plies={len(moves)} elapsed={elapsed:.3f}s", flush=True)
            means = {key: round(statistics.mean(v), 3) for key, v in samples.items()}
            summary[name] = {"mean_seconds": means, "samples": samples}
        print("REVIEW_HASH_BENCHMARK " + json.dumps(summary, sort_keys=True), flush=True)
    finally:
        chess_play._close_analysis_engine()


if __name__ == "__main__":
    main()
