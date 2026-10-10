"""Real Stockfish 19 v21 MultiPV-1 -> MultiPV-3 -> depth-18 smoke.

Uses only deterministic synthetic PGN positions. No Chess.com accounts or
network calls, and cannot issue review priorities.
"""
import chess.engine
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from fairplay_analysis import EngineScanner
from fairplay_config import CONFIG
from fairplay_data import parse_game
from scripts.smoke_stockfish_reviews import TARGET, fixture


def main():
    game=parse_game(fixture(),TARGET)
    if game is None:
        raise AssertionError("Synthetic PGN unavailable")
    decision=next((d for d in game.decisions if d.useful
                   and d.legal>=CONFIG.critical_legal),None)
    if decision is None:
        raise AssertionError("Synthetic fixture lacks candidate-rich move")
    worker=EngineScanner(None,CONFIG)
    try:
        assert worker.analyse_decision(game,decision,CONFIG.fast_nodes)
        shallow=decision.metrics.copy()
        assert shallow["search_contract"]["multipv"]==1
        assert shallow["candidate_count"]==1, "PV1 must expose only one candidate"

        assert worker.analyse_decision(game,decision,CONFIG.fast_nodes,
                                       fast_multipv_override=3)
        confirmation=decision.metrics.copy()
        assert confirmation["search_contract"]["multipv"]==3
        assert confirmation["candidate_count"]==3
        assert confirmation["gap"] is not None
        assert confirmation["spread"] is not None

        # The selected fast-engine snapshot, rather than the 500-wide PV1
        # snapshot, is what depth18 stability compares against.
        decision.fast_engine=confirmation
        assert worker.analyse_decision(game,decision,chess.engine.Limit(depth=18))
        deep=decision.metrics
        assert deep["search_contract"]["completed"] is True
        assert deep["search_contract"]["multipv"]==3
        assert deep["search_depth"]>=18
        assert deep["candidate_count"]==3
        # Search disagreements remain explicitly marked non-exact; never
        # turn them into automatically verified cheating evidence.
        print("V21_REAL_ENGINE_SMOKE",{
            "wide_fast_pv":shallow["candidate_count"],
            "selected_fast_pv":confirmation["candidate_count"],
            "selected_depth":deep["search_depth"],
            "selected_deep_pv":deep["candidate_count"]},flush=True)
    finally:
        worker.close()


if __name__=="__main__":
    main()
