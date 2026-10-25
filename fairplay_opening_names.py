"""Offline descriptive opening families from the pinned Lichess CC0 reference."""
import json
from pathlib import Path
from functools import lru_cache

@lru_cache(maxsize=1)
def reference():
    try:
        data=json.loads((Path(__file__).parent/'assets/fairplay/opening-families.json').read_text())
        return {tuple(moves):name for moves,name in data['lines']}
    except (OSError,ValueError,KeyError):return {}

def opening_family(game):
    moves=tuple((getattr(game,'moves',None) or [])[:40]);known=reference()
    for length in range(len(moves),1,-1):
        if moves[:length] in known:return known[moves[:length]]
    return 'Unknown'
