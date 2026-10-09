"""Real (network-free at inference time) smoke: Maia-3 23M UCI policy."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess
from fairplay_multimodel import run_external_models

board = chess.Board()
decision = SimpleNamespace(
    ply=1, fen=board.fen(), move="e2e4", useful=True,
    metrics={"nodes": 24000}, clock_before=150.0, think=2.0,
    clock_valid=True)
game = SimpleNamespace(
    identity="synthetic-opening", ended=1, deep=True,
    rated=True, probe_only=False, decisions=[decision], moves=["e2e4"],
    rating=1500, opponent_rating=1500, time_control="180+0")
result = SimpleNamespace(games=[game], priority="LOW")
variants = ["maia3_23m"]
if os.environ.get("FAIRPLAY_MAIA_79M_CHECKPOINT"):
    variants.append("maia3_79m")
environ = dict(os.environ, FAIRPLAY_EXTERNAL_MODELS=",".join(variants),
               FAIRPLAY_EXTERNAL_MAX_POSITIONS="1",
               FAIRPLAY_EXTERNAL_MAX_SECONDS="300")
report = run_external_models(result, env=environ)
for name in variants:
    row = report["statuses"].get(name, {})
    assert row.get("status") == "evaluated", (name, row)
    assert row.get("positions") == 1, (name, row)
    model = report["models"][name]
    assert model["observations"][0]["predicted"] in {
        m.uci() for m in board.legal_moves}, model
assert result.priority == "LOW"
print("Verified: real local Maia-3 " + ", ".join(variants) +
      " UCI predictions; review score untouched.")
