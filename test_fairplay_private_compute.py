"""Synthetic transport tests: no private case identities or real network calls."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from scripts.fairplay_private_compute import generate_key, seal, unseal, wait_input


class PrivateComputeTransport(unittest.TestCase):
    def test_round_trip_only_recipient_can_decrypt(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary)/"first.pem", Path(temporary)/"second.pem"
            generate_key(first)
            generate_key(second)
            payload = {"schema": 1, "games": [{"case": 1, "private": "synthetic-only"}]}
            encrypted = seal(payload, first.with_suffix('.pub').read_bytes())
            self.assertNotIn(b'synthetic-only', encrypted)
            self.assertEqual(unseal(encrypted, first.read_bytes()), payload)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                unseal(encrypted, second.read_bytes())
            with self.assertRaises(InvalidTag):
                unseal(encrypted[:-1]+bytes([encrypted[-1]^1]), first.read_bytes())

    def test_existing_private_key_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"worker.pem"
            generate_key(path)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                generate_key(path)
            self.assertEqual(path.read_bytes(), before)

    def test_branch_is_opaque_not_arbitrary_git_syntax(self):
        with patch('scripts.fairplay_private_compute.subprocess.run') as command:
            for value in ('main', '--upload-pack=anything', 'validation-input-private-target', '../HEAD'):
                with self.assertRaises(ValueError):
                    wait_input(value, 0, 0)
            command.assert_not_called()

    def test_invalid_and_oversized_envelopes_rejected(self):
        with self.assertRaises(ValueError):
            unseal(b'not authenticated ciphertext', b'no key')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'worker.pem'; generate_key(path)
            with patch('scripts.fairplay_private_compute.MAX_BYTES', 10):
                with self.assertRaises(ValueError):
                    seal({'large': 'a'*20}, path.with_suffix('.pub').read_bytes())


if __name__ == '__main__':
    unittest.main()
