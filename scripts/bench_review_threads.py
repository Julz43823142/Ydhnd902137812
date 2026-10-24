"""Offline Stockfish 19 depth-18 worker thread benchmark (no private game)."""
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chess
import chess_play


def main():
    # Legal deterministic opening; analyse the later 12 plies from their true FEN.
    moves = ("e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 "
             "Bb3 d6 c3 O-O h3 Nb8 d4 Nbd7").split()
    board = chess.Board()
    for san in moves[:8]:
        board.push_san(san)
    start_fen = board.fen()
    segment = moves[8:]
    timings = {}
    try:
        for threads in (1, 2, 4):
            chess_play._close_analysis_engine()
            chess_play.STOCKFISH_THREADS = threads
            start = time.perf_counter()
            result = chess_play.analyse_game_moves(segment, start_fen=start_fen)
            seconds = time.perf_counter() - start
            if (result["analysed_plies"] != len(segment)
                    or result["analysis_depth"] != 18
                    or not result["engine"].startswith("Stockfish 19")
                    or result["engine_configuration"]["threads"] != threads):
                raise RuntimeError("Invalid depth-18 benchmark result")
            timings[str(threads)] = round(seconds, 3)
            print(f"Stockfish 19 depth-18 threads={threads}: {seconds:.3f}s", flush=True)
        print("Review worker thread benchmark: " + json.dumps(timings), flush=True)
    finally:
        chess_play._close_analysis_engine()


if __name__ == "__main__":
    main()
