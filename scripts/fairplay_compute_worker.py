"""GitHub Actions entry point for a single encrypted Stockfish shard.

Never accesses Discord, the Chess.com API, user identities or the scoring
model. Inputs are opaque ticket/partition identifiers only.
"""
import os
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from fairplay_distributed import WORKERS, worker, safe_error_code


def main():
    ticket=os.getenv("INPUT_TICKET","")
    index=os.getenv("INPUT_SHARD","")
    if re.fullmatch(r"[0-9a-f]{24}",ticket) is None or index not in ("0","1","2","3","4"):
        raise SystemExit("Invalid bounded compute workload parameters.")
    try:
        result=worker(ticket,int(index))
        print(f"Completed isolated Stockfish shard {result['shard']}: "
              f"{result['games']} games / {result['positions']} positions.",flush=True)
    except Exception:
        # Never print sensitive FEN/PGN/account paths or underlying exception.
        raise SystemExit("Isolated Fair Play compute shard failed closed.") from None


if __name__=="__main__":
    main()
