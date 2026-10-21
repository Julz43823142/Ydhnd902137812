"""Regression tests for Fair Play channel routing."""

import ast
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fairplay_ui import FairPlayService, Job, CHANNEL_ID, STAFF_TEST_CHANNEL_ID


class ChannelRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_staff_submission_uses_staff_channel(self):
        staff = MagicMock()
        staff.id = STAFF_TEST_CHANNEL_ID
        ctx = MagicMock()
        ctx.channel = staff
        ctx.channel_id = STAFF_TEST_CHANNEL_ID
        ctx.user.id = 42
        service = FairPlayService(MagicMock(), MagicMock(), checkpoints=None)
        service.closed = False
        service.queue = MagicMock()
        service.queue.put_nowait = MagicMock()
        with patch("feature_usage.note"):
            await service.enqueue(ctx, "sample")
        job = service.jobs["sample"]
        self.assertIs(job.output_channel, staff)
        self.assertIs(service.queue.put_nowait.call_args.args[0], job)
        service.executor.shutdown(wait=False, cancel_futures=True)

    async def test_taskforce_submission_uses_taskforce_channel(self):
        taskforce = MagicMock()
        taskforce.id = CHANNEL_ID
        ctx = MagicMock()
        ctx.channel = taskforce
        ctx.channel_id = CHANNEL_ID
        ctx.user.id = 42
        service = FairPlayService(MagicMock(), taskforce, checkpoints=None)
        service.closed = False
        service.queue = MagicMock()
        with patch("feature_usage.note"):
            await service.enqueue(ctx, "sample")
        self.assertIs(service.jobs["sample"].output_channel, taskforce)
        service.executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
