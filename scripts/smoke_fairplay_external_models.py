"""Run installed external models against a synthetic opening position only.

Never queries a real account. Intended for a self-hosted research worker after
explicitly provisioning models and accepting applicable upstream licenses.

Usage:
  python scripts/smoke_fairplay_external_models.py --require lc0
  python scripts/smoke_fairplay_external_models.py --require allie_2,chessmimic

Exit 1 unless every required model makes >=1 actual legal prediction.
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess
from fairplay_multimodel import run_external_models
from fairplay_model_readiness import summarize_model_audit


def synthetic_result():
    board = chess.Board()
    decision = SimpleNamespace(
        ply=1, fen=board.fen(), move="e2e4", useful=True,
        metrics={"search_depth": 18}, clock_before=180,
        opponent_clock_before=None, clock_after=178,
        clock_valid=True, think=2.0)
    game = SimpleNamespace(
        identity="synthetic-only", ended=1, deep=True, probe_only=False,
        rated=True, time_class="blitz", rating=1600, opponent_rating=1600,
        time_control="180+0", color=True, moves=["e2e4"],
        decisions=[decision])
    return SimpleNamespace(games=[game], priority="LOW", diagnostics={})


def run(required, *, env=None):
    environment = dict(__import__("os").environ if env is None else env)
    environment["FAIRPLAY_EXTERNAL_MODELS"] = ",".join(required)
    environment["FAIRPLAY_EXTERNAL_MAX_POSITIONS"] = "1"
    environment["FAIRPLAY_EXTERNAL_MAX_SECONDS"] = "120"
    specimen = synthetic_result()
    audit = run_external_models(specimen, env=environment)
    summary = summarize_model_audit(audit)
    # Never emit model prediction details, accounts, FENs, local paths, URLs.
    print(json.dumps(summary, sort_keys=True))
    return (all(summary["per_model"][name]["status"] == "evaluated"
                and summary["per_model"][name]["positions"] >= 1
                for name in required)
            and specimen.priority == "LOW")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require", required=True,
                        help="Comma-separated providers to actually execute")
    args = parser.parse_args()
    names = list(dict.fromkeys(n.strip().lower() for n in args.require.split(",")
                               if n.strip()))
    if not names:
        parser.error("At least one real model must be required")
    sys.exit(0 if run(names) else 1)
