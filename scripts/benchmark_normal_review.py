"""Real Stockfish review benchmark, synthetic game only; no private data.

Measures real local depth-18 review and a second identical request served
from the validated analysis cache. This does NOT measure remote worker startup.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from game_review_service import GameReviewService


def main():
    service = GameReviewService()
    moves = ["e4", "e5"]
    start = time.perf_counter()
    first = service.analyse(moves)
    local_seconds = time.perf_counter() - start
    start = time.perf_counter()
    second = service.analyse(moves)
    cached_seconds = time.perf_counter() - start
    if (first != second or first["analysed_plies"] != len(moves)
        or service.stats()["misses"] != 1 or service.stats()["hits"] != 1):
        raise RuntimeError("Game Review cache benchmark failed correctness checks.")
    print(json.dumps({
        "synthetic_game": True,
        "engine": first["engine"],
        "depth": first["analysis_depth"],
        "plies": first["analysed_plies"],
        "local_seconds": round(local_seconds, 3),
        "cached_seconds": round(cached_seconds, 4),
        "cache": service.stats(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
