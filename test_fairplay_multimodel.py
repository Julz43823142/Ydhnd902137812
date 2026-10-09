"""Synthetic opt-in research-model tests; never use real accused accounts."""
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import chess
from fairplay_multimodel import (
    _san_history, _valid_move, local_base_url, model_config,
    run_external_models, samples_for_review, KNOWN, INCOMPATIBLE,
)


def fixture(count=6):
    games = []
    for i in range(count):
        board = chess.Board()
        move = board.parse_san("e4")
        decision = SimpleNamespace(
            ply=1, fen=board.fen(), move=move.uci(),
            useful=True, metrics={"nodes": 24000}, clock_before=120,
            clock_valid=True, think=2.1)
        games.append(SimpleNamespace(
            identity=f"synthetic-{i}", ended=i + 1, deep=True,
            probe_only=False, rated=True, time_class="blitz",
            rating=1600, opponent_rating=1610, time_control="180+0",
            moves=["e2e4"], decisions=[decision]))
    return SimpleNamespace(games=games, diagnostics={}, priority="LOW")


class ModelSafety(unittest.TestCase):
    def test_only_local_services_allowed(self):
        self.assertEqual(local_base_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertEqual(local_base_url("http://localhost:12398/"), "http://localhost:12398")
        for url in (
            "https://127.0.0.1:8000", "http://chess.com:8000",
            "http://127.0.0.1:8000/path", "http://admin@localhost:8000",
            "http://localhost:8000/?x=1", "file:///tmp/models",
            "http://169.254.169.254:8000",
        ):
            with self.subTest(url=url):
                self.assertIsNone(local_base_url(url))

    def test_none_means_no_fake_models_and_priority_unchanged(self):
        result = fixture()
        audit = run_external_models(result, env={"FAIRPLAY_EXTERNAL_MODELS": ""})
        self.assertEqual(audit["requested"], [])
        self.assertEqual(audit["models"], {})
        self.assertEqual(result.priority, "LOW")

    def test_unavailable_weights_are_not_counted_as_model_inference(self):
        audit = run_external_models(fixture(), env={
            "FAIRPLAY_EXTERNAL_MODELS": "maia3_23m,maia3_79m,lc0,chessmimic,allie"})
        self.assertEqual(audit["models"], {})
        self.assertTrue(all(row["status"] == "unavailable"
                            for row in audit["statuses"].values()))

    def test_unsupported_lichess_moderation_services_are_not_fake_checks(self):
        audit = run_external_models(fixture(), env={
            "FAIRPLAY_EXTERNAL_MODELS": "kaladin,irwin,not_a_model"})
        self.assertEqual(audit["statuses"]["kaladin"]["status"], "incompatible")
        self.assertEqual(audit["statuses"]["irwin"]["status"], "incompatible")
        self.assertEqual(audit["statuses"]["not_a_model"]["status"], "unknown")
        self.assertFalse(audit["models"])

    def test_sampling_is_temporally_spread_across_500_deep_games(self):
        picked = samples_for_review(fixture(500), 48)
        self.assertEqual(len(picked), 48)
        self.assertEqual(picked[0][0], 0)
        self.assertEqual(picked[-1][0], 499)
        self.assertEqual(len({g.identity for _, g, d in picked}), 48)

    def test_unsearched_games_and_unrated_games_never_enter_model_sample(self):
        result = fixture(4)
        result.games[0].deep = False
        result.games[1].rated = None
        result.games[2].probe_only = True
        self.assertEqual(len(samples_for_review(result)), 1)

    def test_maia_23m_real_uci_protocol_produces_observations(self):
        board = chess.Board()
        engine = SimpleNamespace(
            timeout=30,
            configure=Mock(),
            analyse=Mock(return_value=[{"pv": [chess.Move.from_uci("e2e4")]}]),
            quit=Mock())
        with patch("fairplay_multimodel.model_config",
                   return_value=({"kind": "uci",
                                  "command": ["maia3-uci", "--model", "maia3-23m"]}, None)):
            result = fixture(3)
            audit = run_external_models(result,
                env={"FAIRPLAY_EXTERNAL_MODELS": "maia3_23m"},
                engine_factory=lambda cmd: engine)
        self.assertEqual(audit["statuses"]["maia3_23m"]["status"], "evaluated")
        self.assertEqual(audit["models"]["maia3_23m"]["top1_agreement"], 1)
        self.assertEqual(len(audit["models"]["maia3_23m"]["observations"]), 3)
        self.assertEqual(result.priority, "LOW")
        self.assertEqual(engine.configure.call_args_list[0].args[0]["SelfElo"], 1600)
        self.assertEqual(engine.analyse.call_args_list[0].args[1].depth, 1)
        engine.quit.assert_called_once()

    def test_unexpected_model_failure_is_not_reported_as_agreement(self):
        engine = SimpleNamespace(
            options={}, analyse=Mock(side_effect=RuntimeError("synthetic")),
            configure=Mock(), quit=Mock())
        with patch("fairplay_multimodel.model_config",
                   return_value=({"kind": "uci", "command": ["lc0"],
                                  "weights": "/tmp/weights"}, None)):
            audit = run_external_models(fixture(2),
                env={"FAIRPLAY_EXTERNAL_MODELS": "lc0"},
                engine_factory=lambda cmd: engine)
        self.assertEqual(audit["models"], {})
        self.assertEqual(audit["statuses"]["lc0"]["status"], "failed")
        engine.quit.assert_called_once()

    def test_chessmimic_actual_documented_api_and_clock_observations(self):
        def get(url):
            self.assertEqual(url, "http://127.0.0.1:8000/models")
            return {"move_models": {"count": 2}}
        post = Mock(return_value={"move": "e4", "thinking_time": 2.5})
        audit = run_external_models(fixture(1), env={
            "FAIRPLAY_EXTERNAL_MODELS": "chessmimic",
            "FAIRPLAY_CHESSMIMIC_URL": "http://127.0.0.1:8000",
            "FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE": "1",
        }, http_post=post, http_get=get)
        self.assertEqual(audit["statuses"]["chessmimic"]["status"], "evaluated")
        row = audit["models"]["chessmimic"]["observations"][0]
        self.assertTrue(row["top1_agreement"])
        self.assertEqual(row["actual_think_seconds"], 2.1)
        self.assertEqual(row["predicted_think_seconds"], 2.5)
        self.assertEqual(post.call_args.args[1]["moves"], [])
        self.assertEqual(post.call_args.args[1]["clock_time"], 120)

    def test_chessmimic_no_checkpoint_never_counts_arbitrary_move(self):
        audit = run_external_models(fixture(1), env={
            "FAIRPLAY_EXTERNAL_MODELS": "chessmimic",
            "FAIRPLAY_CHESSMIMIC_URL": "http://localhost:8000",
            "FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE": "1",
        }, http_get=lambda _: {"move_models": {"count": 0}})
        self.assertEqual(audit["statuses"]["chessmimic"]["status"], "failed")
        self.assertNotIn("chessmimic", audit["models"])

    def test_allie_v2_bridge_requires_correct_identity(self):
        audit = run_external_models(fixture(1), env={
            "FAIRPLAY_EXTERNAL_MODELS": "allie_v2",
            "FAIRPLAY_ALLIE_V2_BRIDGE_URL": "http://localhost:12398",
        }, http_post=lambda url, payload: {"move": "e2e4", "model": "not-allie"})
        self.assertEqual(audit["statuses"]["allie_v2"]["status"], "failed")
        self.assertEqual(audit["models"], {})

    def test_allie_v2_bridge_can_supply_actual_prediction(self):
        audit = run_external_models(fixture(1), env={
            "FAIRPLAY_EXTERNAL_MODELS": "allie_v2",
            "FAIRPLAY_ALLIE_V2_BRIDGE_URL": "http://localhost:12398",
        }, http_post=lambda url, payload: {"move": "e2e4", "model": "allie_v2"})
        self.assertEqual(audit["models"]["allie_v2"]["top1_agreement"], 1)
        self.assertEqual(audit["statuses"]["allie_v2"]["status"], "evaluated")

    def test_licensing_and_missing_module_preclude_fake_model(self):
        self.assertEqual(model_config("chessmimic", {
            "FAIRPLAY_CHESSMIMIC_URL": "http://localhost:8000",
        })[0], None)
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "23m.pt"
            file.write_bytes(b"fake weights; synthetic only")
            with patch("fairplay_multimodel.importlib.util.find_spec", return_value=None):
                cfg, reason = model_config("maia3_23m", {
                    "FAIRPLAY_MAIA_23M_CHECKPOINT": str(file)})
            self.assertIsNone(cfg)
            self.assertIn("not installed", reason)

    def test_correct_san_history_and_invalid_moves(self):
        x = fixture(1).games[0]
        self.assertEqual(_san_history(x, x.decisions[0]), [])
        board = chess.Board()
        self.assertEqual(_valid_move(board, "e4"), "e2e4")
        self.assertIsNone(_valid_move(board, "e2e5"))

    def test_model_feature_names_are_all_explicit(self):
        self.assertIn("maia3_23m", KNOWN)
        self.assertIn("maia3_79m", KNOWN)
        self.assertIn("allie_v2", KNOWN)
        self.assertIn("maia4all", KNOWN)
        self.assertIn("kaladin", INCOMPATIBLE)
        self.assertIn("irwin", INCOMPATIBLE)


if __name__ == "__main__":
    unittest.main()
