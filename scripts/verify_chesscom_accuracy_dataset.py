"""Independently re-validate an exported public Chess.com accuracy dataset.

This deliberately does not import the collector's game validation logic.
It does not compute any Stockfish metrics or make HTTP requests.
"""
import argparse
from datetime import datetime, timezone
from io import StringIO
import json
import math
from pathlib import Path
import sys

import chess
import chess.pgn


def verify(dataset_path, report_path=None):
    games = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    if not isinstance(games, list):
        raise ValueError("Expected dataset JSON list")
    ids = set()
    urls = set()
    checked = 0
    for i, g in enumerate(games, 1):
        try:
            if not isinstance(g, dict):
                raise ValueError("Not an object")
            ref = g["chesscom"]
            for side in ("white", "black"):
                val = ref[side]
                if type(val) not in (float, int) or not math.isfinite(val) or not 0 <= val <= 100:
                    raise ValueError("Missing or invalid official accuracy")
            if g.get("provenance", {}).get("source") != "Chess.com Published Data API":
                raise ValueError("Unknown source")
            uid, url = g.get("uuid"), g.get("url")
            if not uid and not url:
                raise ValueError("Missing identity")
            if uid and uid in ids or url and url in urls:
                raise ValueError("Duplicate identity")
            if uid:
                ids.add(uid)
            if url:
                urls.add(url)
            ended = datetime.fromisoformat(g["end_time_utc"])
            if ended.tzinfo is None or ended.utcoffset().total_seconds() != 0:
                raise ValueError("Time not UTC")
            if int(ended.timestamp()) != g["end_time"]:
                raise ValueError("Incorrect Unix timestamp")
            if ended.date().isoformat() != g["date_utc"]:
                raise ValueError("Incorrect UTC date")
            raw = StringIO(g["pgn"])
            parsed = chess.pgn.read_game(raw)
            if parsed is None or parsed.errors or chess.pgn.read_game(raw) is not None:
                raise ValueError("Invalid PGN")
            if parsed.headers.get("Result") != g["result"]:
                raise ValueError("Result mismatch")
            for side, header in (("white", "White"), ("black", "Black")):
                if parsed.headers.get(header, "").casefold() != g[side]["username"].casefold():
                    raise ValueError("Player mismatch")
            if parsed.board().chess960 or parsed.board().fen() != chess.STARTING_FEN:
                raise ValueError("Nonstandard initial position")
            board = parsed.board()
            plies = 0
            for move in parsed.mainline_moves():
                if move not in board.legal_moves:
                    raise ValueError("Illegal move")
                board.push(move)
                plies += 1
            if plies == 0 or plies > 600 or plies != g["plies"]:
                raise ValueError("Incorrect move count")
            checked += 1
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Dataset entry {i}: {exc}") from exc

    if report_path is not None:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        if checked != report["stored_games"]:
            raise ValueError("Export and report counts differ")
    return checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        checked = verify(args.dataset, args.report)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Independent verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Independent verification PASS: {checked} unique legal PGNs with two official accuracy scores")
    return 0


if __name__ == "__main__":
    sys.exit(main())
