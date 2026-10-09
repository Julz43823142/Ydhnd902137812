"""Comparable real Stockfish 19 search timings on synthetic-only bullet positions.

Both modes use Threads=1, MultiPV=5 and a root-restricted search when the
played move is not in MultiPV, matching the production engine work. This is
not a benchmark of a complete account, nor evidence of cheating.
"""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chess
import chess.engine
import chess_play

from fairplay_config import CONFIG
from fairplay_data import parse_game
from scripts.smoke_stockfish_reviews import TARGET, fixture


def main():
    item = parse_game(fixture(), TARGET)
    useful = [decision for decision in item.decisions if decision.useful]
    if len(useful) < 4:
        raise RuntimeError("Synthetic fixture does not contain four useful positions")
    positions = [useful[round(i * (len(useful) - 1) / 3)] for i in range(4)]
    binary = chess_play._resolve_stockfish_binary(allow_install=False)
    engine = chess.engine.SimpleEngine.popen_uci(binary, timeout=180)
    observed = []
    try:
        options = {"Threads": 1, "Hash": CONFIG.hash_mb}
        if "UCI_LimitStrength" in engine.options:
            options["UCI_LimitStrength"] = False
        engine.configure(options)
        for decision in positions:
            board = chess.Board(decision.fen)
            actual = chess.Move.from_uci(decision.move)
            row = {"ply": decision.ply}
            for depth in (12, 18):
                engine.configure({"Clear Hash": None})
                start = time.monotonic()
                lines = engine.analyse(board, chess.engine.Limit(depth=depth),
                                       multipv=CONFIG.deep_multipv)
                if any(line.get("depth", 0) < depth for line in lines):
                    raise RuntimeError("Stockfish did not reach the requested depth")
                extra = actual not in [line["pv"][0] for line in lines]
                if extra:
                    engine.configure({"Clear Hash": None})
                    root = engine.analyse(board, chess.engine.Limit(depth=depth),
                                          root_moves=[actual])
                    if root.get("depth", 0) < depth:
                        raise RuntimeError("Root analysis did not reach the requested depth")
                row[str(depth)] = {"seconds": round(time.monotonic() - start, 4),
                                   "root_search": extra}
            observed.append(row)
            print("BULLET_DEPTH_POSITION " + json.dumps(row, sort_keys=True), flush=True)
    finally:
        try:
            engine.quit()
        except Exception:
            engine.close()
    before = sum(row["18"]["seconds"] for row in observed)
    after = sum(row["12"]["seconds"] for row in observed)
    print("BULLET_DEPTH_BENCH " + json.dumps({
        "source": "4 synthetic positions; official Stockfish 19; Threads=1; MultiPV=5; played-root included",
        "sample_positions": len(observed),
        "depth18_total_seconds": round(before, 4),
        "depth12_total_seconds": round(after, 4),
        "bullet_engine_deep_speedup_ratio": round(before / max(.0001, after), 2),
        "depth18_median_seconds": round(statistics.median(row["18"]["seconds"] for row in observed), 4),
        "depth12_median_seconds": round(statistics.median(row["12"]["seconds"] for row in observed), 4),
        "not_included": "archive fetch, 24k fast pass, Maia, clock analysis, Git checkpoint writes",
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
