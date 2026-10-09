"""Real-source adapter integrity: Allie 2.0, ChessMimic clocks, local HTTP.

All accounts, moves and service responses are synthetic. External weights
are not downloaded in CI; native inference is supplied by the official API
when a local operator installs the model separately.
"""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import chess

import fairplay_data
from fairplay_multimodel import (
    _RejectRedirects, _allie_clocks, model_config, run_external_models)
from test_fairplay import sample_row, TARGET
from test_fairplay_multimodel import fixture


class LocalModelAvailability(unittest.TestCase):
    def test_real_allie_2_requires_package_and_both_local_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            env={"FAIRPLAY_ALLIE_2_MODEL_DIR":str(folder)}
            conf,reason=model_config("allie_2",env)
            self.assertIsNone(conf)
            self.assertIn("weights",reason)
            (folder/"config.json").write_text("{}",encoding="utf-8")
            (folder/"model.safetensors").write_bytes(b"synthetic-not-real-weights")
            with patch("fairplay_multimodel.importlib.util.find_spec",return_value=None):
                conf,reason=model_config("allie_2",env)
                self.assertIsNone(conf)
                self.assertIn("not installed",reason)
            with patch("fairplay_multimodel.importlib.util.find_spec",return_value=object()):
                conf,reason=model_config("allie_2",env)
            self.assertEqual(conf,{"kind":"allie_local","directory":str(folder)})
            self.assertIsNone(reason)

    def test_allie_2_bridge_remains_optional_for_separately_hosted_model(self):
        config,reason=model_config("allie_2",{
            "FAIRPLAY_ALLIE_2_BRIDGE_URL":"http://127.0.0.1:8999"})
        self.assertEqual(config,{"kind":"bridge","url":"http://127.0.0.1:8999"})
        self.assertIsNone(reason)

    def test_offhost_model_redirect_is_explicitly_rejected(self):
        handler=_RejectRedirects()
        for destination in ("https://elsewhere.example/leak",
                            "http://127.0.0.1:9000/another-server"):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(ValueError,"must never redirect"):
                    handler.redirect_request(None,None,302,"Moved",{},destination)


class ClockAndAllieInference(unittest.TestCase):
    def test_chesscom_parser_keeps_opponent_clock_separate(self):
        game=fairplay_data.parse_game(sample_row(),TARGET)
        self.assertIsNotNone(game)
        self.assertTrue(any(d.opponent_clock_before is not None for d in game.decisions[1:]))
        # At a white player's third ply the last opposing observation
        # belongs to black, not a duplicate of the white player's clock.
        for d in game.decisions:
            if d.ply >= 3 and d.opponent_clock_before is not None:
                self.assertGreaterEqual(d.opponent_clock_before,0)

    def test_allie_clocks_only_reconstruct_observed_mover_clocks(self):
        game=fixture(1).games[0]
        earlier=game.decisions[0]
        earlier.clock_after=119
        earlier.ply=1
        recent=SimpleNamespace(ply=3,clock_after=116,
                               opponent_clock_before=118)
        game.decisions=[earlier,recent]
        self.assertEqual(_allie_clocks(game,recent),[119,118])

    def test_true_allie_2_local_api_yields_move_probability_and_think_time(self):
        game=fixture(1).games[0]
        game.color=True
        game.moves=["e2e4","e7e5","g1f3"]
        earlier=game.decisions[0]
        earlier.clock_after=119
        earlier.opponent_clock_before=None
        board=chess.Board()
        board.push_uci("e2e4");board.push_uci("e7e5")
        recent=SimpleNamespace(
            ply=3,fen=board.fen(),move="g1f3",useful=True,
            metrics={"nodes":24000},clock_before=119,clock_after=116,
            clock_valid=True,think=3.1,opponent_clock_before=118)
        game.decisions=[earlier,recent]
        human=SimpleNamespace(analyze=Mock(return_value={
            "moves":{"g1f3":0.7,"f1c4":0.3},"think_time":3.4}))
        with patch("fairplay_multimodel.model_config",return_value=(
                {"kind":"allie_local","directory":"/tmp/installed-allie2"},None)):
            result=fixture(1)
            result.games=[game]
            audit=run_external_models(result,env={
                "FAIRPLAY_EXTERNAL_MODELS":"allie_2"},allie_factory=lambda _:human)
        human.analyze.assert_called_once_with(
            ["e2e4","e7e5"],white_elo=1600,black_elo=1610,
            time_control="180+0",clocks=[119,118])
        self.assertEqual(audit["statuses"]["allie_2"]["status"],"evaluated")
        obs=audit["models"]["allie_2"]["observations"][0]
        self.assertEqual(obs["predicted"],"g1f3")
        self.assertEqual(obs["played_move_probability"],0.7)
        self.assertEqual(obs["predicted_think_seconds"],3.4)
        self.assertEqual(obs["actual_think_seconds"],3.1)
        self.assertEqual(obs["clock_observations"],2)
        self.assertEqual(result.priority,"LOW")

    def test_black_player_elo_not_swapped(self):
        game=fixture(1).games[0]
        game.color=False
        game.moves=["e2e4","e7e5"]
        board=chess.Board();board.push_uci("e2e4")
        move=game.decisions[0]
        move.ply=2
        move.fen=board.fen()
        move.move="e7e5"
        move.opponent_clock_before=121
        move.clock_after=119
        game.decisions=[move]
        human=SimpleNamespace(analyze=Mock(return_value={
            "moves":{"e7e5":0.6,"c7c5":0.4},"think_time":2.1}))
        with patch("fairplay_multimodel.model_config",return_value=(
                {"kind":"allie_local","directory":"/tmp/installed-allie2"},None)):
            audit=run_external_models(SimpleNamespace(games=[game]),env={
                "FAIRPLAY_EXTERNAL_MODELS":"allie_2"},allie_factory=lambda _:human)
        human.analyze.assert_called_once_with(
            ["e2e4"],white_elo=1610,black_elo=1600,
            time_control="180+0",clocks=[121])
        self.assertEqual(audit["statuses"]["allie_2"]["status"],"evaluated")

    def test_chessmimic_no_fake_opponent_clock_or_false_neural_attribution(self):
        game=fixture(1).games[0]
        game.decisions[0].opponent_clock_before=None
        posted=[]
        def send(url,payload):
            posted.append(payload)
            return {"move":"e4","thinking_time":2.5}
        audit=run_external_models(SimpleNamespace(games=[game]),env={
            "FAIRPLAY_EXTERNAL_MODELS":"chessmimic",
            "FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE":"1",
            "FAIRPLAY_CHESSMIMIC_URL":"http://127.0.0.1:8015",
        },http_get=lambda _:{"move_models":{"count":1}},http_post=send)
        self.assertEqual(posted[0]["clock_time"],120)
        self.assertIsNone(posted[0]["opponent_clock_time"])
        row=audit["models"]["chessmimic"]["observations"][0]
        self.assertFalse(row["opponent_clock_observed"])
        self.assertIn("opening DB",row["source"])


if __name__=="__main__":
    unittest.main()
