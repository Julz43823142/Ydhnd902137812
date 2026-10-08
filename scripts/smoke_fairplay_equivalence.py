"""Cold real-Stockfish equivalence of sequential and positional Fair Play scheduling.

Runs only in CI with the pinned engine and a synthetic game: no account data.
"""
from fairplay_analysis import SharedEnginePool, _game_cache, review
from scripts.smoke_stockfish_reviews import FixtureAPI, TARGET


def evidence(result):
    return (
        result.priority, result.classes, result.totals,
        result.deep_coverage, result.diagnostics['gate_scores'],
        [(game.identity, game.metrics, game.fast_metrics,
          [(decision.move, decision.useful, decision.metrics, decision.fast_engine)
           for decision in game.decisions])
         for game in result.games],
    )


def main():
    _game_cache.clear()
    baseline = review(TARGET, lambda _: None, api_factory=FixtureAPI, engine_workers=1)
    _game_cache.clear()
    pool = SharedEnginePool(size=2)
    try:
        parallel = review(TARGET, lambda _: None, api_factory=FixtureAPI, engine_pool=pool)
        assert parallel.diagnostics['runtime']['fast_position_tasks'] > 0
        assert parallel.diagnostics['runtime']['deep_position_tasks'] > 0
        assert evidence(parallel) == evidence(baseline), (
            'Position scheduling changed real Stockfish evidence or classification'
        )
        print('Real Stockfish sequential/position-queue evidence equivalence passed.')
    finally:
        pool.close()
        _game_cache.clear()


if __name__ == '__main__':
    main()
