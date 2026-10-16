"""Regression tests for protected public game-state persistence."""
import json
import os
import unittest
from unittest.mock import patch

from shark_state_privacy import is_sealed, open_state, seal_state


class PublicPuzzleStateSecurity(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "current_puzzle": {
                "fen": "public board",
                "all_moves": [{"uci": "a1a2"}],
                "player_moves": [{"uci": "a1a2"}],
                "pgn": "secret-moves",
            },
            "puzzle_racers_v1": {"games": {"match": {
                "data": {"puzzles": [{"expected_uci": "a1a2"}]}
            }}},
            "teams": {"alpha": {"current": {
                "puzzle": {"solution": ["a1a2"]}
            }}},
            "coins": 100,
        }

    def test_roundtrip_and_no_plain_answers(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "test-token-only",
                                     "SHARK_STATE_ENCRYPTION_KEY": ""}):
            sealed = seal_state(self.snapshot)
            wire = json.dumps(sealed)
            self.assertTrue(is_sealed(sealed))
            self.assertNotIn("a1a2", wire)
            self.assertNotIn("secret-moves", wire)
            self.assertEqual(open_state(json.loads(wire)), self.snapshot)
            self.assertEqual(seal_state(self.snapshot), sealed)
            self.assertEqual(sealed["current_puzzle"]["fen"], "public board")

    def test_no_secret_fails_closed(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "",
                                     "SHARK_STATE_ENCRYPTION_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "key missing"):
                seal_state(self.snapshot)

    def test_wrong_key_never_erases_state(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "first-token",
                                     "SHARK_STATE_ENCRYPTION_KEY": ""}):
            sealed = seal_state(self.snapshot)
        with patch.dict(os.environ, {"DISCORD_TOKEN": "new-token",
                                     "SHARK_STATE_ENCRYPTION_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "Cannot decrypt"):
                open_state(sealed)

    def test_stable_dedicated_key_tolerates_discord_token_rotation(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "first-token",
                                     "SHARK_STATE_ENCRYPTION_KEY": "stable-private-key"}):
            sealed = seal_state(self.snapshot)
        with patch.dict(os.environ, {"DISCORD_TOKEN": "new-token",
                                     "SHARK_STATE_ENCRYPTION_KEY": "stable-private-key"}):
            self.assertEqual(open_state(sealed), self.snapshot)

    def test_plain_history_detected(self):
        self.assertFalse(is_sealed(self.snapshot))
        self.assertTrue(is_sealed({"only_public_metadata": {"fen": "public"}}))


if __name__ == "__main__":
    unittest.main()
