"""Synthetic /stopfairplay command, queue and encrypted-checkpoint regressions.

The moderator command cancels exactly the running review, not the Actions
worker, other game systems or queued Fair Play reviews.
"""
import asyncio
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import bot
import fairplay_data as data
import fairplay_routing as routing
import fairplay_ui as ui
import shark_admin
from fairplay_checkpoint import CheckpointStore
from fairplay_config import CHANNEL_ID
from test_fairplay import FakeChannel, FakeMessage, TARGET, ctx


def moderator_context(*, owner=False, manage_server=False, administrator=False):
    interaction=ctx()
    interaction.user.id=(shark_admin.ADMIN_ID if owner else 42)
    interaction.user.guild_permissions=SimpleNamespace(
        manage_guild=manage_server, administrator=administrator)
    return interaction


class SlashPermissions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_service=ui._service
        self.channel=FakeChannel()
        self.service=ui.FairPlayService(
            SimpleNamespace(user=SimpleNamespace(id=99),is_closed=lambda:False),
            self.channel)
        ui._service=self.service

    async def asyncTearDown(self):
        await self.service.close()
        ui._service=self.old_service

    async def test_registered_command_and_gateway_whitelist(self):
        command=bot.command_tree.get_command('stopfairplay')
        self.assertIsNotNone(command)
        self.assertEqual(command.name,'stopfairplay')
        for typ in (2,4):
            self.assertTrue(routing.permitted({'channel_id':str(CHANNEL_ID),
                'type':typ,'data':{'name':'stopfairplay'}}))
            self.assertFalse(routing.permitted({'channel_id':str(CHANNEL_ID),
                'type':typ,'data':{'name':'status'}}))

    async def test_outside_fair_play_channel_refused(self):
        interaction=moderator_context(owner=True)
        interaction.channel_id=bot.PRIMARY_CHESS_CHANNEL_ID
        active=ui.Job(TARGET,None)
        self.service.active_job=active
        await ui.stop_current_review(interaction)
        self.assertFalse(active.stop.is_set())
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_regular_member_cannot_stop_anyone_else_review(self):
        interaction=moderator_context()
        active=ui.Job(TARGET,None)
        self.service.active_job=active
        await ui.stop_current_review(interaction)
        self.assertFalse(active.stop.is_set())
        interaction.response.defer.assert_not_awaited()
        self.assertIn('Only Sharkmeister',interaction.response.send_message.call_args.args[0])

    async def test_owner_server_manager_and_administrator_authorized(self):
        for flags in ({'owner':True},{'manage_server':True},{'administrator':True}):
            with self.subTest(flags=flags):
                interaction=moderator_context(**flags)
                with patch.object(self.service,'stop_current',new_callable=AsyncMock,
                                  return_value='stopping') as stop:
                    await ui.stop_current_review(interaction)
                    stop.assert_awaited_once_with()
                interaction.response.defer.assert_awaited_once()
                self.assertTrue(interaction.followup.send.call_args.kwargs['ephemeral'])
                self.assertIn('Stop requested',interaction.followup.send.call_args.args[0])

    async def test_idle_command_does_not_stop_pending_reviews(self):
        waiting=ui.Job(TARGET,ctx())
        self.service.jobs[TARGET]=waiting
        self.service.queue.put_nowait(waiting)
        interaction=moderator_context(owner=True)
        await ui.stop_current_review(interaction)
        self.assertFalse(waiting.stop.is_set())
        self.assertEqual(self.service.queue.qsize(),1)
        self.assertIn('No Fair Play review',interaction.followup.send.call_args.args[0])

    async def test_duplicate_stop_is_idempotent(self):
        active=ui.Job(TARGET,None)
        self.service.active_job=active
        self.assertEqual(await self.service.stop_current(),'stopping')
        self.assertTrue(active.stop.is_set())
        self.assertEqual(await self.service.stop_current(),'already')

    async def test_delivered_review_cannot_be_retroactively_cancelled(self):
        active=ui.Job(TARGET,None)
        active.report_published=True
        self.service.active_job=active
        self.assertEqual(await self.service.stop_current(),'idle')
        self.assertFalse(active.stop.is_set())

    async def test_missing_checkpoint_durability_is_explicit(self):
        active=ui.Job(TARGET,None)
        checkpoints=SimpleNamespace(enabled=True,cancel=Mock(return_value=False),
                                     finish=Mock(return_value=True),flush=Mock(return_value=True))
        self.service.active_job=active
        self.service.checkpoints=checkpoints
        result=await self.service.stop_current()
        self.assertEqual(result,'checkpoint_unconfirmed')
        self.assertTrue(active.stop.is_set())


class CancellationLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_service=ui._service
        self.channel=FakeChannel()
        self.service=ui.FairPlayService(
            SimpleNamespace(user=SimpleNamespace(id=99),is_closed=lambda:False),
            self.channel)
        ui._service=self.service

    async def asyncTearDown(self):
        await self.service.close()
        ui._service=self.old_service

    async def test_active_scan_stops_queued_next_runs_and_no_late_report(self):
        entered=threading.Event()
        started=[]
        def analyzer(target,progress,*,cancel):
            started.append(target)
            if target==TARGET:
                entered.set()
                self.assertTrue(cancel.wait(5))
                # Even a late, apparently successful analyzer must not
                # publish a result after an explicit stop.
                return SimpleNamespace(username=target,games=[],diagnostics={})
            raise data.ReviewError('Synthetic later review ended')
        self.service.analyzer=analyzer
        active=ui.Job(TARGET,ctx())
        pending=ui.Job('synthetic-next',ctx())
        for job in (active,pending):
            self.service.jobs[job.target]=job
            self.service.queue.put_nowait(job)
        self.service.worker=asyncio.create_task(self.service.run_queue())
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        self.assertIs(self.service.active_job,active)
        self.assertEqual(await self.service.stop_current(),'stopping')
        self.assertTrue(active.cancel_requested)
        self.assertFalse(pending.stop.is_set())
        await asyncio.wait_for(self.service.queue.join(),10)
        self.assertEqual(started,[TARGET,'synthetic-next'])
        self.assertFalse(self.service.results)
        self.assertFalse(self.service.cache)
        self.assertEqual(self.channel.sends,0)
        self.assertIsNone(self.service.active_job)

    async def test_cancelled_error_updates_existing_card_without_false_failure(self):
        entered=threading.Event()
        def analyzer(target,progress,*,cancel):
            entered.set()
            cancel.wait(5)
            raise data.ReviewError('Intentional interruption')
        self.service.analyzer=analyzer
        active=ui.Job(TARGET,ctx())
        active.message=FakeMessage(self.channel,555,ui.progress_embed(TARGET,'Fast engine scan'))
        self.channel.messages.append(active.message)
        self.service.jobs[TARGET]=active
        self.service.queue.put_nowait(active)
        self.service.worker=asyncio.create_task(self.service.run_queue())
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        self.assertEqual(await self.service.stop_current(),'stopping')
        await asyncio.wait_for(self.service.queue.join(),10)
        self.assertEqual(self.channel.sends,0)
        self.assertEqual(len(self.channel.messages),1)
        self.assertIn('Stopped by moderator',self.channel.messages[0].embeds[0].description)
        self.assertNotIn('Intentional interruption',self.channel.messages[0].embeds[0].description)
        self.assertFalse(self.service.results)

    async def test_shutdown_is_not_treated_as_explicit_moderator_stop(self):
        job=ui.Job(TARGET,None)
        self.service.jobs[TARGET]=job
        await self.service.close()
        self.assertTrue(job.stop.is_set())
        self.assertFalse(job.cancel_requested)


class CancelledCheckpoints(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'resume.enc')
        self.env=patch.dict(os.environ,{'FAIRPLAY_CHECKPOINT_KEY':'synthetic-stop-only-key',
                  'FAIRPLAY_CHECKPOINT_FILE':self.path})
        self.env.start()
        self.addCleanup(self.env.stop)

    def store(self):
        return CheckpointStore(path=self.path,remote='')

    def test_cancel_marker_survives_restart_and_cannot_be_reactivated(self):
        first=self.store()
        self.assertTrue(first.note(TARGET,token='synthetic'))
        self.assertEqual(len(first.pending()),1)
        self.assertTrue(first.cancel(TARGET))
        self.assertFalse(first.pending())
        self.assertFalse(first.note(TARGET,stage='Late scan progress'))
        self.assertTrue(first.suspend(TARGET))
        self.assertEqual(first.state['jobs'][TARGET]['status'],'cancelled')
        second=self.store()
        self.assertEqual(second.pending(),[])
        self.assertEqual(second.state['jobs'][TARGET]['status'],'cancelled')
        self.assertTrue(second.finish(TARGET))
        self.assertEqual(self.store().state['jobs'],{})
        self.assertTrue(second.note(TARGET,token='new-review'))
        self.assertEqual(len(second.pending()),1)

    def test_inflight_positions_do_not_revive_cancelled_job(self):
        from test_fairplay_checkpoint import game_with_positions, metrics
        from fairplay_config import CONFIG, VERSION
        first=self.store()
        first.note(TARGET)
        first.bind(TARGET,engine='Synthetic',version=VERSION,config=CONFIG,
                   full_depth=True,maia='synthetic-model')
        one=game_with_positions(1)
        one.decisions[0].metrics=metrics('deep')
        self.assertTrue(first.cancel(TARGET))
        self.assertFalse(first.record(TARGET,one,one.decisions[0],'deep'))
        self.assertFalse(first.note(TARGET,stage='Late finished engine result'))
        self.assertEqual(self.store().pending(),[])


if __name__=='__main__':unittest.main()
