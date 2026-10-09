"""Offline, local-file baseline from the official Lichess open PGN database.

This is *not* a cheating-label dataset. Use only for exploratory human move/
clock statistics. No downloads, user identifiers, network access or changes to
the Fair Play detector. Standard rated games only; stratify time control/Elo.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import io
import json
from pathlib import Path
from statistics import median

import chess.pgn


def bracket(value):
    try:
        number = int(value)
        return f"{min(2900, max(0, number // 200 * 200)):04d}-{min(3100, max(200, number // 200 * 200 + 199)):04d}"
    except (ValueError, TypeError):
        return None


def examine(games, max_games=10000):
    """Aggregated statistics only. No accounts, PGNs or positions in output."""
    out = defaultdict(lambda: {"games": 0, "plies": 0, "think_seconds": []})
    read = 0
    for game in games:
        read += 1
        if read > max_games:
            break
        headers = game.headers
        if headers.get("Variant", "Standard") not in ("Standard", "Chess"):
            continue
        if headers.get("Rated", "True").lower() == "false":
            continue
        control = headers.get("TimeControl", "")
        if not control or "?" in control or "-" in control:
            continue
        nodes = list(game.mainline())
        if len(nodes) < 24:
            continue
        for color, header in ((True, "WhiteElo"), (False, "BlackElo")):
            band = bracket(headers.get(header))
            if band is None:
                continue
            key = (control, band)
            row = out[key]
            row["games"] += 1
            row["plies"] += sum(1 for index in range(len(nodes))
                               if (index % 2 == 0) == color)
            last = None
            for index, node in enumerate(nodes):
                if (index % 2 == 0) != color:
                    continue
                clock = node.clock()
                if clock is not None and last is not None:
                    value = last - clock
                    # Do not assert elapsed time when increment is unknown.
                    if 0 <= value < 900:
                        row["think_seconds"].append(value)
                last = clock
    summary = [
        {"time_control": ctrl, "rating_band": band, "games": item["games"],
         "plies": item["plies"],
         "clock_observations": len(item["think_seconds"]),
         "median_raw_clock_decrement": (
             round(median(item["think_seconds"]), 3)
             if item["think_seconds"] else None)}
        for (ctrl, band), item in sorted(out.items())
    ]
    return {"schema": "lichess-observational-human-baseline-v1",
            "source": "Local official Lichess PGN only; neither clean labels nor evidence of cheating",
            "games_read": min(read, max_games), "groups": summary,
            "note": "Raw decrement omits increment and lag; do not treat it as validated think time."}


@contextmanager
def open_text(path):
    path = Path(path)
    if path.suffix != ".zst":
        with path.open("rt", encoding="utf-8", errors="replace") as src:
            yield src
        return
    try:
        import zstandard
    except ImportError as exc:
        raise RuntimeError("Install optional 'zstandard' to read .pgn.zst") from exc
    with path.open("rb") as raw:
        with zstandard.ZstdDecompressor().stream_reader(raw) as stream:
            with io.TextIOWrapper(stream, encoding="utf-8", errors="replace") as text:
                yield text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path, help="Locally downloaded official Lichess PGN or PGN.zst")
    parser.add_argument("--max-games", type=int, default=10000)
    args = parser.parse_args()
    if not 1 <= args.max_games <= 50000:
        parser.error("--max-games must be between 1 and 50000")
    with open_text(args.input) as file:
        def games():
            while True:
                game = chess.pgn.read_game(file)
                if game is None:
                    return
                yield game
        result = examine(games(), max_games=args.max_games)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
