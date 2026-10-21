"""Regression tests for Fair Play channel routing."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import discord
import fairplay_ui as ui
from test_fairplay import FakeChannel, FakeMessage, TARGET, ctx

from fairplay_ui import FairPlayService, Job, CHANNEL_ID, STAFF_TEST_CHANNEL_ID


class ChannelRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def restore(self, item, *, staff_cached=True, fetch_error=None):
        taskforce = FakeChannel()
        staff = FakeChannel()
        staff.id = STAFF_TEST_CHANNEL_ID
        staff.messages.append(FakeMessage(staff, 444, ui.progress_embed(TARGET, 'Collecting games…')))
        client = SimpleNamespace(
            user=SimpleNamespace(id=99), is_closed=lambda:False, add_view=Mock(),
            get_channel=Mock(side_effect=lambda key: taskforce if key==CHANNEL_ID else staff if staff_cached else None),
            fetch_channel=AsyncMock(return_value=staff, side_effect=fetch_error))
        store = SimpleNamespace(enabled=True, pending=lambda:[item], auto_state=lambda:{},
                                note=Mock(return_value=True), finish=Mock(return_value=True))
        def discard_task(coro, **kwargs):
            coro.close()
            return Mock(done=lambda:True)
        with patch.object(ui, '_service', None), patch.object(ui, 'CheckpointStore', return_value=store), \
                patch.object(ui.asyncio, 'create_task', side_effect=discard_task):
            await ui.startup(client)
            service = ui._service
        self.addCleanup(service.executor.shutdown, wait=True, cancel_futures=True)
        return service, taskforce, staff, client, store

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

    async def test_staff_restart_preserves_progress_and_final_report_channel(self):
        service, taskforce, staff, _, _ = await self.restore(dict(
            target=TARGET, channel_id=STAFF_TEST_CHANNEL_ID, message_id=444))
        job = service.queue.get_nowait()
        self.assertIs(job.output_channel, staff)
        self.assertIs(job.message, staff.messages[0])
        result = SimpleNamespace(username=TARGET, games=[], diagnostics={})
        service.analyzer = lambda *a, **k:result
        with patch.object(ui, 'result_embed', return_value=discord.Embed(title='Synthetic final report')):
            await service.process(job)
        self.assertTrue(job.report_published)
        self.assertEqual(taskforce.sends, 0)
        self.assertEqual(staff.sends, 1)
        self.assertIs(job.message.channel, staff)

    async def test_taskforce_and_legacy_checkpoints_preserve_taskforce_route(self):
        for extra in ({'channel_id':CHANNEL_ID}, {}):
            with self.subTest(extra=extra):
                service, taskforce, _, _, _ = await self.restore(dict(target=TARGET, **extra))
                self.assertIs(service.jobs[TARGET].output_channel, taskforce)

    async def test_uncached_staff_channel_is_fetched_without_changing_route(self):
        service, _, staff, client, _ = await self.restore(
            dict(target=TARGET, channel_id=STAFF_TEST_CHANNEL_ID), staff_cached=False)
        client.fetch_channel.assert_awaited_once_with(STAFF_TEST_CHANNEL_ID)
        self.assertIs(service.jobs[TARGET].output_channel, staff)

    async def test_inaccessible_or_deleted_staff_channel_keeps_checkpoint_pending(self):
        for error_type, status in ((discord.Forbidden,403),(discord.NotFound,404)):
            with self.subTest(status=status):
                item = dict(target=TARGET, channel_id=STAFF_TEST_CHANNEL_ID)
                error = error_type(SimpleNamespace(status=status,reason='Unavailable'),'synthetic')
                service, taskforce, _, _, store = await self.restore(
                    item, staff_cached=False, fetch_error=error)
                self.assertFalse(service.jobs)
                self.assertTrue(service.queue.empty())
                self.assertEqual(taskforce.sends, 0)
                self.assertEqual(store.pending(), [item])
                store.finish.assert_not_called()

    async def test_unapproved_or_malformed_checkpoint_channel_is_not_redirected(self):
        for channel_id in (12345, 'invalid', '', False):
            with self.subTest(channel_id=channel_id):
                service, taskforce, _, client, _ = await self.restore(
                    dict(target=TARGET, channel_id=channel_id))
                self.assertFalse(service.jobs)
                self.assertEqual(taskforce.sends, 0)
                client.fetch_channel.assert_not_awaited()

    async def test_staff_uncertain_send_is_reconciled_in_its_own_channel(self):
        taskforce, staff = FakeChannel(), FakeChannel()
        staff.id = STAFF_TEST_CHANNEL_ID
        service = FairPlayService(SimpleNamespace(user=SimpleNamespace(id=99)), taskforce)
        self.addCleanup(service.executor.shutdown, wait=True, cancel_futures=True)
        job = Job(TARGET, ctx(staff), output_channel=staff)
        original_send = staff.send
        async def lost(**kwargs):
            await original_send(**kwargs)
            raise discord.HTTPException(SimpleNamespace(status=503,reason='Unavailable'),'synthetic')
        staff.send = lost
        self.assertFalse(await service.safe_progress(job, 'Collecting games…'))
        staff.send = original_send
        self.assertTrue(await service.safe_progress(job, 'Reviewing games…'))
        self.assertEqual(staff.sends, 1)
        self.assertEqual(taskforce.sends, 0)
        self.assertIs(job.message.channel, staff)


class FinalReportDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.taskforce, self.staff = FakeChannel(), FakeChannel()
        self.staff.id = STAFF_TEST_CHANNEL_ID
        self.result = SimpleNamespace(username=TARGET, priority='LOW',version='synthetic-version',
                                      games=[],diagnostics={})
        self.service = FairPlayService(SimpleNamespace(user=SimpleNamespace(id=99)), self.taskforce,
                                       analyzer=lambda *a, **k:self.result)
        self.job = Job(TARGET, ctx(self.staff), output_channel=self.staff)
        self.addCleanup(self.service.executor.shutdown, wait=True, cancel_futures=True)

    async def process(self):
        with patch.object(self.service, 'safe_progress', new_callable=AsyncMock, return_value=True), \
                patch.object(self.service, 'save_failure', new_callable=AsyncMock), \
                patch.object(ui, 'result_embed', return_value=discord.Embed(
                    title=ui.REPORT_PREFIX+TARGET,description='Synthetic completed report')):
            await self.service.process(self.job)

    async def test_accepted_report_with_lost_ack_is_recovered_without_duplicate(self):
        original = self.staff.send
        async def lost(**kwargs):
            await original(**kwargs)
            raise discord.HTTPException(SimpleNamespace(status=500,reason='Unavailable'),'synthetic')
        self.staff.send = lost
        # A progress card uses the same Review ID; it cannot be mistaken for
        # the final report even though both have the same title.
        progress = ui.progress_embed(TARGET, 'Posting final report…')
        progress.set_footer(text=f'Review ID: {self.job.token}')
        self.job.message = FakeMessage(self.staff, 555, progress)
        self.staff.messages.append(self.job.message)
        await self.process()
        self.assertTrue(self.job.report_published)
        self.assertEqual(self.staff.sends, 1)
        self.assertEqual(self.taskforce.sends, 0)
        self.assertEqual(len(self.staff.messages), 1)
        self.assertIs(self.service.result_for(self.job.message.id), self.result)

    async def test_timeout_after_acceptance_is_recovered_without_resending(self):
        original = self.staff.send
        async def lost(**kwargs):
            await original(**kwargs)
            raise TimeoutError('synthetic')
        self.staff.send = lost
        await self.process()
        self.assertTrue(self.job.report_published)
        self.assertEqual(self.staff.sends, 1)

    async def test_unresolved_connection_failure_does_not_resend_or_publish_progress(self):
        progress = ui.progress_embed(TARGET, 'Posting final report…')
        progress.set_footer(text=f'Review ID: {self.job.token}')
        self.job.message = FakeMessage(self.staff, 555, progress)
        self.staff.messages.append(self.job.message)
        self.staff.send = AsyncMock(side_effect=aiohttp.ServerDisconnectedError('synthetic'))
        await self.process()
        self.staff.send.assert_awaited_once()
        self.assertFalse(self.job.report_published)
        self.assertFalse(self.service.results)

    async def test_history_denied_after_lost_ack_does_not_send_fallback(self):
        original = self.staff.send
        async def lost(**kwargs):
            await original(**kwargs)
            raise discord.HTTPException(SimpleNamespace(status=503,reason='Unavailable'),'synthetic')
        async def denied(**kwargs):
            raise discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'synthetic')
            yield
        self.staff.send, self.staff.history = lost, denied
        await self.process()
        self.assertEqual(self.staff.sends, 1)
        self.assertFalse(self.job.report_published)

    async def test_payload_rejection_allows_one_smaller_fallback(self):
        original = self.staff.send
        attempts = []
        async def reject_full(**kwargs):
            attempts.append(kwargs)
            if len(attempts)==1:
                raise discord.HTTPException(SimpleNamespace(status=400,reason='Bad Request'),'synthetic')
            return await original(**kwargs)
        self.staff.send = reject_full
        await self.process()
        self.assertTrue(self.job.report_published)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.staff.sends, 1)
        self.assertIn('Short report', self.job.message.embeds[0].footer.text)

    async def test_permission_rejection_does_not_try_a_smaller_report(self):
        self.staff.send = AsyncMock(side_effect=discord.Forbidden(
            SimpleNamespace(status=403,reason='Forbidden'),'synthetic'))
        await self.process()
        self.staff.send.assert_awaited_once()
        self.assertFalse(self.job.report_published)


if __name__ == "__main__":
    unittest.main()
