"""GitHub Actions entry point: isolated, bounded ordinary Game Review."""
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess_play
from game_review_remote import compute_worker, RemoteReviewError


def main():
    ticket = os.getenv("INPUT_TICKET", "")
    index = os.getenv("INPUT_SHARD", "")
    if (re.fullmatch(r"[0-9a-f]{24}", ticket) is None
            or not index.isdecimal() or not 0 <= int(index) < 14):
        raise SystemExit("Invalid bounded Game Review worker request.")
    try:
        positions = compute_worker(ticket, int(index))
        print(f"Isolated ordinary review shard {index} verified {positions} plies.")
    except Exception:
        # Do not leak game moves, source metadata, FENs or exception strings.
        raise SystemExit("Isolated ordinary review worker failed closed.") from None
    finally:
        # python-chess owns a subprocess/event-loop thread. Explicitly quitting
        # the analysis engine is required before Python can terminate cleanly;
        # atexit alone cannot release a live non-daemon engine thread.
        chess_play._close_analysis_engine()


if __name__ == "__main__":
    main()
