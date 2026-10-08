"""Synthetic regression tests: hosted worker rotation must not lose a long scan."""
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import bot
import fairplay_ui as ui
from test_fairplay import FakeChannel, ctx, TARGET


class RotationAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service=ui.FairPlayService(
            SimpleNamespace(user=SimpleNamespace(id=99),is_closed=lambda:False),
            FakeChannel())
    async def asyncTearDown(self):
        await self.service.close()

    async def test_ongoing_full_depth_scan_is_not_restarted_or_double_queued(self):
        existing=ui.Job(TARGET,ctx())
        self.service.jobs[TARGET]=existing
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','FAIRPLAY_FULL_DEPTH18':'1'}):
            duplicate=ctx()
            await self.service.enqueue(duplicate,TARGET)
            self.assertIn('already has a queued or running review',
                          duplicate.followup.send.call_args.args[0])
            newcomer=ctx()
            await self.service.enqueue(newcomer,'another-synthetic-account')
            self.assertIn('already running',newcomer.followup.send.call_args.args[0])
        self.assertEqual(self.service.jobs,{TARGET:existing})
        self.assertFalse(existing.stop.is_set())

    async def test_review_is_declined_near_runner_hard_limit_before_any_work(self):
        self.service.started_monotonic=(
            time.monotonic() -
            (ui._ACTION_RUN_MAX_SECONDS-ui._MAX_FULL_REVIEW_SECONDS
             -ui._ACTION_HANDOFF_MARGIN_SECONDS+60))
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','FAIRPLAY_FULL_DEPTH18':'1'}):
            event=ctx()
            await self.service.enqueue(event,TARGET)
        self.assertIn('remaining GitHub runner lifetime',
                      event.followup.send.call_args.args[0])
        self.assertFalse(self.service.jobs)

    async def test_recent_runner_still_accepts_a_full_depth_review(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','FAIRPLAY_FULL_DEPTH18':'1'}):
            event=ctx()
            with patch('feature_usage.note'):
                await self.service.enqueue(event,TARGET)
        self.assertIn(TARGET,self.service.jobs)

    async def test_rotation_drains_running_job_without_canceling_it(self):
        existing=ui.Job(TARGET,ctx())
        self.service.jobs[TARGET]=existing
        async def complete_after_poll(_):
            self.service.jobs.clear()
        with patch.object(bot.asyncio,'sleep',side_effect=complete_after_poll) as pause:
            result=await bot._await_fairplay_idle_before_rotation(
                self.service,time.monotonic()+100)
        self.assertTrue(result)
        self.assertTrue(self.service.draining)
        self.assertFalse(existing.stop.is_set())
        pause.assert_awaited_once_with(10)
        event=ctx()
        await self.service.enqueue(event,'another-synthetic-account')
        self.assertIn('changing workers',event.followup.send.call_args.args[0])

    async def test_deadline_boundary_returns_not_drained_and_never_claims_completion(self):
        existing=ui.Job(TARGET,ctx())
        self.service.jobs[TARGET]=existing
        result=await bot._await_fairplay_idle_before_rotation(
            self.service,time.monotonic()-1)
        self.assertFalse(result)
        self.assertTrue(self.service.draining)
        self.assertFalse(existing.stop.is_set())


class ActionsConcurrencyTests(unittest.TestCase):
    def test_daily_workflow_queues_replacement_instead_of_canceling(self):
        raw=Path('.github/workflows/daily_puzzle_and_answer.yml').read_text()
        self.assertIn('group: daily-puzzle',raw)
        self.assertIn('cancel-in-progress: false',raw)
        self.assertNotIn('cancel-in-progress: true',raw)
        self.assertIn('timeout-minutes: 350',raw)
        self.assertIn('DAILY_WORKFLOW_ROTATION_SECONDS: "15600"',raw)


if __name__=='__main__':
    unittest.main()
