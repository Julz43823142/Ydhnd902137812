"""Exercise exact depth-18 full-game Fair Play without network/user accounts."""
import os
import time
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.smoke_stockfish_reviews import TARGET, FixtureAPI
from fairplay_analysis import review, close_shared_engine_pool


def main():
    os.environ['FAIRPLAY_FULL_DEPTH18']='1'
    started=time.monotonic()
    try:
        report=review(TARGET,lambda text:None,api_factory=FixtureAPI)
        runtime=report.diagnostics['runtime']
        if not runtime['full_depth18_mode'] or runtime['full_depth18_games_completed']!=1:
            raise AssertionError(f'Full depth-18 review was not completed: {runtime}')
        if report.partial:
            raise AssertionError('Full depth-18 report was partial')
        depth_rows=[d.metrics['search_depth'] for g in report.games for d in g.decisions
                    if 'search_depth' in d.metrics]
        if not depth_rows or min(depth_rows)!=18:
            raise AssertionError(f'Expected exact depth 18 for all retained searches, got {depth_rows}')
        if runtime['deep_games']!=1 or runtime['fast_position_tasks']<=0:
            raise AssertionError('Missing complete two-pass coverage')
        print(f'DEPTH18_FULL_GAME_PASSED positions={len(depth_rows)} seconds={time.monotonic()-started:.1f}',flush=True)
    finally:
        close_shared_engine_pool()


if __name__=='__main__':
    main()
