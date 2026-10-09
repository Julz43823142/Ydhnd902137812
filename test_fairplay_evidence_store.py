"""Private evidence survives worker restarts; only ciphertext reaches disk."""
import gzip
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import fairplay_private_extension
import fairplay_ui as ui
from fairplay_export_persistence import OwnerEvidenceStore
from shark_admin import ADMIN_ID


class EncryptedAuditPersistence(unittest.TestCase):
    def test_evidence_is_encrypted_restorable_and_not_plaintext(self):
        with tempfile.TemporaryDirectory() as path:
            target = str(Path(path) / "audit.enc")
            with patch.dict(os.environ, {"FAIRPLAY_CHECKPOINT_KEY": "synthetic-test-only"}):
                original = OwnerEvidenceStore(path=target, remote="")
                payload = gzip.compress(b'{"account":"synthetic-secret-account"}')
                self.assertTrue(original.save(123, [("part.json.gz", payload)]))
                stored = Path(target).read_bytes()
                self.assertNotIn(b"synthetic-secret-account", stored)
                recovered = OwnerEvidenceStore(path=target, remote="")
                self.assertEqual(recovered.get(123), [("part.json.gz", payload)])
                self.assertIsNone(recovered.get(999))

    def test_storage_requires_a_secret_and_never_writes_plaintext(self):
        with patch.dict(os.environ, {}, clear=True):
            store = OwnerEvidenceStore(remote="")
            self.assertFalse(store.save(1, [("audit.json.gz", b"secret")]))
            self.assertIsNone(store.get(1))


class RestartedOwnerButton(unittest.IsolatedAsyncioTestCase):
    async def test_owner_can_download_persisted_audit_without_live_result(self):
        original_service = ui._service
        ui._service = None
        try:
            with patch("fairplay_private_extension.OwnerEvidenceStore") as store:
                store.return_value.get.return_value = [
                    ("part.json.gz", gzip.compress(b'{"verified":true}'))]
                event = SimpleNamespace(
                    user=SimpleNamespace(id=ADMIN_ID),
                    message=SimpleNamespace(id=321),
                    response=SimpleNamespace(defer=AsyncMock(),
                                             send_message=AsyncMock()),
                    followup=SimpleNamespace(send=AsyncMock()))
                button = next(child for child in ui.ReportView().children
                    if getattr(child, "custom_id", "") == "shark:fairplay:owner-evidence")
                await button.callback(event)
                event.followup.send.assert_awaited_once()
                store.return_value.get.assert_called_once_with(321)
        finally:
            ui._service = original_service
