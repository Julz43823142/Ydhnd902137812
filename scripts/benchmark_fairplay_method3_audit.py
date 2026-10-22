"""Offline, anonymous Method 3 cost/stability experiment; no detection claim.

Run each --games value in a fresh process for comparable peak RSS and cold
process caches. Collection is injected, so this does NOT measure API/Discord
latency. No network, engines, models, validation labels or account names.
"""
import argparse
import json
from pathlib import Path
import random
import resource
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairplay_method3 import review
from fairplay_method3_ui import chart_pngs


class OfflineAPI:
    def __init__(self, deadline):
        self.deadline = deadline

    def get(self, target, suffix='', *, profile=False):
        if not profile:
            raise AssertionError('The benchmark must not fetch game data')
        return {'username': target}

    def close(self):
        pass


def sample(count, seed):
    rng = random.Random(seed)
    rows = []
    for i in range(count):
        kind = i % 3
        rating = 1500 + rng.randrange(-100, 101)
        opponent = rating + rng.randrange(-200, 201)
        expected = 1 / (1 + 10 ** ((opponent-rating)/400))
        rows.append(SimpleNamespace(
            identity=str(i), ended=1600000000+i*3600, rated=i % 5 != 0,
            time_class=('rapid', 'blitz', 'bullet')[kind],
            time_control=('600+0', '300+0', '60+0')[kind],
            rating=rating, opponent_rating=opponent,
            opponent_username=f'anonymous-{i % 250}',
            score=int(rng.random() < expected),
            accuracy=60+rng.random()*30 if i % 5 else None, decisions=[]))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--games', type=int, choices=(200, 1000, 5000), default=5000)
    parser.add_argument('--seed', type=int, default=20261022)
    args = parser.parse_args()
    rows = sample(args.games, args.seed)
    start = time.perf_counter()
    with patch('fairplay_method3.collect_games', return_value=(rows, {}, False)):
        result = review('anonymous-subject', lambda _: None, api_factory=OfflineAPI)
    scoring = time.perf_counter()-start
    start = time.perf_counter()
    files = chart_pngs(result)
    rendering = time.perf_counter()-start
    print(json.dumps({
        'version': result.version, 'games': len(rows), 'seed': args.seed,
        'scoring_seconds': scoring, 'charts_seconds': rendering,
        'max_rss_MiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        'pngs': len(files), 'windows': result.diagnostics['method3']['searched_windows'],
        'priority': result.priority,
        'limits': 'Synthetic bounded Elo-outcome model only; no real-world accuracy estimate. '
                  'Excludes collection, PGN parsing, Discord and queue time.'}, indent=2))


if __name__ == '__main__':
    main()
