"""Offline ordinary Game Review opening labels from a public CC0 reference.

The existing Lichess chess-openings snapshot is shared as read-only data; this
module does not invoke or change any Fair Play opening heuristics. A move is
Book only while the played sequence remains a documented opening prefix.
"""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _known_opening_prefixes():
    path = Path(__file__).resolve().parent / "assets" / "fairplay" / "opening-lines.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("license") != "CC0-1.0" or not isinstance(data.get("lines"), list):
            return frozenset()
        prefixes = set()
        for line in data["lines"]:
            if not isinstance(line, str):
                continue
            moves = tuple(line.split()[:30])
            for length in range(1, len(moves) + 1):
                prefixes.add(moves[:length])
        return frozenset(prefixes)
    except (OSError, ValueError, TypeError, AttributeError):
        # Never label a move as theory if reference data is missing or invalid.
        return frozenset()


def is_known_opening_move(played_uci, next_uci):
    """Confirm an exact move-sequence continuation, not just an early ply."""
    return tuple(played_uci) + (str(next_uci),) in _known_opening_prefixes()
