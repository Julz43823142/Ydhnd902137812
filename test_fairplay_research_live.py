"""Offline contract tests for the privately delivered, resumable 200-account run."""
import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fairplay_research_live as research
from fairplay_checkpoint import CheckpointStore


class ResearchTests(unittest.TestCase):
    def test_public_candidate_file_is_valid_and_secretless(self):
        with patch.dict(os.environ, {}, clear=True):
            groups, digest = research.read_cohorts()
        self.assertEqual(len(groups["closed"]), 100)
        self.assertEqual(len(groups["comparison"]), 100)
        self.assertEqual(len(set(groups["closed"] + groups["comparison"])), 200)
        self.assertEqual(len(digest), 64)

    def test_no_usernames_in_public_status(self):
        async def run():
            with patch.object(research, "_github_status", return_value=True) as post:
                self.assertTrue(await research.report_status("pending", 73))
                state, desc = post.call_args.args
                self.assertEqual(state, "pending")
                self.assertIn("RUNNING 73/200", desc)
                self.assertIn("account 74/200", desc)
                self.assertNotIn("test-user", desc)
        asyncio.run(run())

    def test_encrypted_resume_cursor(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"FAIRPLAY_CHECKPOINT_KEY": "unit-test-key"}):
                path = str(Path(folder) / "private.enc")
                first = CheckpointStore(path=path, remote="")
                record = {"digest": "a" * 64, "cursor": 17, "rows": [
                    {"group": "closed", "account": "confidential"}], "finished": False}
                self.assertTrue(first.save_research(record))
                self.assertNotIn(b"confidential", Path(path).read_bytes())
                second = CheckpointStore(path=path, remote="")
                self.assertEqual(second.research_state(), record)

    def test_hold_is_reserved_before_queue_starts(self):
        async def run():
            service = SimpleNamespace(research_hold=asyncio.Event(), research_task=None)
            service.research_hold.set()
            async def worker(_):
                await asyncio.sleep(60)
            with patch.dict(os.environ, {"SHARKBOT_RESEARCH_COHORTS_B64": "present"}), patch.object(
                    research, "run_research", worker):
                research.start_research(service)
                self.assertFalse(service.research_hold.is_set())
                self.assertIsNotNone(service.research_task)
                service.research_task.cancel()
                await asyncio.gather(service.research_task, return_exceptions=True)
        asyncio.run(run())

    def test_resume_does_not_repeat_delivered_accounts(self):
        async def run():
            class Store:
                enabled = True
                def __init__(self):
                    self.state = {"digest": "cohort", "cursor": 1,
                                  "rows": [{"group": "closed", "account": "one",
                                            "status": "complete", "priority": "LOW",
                                            "partial": False}], "finished": False}
                def research_state(self):
                    return dict(self.state)
                def save_research(self, state):
                    self.state = dict(state)
                    return True
            store = Store()
            client = SimpleNamespace(is_closed=lambda: False)
            channel = SimpleNamespace(send=AsyncMock())
            called = []
            def analyzer(account, progress):
                called.append(account)
                return SimpleNamespace(priority="HIGH", confidence="HIGH",
                                       partial=False, selected_games=50)
            with ThreadPoolExecutor(max_workers=1) as pool:
                service = SimpleNamespace(checkpoints=store, client=client, channel=channel,
                                          executor=pool, analyzer=analyzer, closed=False,
                                          research_hold=asyncio.Event())
                async def no_delivery(*args, **kwargs):
                    return None
                with patch.object(research, "TOTAL", 2), patch.object(
                        research, "read_cohorts", return_value=(
                            {"closed": ["one"], "comparison": ["two"]}, "cohort")
                        ), patch.object(research, "owner_dm", new=no_delivery), patch.object(
                        research, "deliver_result", new=no_delivery), patch.object(
                        research, "report_status", new=no_delivery):
                    await research.run_research(service)
            self.assertEqual(called, ["two"])
            self.assertEqual(store.state["cursor"], 2)
            self.assertTrue(store.state["finished"])
            self.assertTrue(service.research_hold.is_set())
            channel.send.assert_awaited_once()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
