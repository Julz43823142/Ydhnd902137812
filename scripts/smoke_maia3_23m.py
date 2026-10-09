"""Real (network-free at inference time) smoke: Maia-3 23M UCI policy."""
import os
from types import SimpleNamespace
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
environ = dict(os.environ, FAIRPLAY_EXTERNAL_MODELS="maia3_23m",
               FAIRPLAY_EXTERNAL_MAX_POSITIONS="1",
               FAIRPLAY_EXTERNAL_MAX_SECONDS="240")
report = run_external_models(result, env=environ)
row = report["statuses"].get("maia3_23m", {})
assert row.get("status") == "evaluated", row
assert row.get("positions") == 1, row
model = report["models"]["maia3_23m"]
assert model["observations"][0]["predicted"] in {
    m.uci() for m in board.legal_moves}, model
assert result.priority == "LOW"
print("Verified: real Maia-3 23M local UCI prediction; review score untouched.")
