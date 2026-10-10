"""Synthetic-only private failure reports; no real account data or DMs."""
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord

from fairplay_checkpoint import CheckpointStore
from fairplay_data import ReviewError
from fairplay_failure import (FailureView, _embed, classify, failure_record,
                              owner_diagnostics, send_report, version_metadata)
from fairplay_progress import LiveTiming
from fairplay_routing import permitted
from shark_admin import ADMIN_ID


class SanitizationTests(unittest.TestCase):
    def test_reason_and_runtime_never_expose_exception_text_or_private_player(self):
        job=SimpleNamespace(token="a"*12,stage="Depth-18 rapid/blitz 12/50",
                            progress_percent=83,timing_elapsed=lambda:3700.0)
        err=ReviewError("secret-player@example.org/FEN/PUBLIC")
        pool=SimpleNamespace(last_failure={
            "category":"timeout","phase":"deep-candidates","attempt":2,
            "budget":18,"multipv":3,"elapsed_seconds":1200,
            "timeout_seconds":3600,"terminal":True,"private_fen":"secret"})
        record=failure_record(job,err,pool=pool,checkpoints=SimpleNamespace(
            enabled=True,remote_ok=True),
            env={"GITHUB_SHA":"f"*40,"GITHUB_REPOSITORY":"owner/thing",
                 "GITHUB_RUN_ID":"1234","GITHUB_RUN_ATTEMPT":"2",
                 "FAIRPLAY_FULL_DEPTH18":"1"})
        self.assertEqual(record["stage"],"depth18")
        self.assertEqual(record["git_commit"],"f"*40)
        self.assertEqual(record["github_run_url"],
                         "https://github.com/owner/thing/actions/runs/1234")
        self.assertEqual(record["search_contract"]["deep_multipv"],3)
        self.assertEqual(record["engine_last_failure"]["phase"],"deep-candidates")
        self.assertNotIn("private_fen",str(record))
        self.assertNotIn("secret-player",str(record))
        self.assertNotIn("FEN",str(record))
        self.assertIsNone(record["merged_pr_number"])

    def test_exception_chain_identifies_timeout_but_not_raw_message(self):
        try:
            try:
                raise TimeoutError("secret player")
            except TimeoutError as error:
                raise ReviewError("A Stockfish search timed out") from error
        except ReviewError as err:
            self.assertEqual(classify(err),"stockfish_search_timeout")

    def test_malformed_build_environment_cannot_produce_unsafe_links(self):
        info=version_metadata({"GITHUB_SHA":"../../etc/private",
                              "GITHUB_REPOSITORY":"owner/repo;secret",
                              "GITHUB_RUN_ID":"1abc"})
        self.assertIsNone(info["git_commit"])
        self.assertIsNone(info["github_run_url"])

    def test_gateway_allows_private_owner_diagnostic_command(self):
        self.assertTrue(permitted({"channel_id":"1311445685492781186",
                                   "type":2,"data":{"name":"fairplaydiagnostic"}}))


class EncryptedFailures(unittest.TestCase):
    def test_failure_survives_store_reload_job_finish_without_plaintext(self):
        with TemporaryDirectory() as directory, patch.dict(
                os.environ,{"DISCORD_TOKEN":"synthetic-test-secret",
                            "FAIRPLAY_CHECKPOINT_KEY":""}):
            path=Path(directory)/"state.enc"
            first=CheckpointStore(path=path,remote="")
            self.assertTrue(first.record_failure("a"*12,{"reason_code":"stockfish_search_timeout",
                                                        "git_commit":"f"*40}))
            self.assertFalse(first.record_failure("not-a-token",{"x":1}))
            self.assertNotIn(b"stockfish_search_timeout",path.read_bytes())
            first.finish("synthetic-account")
            again=CheckpointStore(path=path,remote="")
            self.assertEqual(again.get_failure("a"*12)["reason_code"],
                             "stockfish_search_timeout")
            self.assertEqual(again.recent_failures()[0][0],"a"*12)
            self.assertEqual(again.pending(),[])


class OwnerAccess(unittest.IsolatedAsyncioTestCase):
    async def test_nonowner_cannot_read_report_even_with_valid_review_id(self):
        interaction=SimpleNamespace(user=SimpleNamespace(id=14),
            response=SimpleNamespace(send_message=AsyncMock()))
        await send_report(interaction,"a"*12)
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_nonowner_cannot_list_diagnostics(self):
        interaction=SimpleNamespace(user=SimpleNamespace(id=14),
            response=SimpleNamespace(send_message=AsyncMock()))
        await owner_diagnostics(interaction)
        interaction.response.send_message.assert_awaited_once()

    async def test_persistent_button_refuses_other_accounts(self):
        view=FailureView()
        item=next(x for x in view.children if x.custom_id.endswith("failure-insights"))
        owner_dm=_embed({"reason_code":"stockfish_search_timeout",
                          "stage":"deep","elapsed_text":"50m",
                          "git_commit":"f"*40,"review_id":"a"*12})
        ctx=SimpleNamespace(user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(embeds=[owner_dm]))
        await item.callback(ctx)
        ctx.response.send_message.assert_awaited_once()
        self.assertTrue(ctx.response.send_message.call_args.kwargs["ephemeral"])
        self.assertNotEqual(ctx.user.id,ADMIN_ID)


if __name__=="__main__":
    unittest.main()
