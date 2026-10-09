"""Synthetic regression tests: unavailable models are never counted as evidence."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

import chess
from fairplay_model_readiness import summarize_model_audit
from fairplay_multimodel import run_external_models, model_config
from test_fairplay_multimodel import fixture
from scripts.smoke_fairplay_external_models import synthetic_result


class ModelReadinessTests(unittest.TestCase):
    def test_real_model_must_emit_a_prediction(self):
        audit = {"requested": ["lc0", "allie_2", "maia3_23m"],
                 "statuses": {
                     "lc0": {"status": "evaluated", "positions": 1},
                     "allie_2": {"status": "evaluated", "positions": 0},
                     "maia3_23m": {"status": "unavailable", "positions": 0}},
                 "models": {"lc0": {"observations": [{"top1_agreement": True}]}}}
        result = summarize_model_audit(audit)
        self.assertEqual(result["fully_measured_models"], 1)
        self.assertEqual(result["not_measured_models"], 2)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["scoring_influence"])

    def test_no_positions_is_not_claimed_as_success(self):
        result = fixture(1)
        result.games[0].deep = False
        engine = SimpleNamespace(options={"WeightsFile": {}},
            configure=Mock(), analyse=Mock(), quit=Mock())
        with patch("fairplay_multimodel.model_config",
                   return_value=({"kind": "uci", "command": ["lc0"],
                                  "weights": "/tmp/mock.pb.gz"}, None)):
            audit = run_external_models(result,
                env={"FAIRPLAY_EXTERNAL_MODELS": "lc0"},
                engine_factory=lambda _: engine)
        self.assertEqual(audit["statuses"]["lc0"]["status"], "no_positions")
        self.assertEqual(summarize_model_audit(audit)["fully_measured_models"], 0)
        engine.analyse.assert_not_called()
        engine.quit.assert_called_once()

    def test_exception_messages_never_leak_in_diagnostic_summary(self):
        secret = "private-account-fen-and-homepath"
        engine = SimpleNamespace(options={"WeightsFile": {}}, configure=Mock(),
            analyse=Mock(side_effect=RuntimeError(secret)), quit=Mock())
        with patch("fairplay_multimodel.model_config",
                   return_value=({"kind": "uci", "command": ["lc0"],
                                  "weights": "/tmp/mock.pb.gz"}, None)):
            audit = run_external_models(fixture(1),
                env={"FAIRPLAY_EXTERNAL_MODELS": "lc0"},
                engine_factory=lambda _: engine)
        text = str(audit)
        self.assertNotIn(secret, text)
        self.assertEqual(audit["statuses"]["lc0"]["error_type"], "RuntimeError")

    def test_allie_heavy_model_rejected_on_small_worker_before_load(self):
        with patch("fairplay_multimodel.available_model_memory_mb", return_value=7000):
            cfg, reason = model_config("allie_2", {
                "FAIRPLAY_ALLIE_2_MODEL_DIR": "/tmp/would-be-weights",
                "FAIRPLAY_ALLIE_2_REQUIRE_MEMORY_MB": "16000"})
        self.assertIsNone(cfg)
        self.assertIn("high-memory", reason)

    def test_synthetic_smoke_contains_no_real_account(self):
        result = synthetic_result()
        self.assertEqual(result.games[0].identity, "synthetic-only")
        self.assertEqual(result.games[0].decisions[0].fen, chess.Board().fen())
        self.assertEqual(result.priority, "LOW")

    def test_summary_handles_malformed_and_unknown(self):
        self.assertEqual(summarize_model_audit(None)["status"], "unavailable")
        self.assertEqual(summarize_model_audit(
            {"requested": "invalid", "statuses": []})["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
