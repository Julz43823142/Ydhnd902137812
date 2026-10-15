"""GitHub Actions entry point: isolated, bounded ordinary Game Review."""
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game_review_remote import compute_worker, RemoteReviewError


def main():
    ticket = os.getenv("INPUT_TICKET", "")
    index = os.getenv("INPUT_SHARD", "")
    if (re.fullmatch(r"[0-9a-f]{24}", ticket) is None
            or index not in ("0", "1")):
        raise SystemExit("Invalid bounded Game Review worker request.")
    try:
        positions = compute_worker(ticket, int(index))
        print(f"Isolated ordinary review shard {index} verified {positions} plies.")
    except Exception:
        # Do not leak game moves, source metadata, FENs or exception strings.
        raise SystemExit("Isolated ordinary review worker failed closed.") from None


if __name__ == "__main__":
    main()
