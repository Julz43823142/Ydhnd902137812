"""Real engine parity for candidate reuse, using synthetic positions only."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess
import chess.engine
from fairplay_analysis import EngineScanner, SharedEnginePool
from fairplay_config import CONFIG


def position(move, fen, phase='middlegame'):
    board = chess.Board(fen)
    played = chess.Move.from_uci(move)
    decision = SimpleNamespace(
        fen=fen, move=move, useful=True, forced=False, trivial_kind=None,
        phase=phase, legal=board.legal_moves.count(),
        capture=board.is_capture(played), gives_check=board.gives_check(played),
        check=board.is_check(), metrics={}, fast_engine={})
    return SimpleNamespace(color=board.turn), decision


def main():
    deadline = time.monotonic() + 300
    baseline = EngineScanner(deadline)
    pool = SharedEnginePool(size=1)
    try:
        # Different played moves must share only the raw candidate search.
        # Include both in-PV and outside-PV moves, node and full-depth budgets.
        board = chess.Board()
        for san in ('d4', 'd5', 'c4', 'e6', 'Nc3', 'Nf6'):
            board.push_san(san)
        cases = []
        for limit in (CONFIG.fast_nodes, chess.engine.Limit(depth=18)):
            for move in list(board.legal_moves)[:4]:
                cases.append((limit, move.uci(), board.fen(), 'middlegame'))
            # Repeat an outside-candidate root with changed scoring context.
            cases.append((limit, 'a2a4', board.fen(), 'middlegame'))
            cases.append((limit, 'a2a4', board.fen(), 'opening'))
        times = []
        for cached in (False, True):
            results = []
            started = time.monotonic()
            for limit, move, fen, phase in cases:
                game, decision = position(move, fen=fen, phase=phase)
                if cached:
                    pool.run_decision(game, decision, limit, deadline)
                else:
                    baseline.analyse_decision(game, decision, limit)
                results.append(decision.metrics)
            times.append(time.monotonic() - started)
            if not cached:
                reference = results
            else:
                assert results == reference, 'Candidate reuse changed real engine metrics'
        hits = pool.scanners[0].profile['candidate_cache_hits']
        assert pool.scanners[0].profile['root_cache_hits'] >= 1, 'Repeated roots were not reused'
        assert hits >= 6, 'Repeated positions did not reuse candidate searches'
        print(f'Exact real-engine parity: {len(cases)} decisions, {hits} candidate hits; '
              f'uncached {times[0]:.3f}s, cached {times[1]:.3f}s '
              '(synthetic repeated-position workload, not full-review speed).')
    finally:
        baseline.close()
        pool.close()


if __name__ == '__main__':
    main()
