"""Synthetic tests for all-game depth limits and owner-only evidence export."""
import asyncio
import gzip
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import bot
import fairplay_data as data
import fairplay_ui as ui
from fairplay_config import CONFIG
from fairplay_evidence_payload import evidence, verify
from shark_admin import ADMIN_ID
from test_fairplay_v4 import game, report
from test_fairplay import TARGET


class FullScope(unittest.TestCase):
    def test_default_limits_are_500_without_implicit_200_game_cap(self):
        self.assertEqual(CONFIG.history_games, 500)
        self.assertEqual(CONFIG.primary_engine_games, 500)
        self.assertEqual(data.collection_limit(), 500)
        self.assertEqual(data.primary_limit(), 500)
        self.assertGreaterEqual(CONFIG.max_archives, 36)

    def test_all_move_records_are_exported_and_verifiable(self):
        reviewed = report([game(i) for i in range(3)])
        expected = sum(len(g.decisions) for g in reviewed.games)
        package = evidence(reviewed)
        self.assertEqual(verify(package[0][1], package[1:]), (3, expected))
        head = json.loads(gzip.decompress(package[0][1]))
        self.assertEqual(head["schema"], "sharkbot-fairplay-evidence-v1")
        self.assertEqual(head["position_records"], expected)
        row = json.loads(gzip.decompress(package[1][1]))["games"][0]
        self.assertTrue(row["decisions"])
        self.assertIn("fen", row["decisions"][0])
        self.assertIn("metrics", row["decisions"][0])
        self.assertIn("fast_engine", row["decisions"][0])

    def test_tampering_with_export_is_detected(self):
        package = evidence(report([game(1)]))
        name, data_blob = package[1]
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify(package[0][1], [(name, data_blob + b"x")])

    def test_evidence_is_split_without_truncating_game_order(self):
        package = evidence(report([game(i) for i in range(53)]))
        self.assertEqual(len(package), 4)  # manifest + three 25-game chunks
        self.assertEqual(verify(package[0][1], package[1:])[0], 53)


class OwnerOnly(unittest.IsolatedAsyncioTestCase):
    async def test_owner_button_persistent_and_user_id_unauthorized(self):
        buttons = [x for x in ui.ReportView().children
                   if getattr(x, "custom_id", None) == "shark:fairplay:owner-evidence"]
        self.assertEqual(len(buttons), 1)
        self.assertTrue(ui.ReportView().is_persistent())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(id=1))
        await buttons[0].callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_only_exact_owner_id_gets_gzip_attachments(self):
        reviewed = report([game(4)])
        reviewed.diagnostics["_private_evidence"] = evidence(reviewed)
        old = ui._service
        ui._service = SimpleNamespace(result_for=Mock(return_value=reviewed))
        try:
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=ADMIN_ID),
                message=SimpleNamespace(id=555),
                response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()))
            button = next(x for x in ui.ReportView().children
                          if getattr(x, "custom_id", None) ==
                          "shark:fairplay:owner-evidence")
            await button.callback(interaction)
            interaction.response.defer.assert_awaited_once_with(
                ephemeral=True, thinking=True)
            self.assertEqual(interaction.followup.send.await_count,
                             len(reviewed.diagnostics["_private_evidence"]))
            for call in interaction.followup.send.call_args_list:
                self.assertTrue(call.kwargs["ephemeral"])
                self.assertTrue(call.kwargs["file"].filename.endswith(".json.gz"))
        finally:
            ui._service = old


if __name__ == "__main__":
    unittest.main()
