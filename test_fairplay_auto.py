import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
from fairplay_auto import AutoRescanner, report_identity, control
from fairplay_checkpoint import CheckpointStore
from fairplay_config import VERSION


def message(mid, target='example-player', version='fairplay-v49-old', author=123, complete=True):
    card = discord.Embed(title='🛡️ Fair Play Review — '+target,
                         description='LOW review priority' if complete else 'Fast scan 20/100')
    card.set_footer(text='Version: '+version)
    return NS(id=mid, author=NS(id=author), embeds=[card])


class Store:
    enabled=True
    def __init__(self):self.state={};self.reserved=[]
    def auto_state(self):return self.state.copy()
    def save_auto(self, state, **job):
        import copy
        self.state=copy.deepcopy(state)
        if job:self.reserved.append(job)
        return True


class Channel:
    def __init__(self, messages):self.messages=messages;self.calls=[]
    async def history(self, **kwargs):
        self.calls.append(kwargs)
        bound=getattr(kwargs.get('before'), 'id', float('inf'))
        for row in [m for m in self.messages if m.id<bound][:kwargs['limit']]:yield row


def service(messages):
    return NS(checkpoints=Store(), jobs={}, queue=asyncio.Queue(maxsize=6),
              cache={}, closed=False, channel=Channel(messages),
              client=NS(user=NS(id=123), is_closed=lambda:False))


class AutoTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_depth_route_rescans_old_depth_reports_only(self):
        from fairplay_config import DEPTH_VERSION
        s=service([message(3,'already-current',DEPTH_VERSION),
                   message(2,'previous-depth','fairplay-v52-depth-review-priority')])
        with patch.dict('os.environ',{'FAIRPLAY_V51':'1'}):
            a=AutoRescanner(s)
            await a.set_enabled(True)
            self.assertEqual(a.state['version'],DEPTH_VERSION)
            await a.tick()
        self.assertEqual(list(s.jobs),['previous-depth'])

    async def test_newest_first_dedup_current_version_and_finished_only(self):
        s=service([message(9,'current',VERSION), message(8,'current'),
                   message(7,'synthetic-first'),message(6,'synthetic-first'),message(5,'synthetic-next'),
                   message(4,'forged',author=999),message(3,'loading',complete=False)])
        a=AutoRescanner(s)
        await a.set_enabled(True)
        await a.tick()
        self.assertEqual(list(s.jobs), ['synthetic-first'])
        await a.tick()  # never overlap background jobs
        self.assertEqual(len(s.checkpoints.reserved), 1)
        s.jobs.clear();s.queue.get_nowait()
        await a.tick()
        self.assertEqual(list(s.jobs), ['synthetic-next'])
        s.jobs.clear();s.queue.get_nowait()
        await a.tick()
        self.assertTrue(a.state['exhausted'])
        self.assertEqual(len(s.checkpoints.reserved), 2)

    async def test_stop_keeps_active_and_prevents_followup(self):
        s=service([message(2),message(1,'second')]);a=AutoRescanner(s)
        await a.set_enabled(True);await a.tick();await a.set_enabled(False)
        self.assertEqual(len(s.jobs),1)
        s.jobs.clear();s.queue.get_nowait();await a.tick()
        self.assertEqual(len(s.checkpoints.reserved),1)

    async def test_manual_jobs_and_recent_cache_take_priority(self):
        s=service([message(2)]);a=AutoRescanner(s);await a.set_enabled(True)
        s.jobs['manual']=object();await a.tick()
        self.assertFalse(s.channel.calls)
        s.jobs.clear();s.cache['example-player']=object();await a.tick()
        self.assertFalse(s.checkpoints.reserved)

    async def test_restart_continues_cursor_and_version_upgrade_restarts_pass(self):
        s=service([message(3,'first'),message(2,'first'),message(1,'second')])
        a=AutoRescanner(s);await a.set_enabled(True);await a.tick()
        s.jobs.clear();s.queue.get_nowait()
        b=AutoRescanner(s);await b.tick()
        self.assertEqual(list(s.jobs),['second'])
        s.jobs.clear();s.queue.get_nowait();b.state['version']='fairplay-v49-old'
        await b.tick()
        self.assertEqual(list(s.jobs),['first'])

    async def test_history_pagination_does_not_revisit_messages(self):
        rows=[NS(id=i,author=NS(id=999),embeds=[]) for i in range(102,1,-1)]
        s=service(rows+[message(1)]);a=AutoRescanner(s)
        await a.set_enabled(True);await a.tick();self.assertFalse(s.jobs)
        await a.tick();self.assertTrue(s.jobs)
        self.assertEqual(s.channel.calls[1]['before'].id,3)

    async def test_no_storage_fails_closed_and_write_failure_never_enqueues(self):
        s=service([message(1)]);s.checkpoints.enabled=False;a=AutoRescanner(s)
        self.assertIn('require',await a.set_enabled(True));await a.tick()
        self.assertFalse(s.jobs)
        s.checkpoints.enabled=True;await a.set_enabled(True)
        s.checkpoints.save_auto=lambda *a,**kw:False
        with self.assertRaises(RuntimeError):await a.tick()
        self.assertFalse(s.jobs);self.assertTrue(s.queue.empty())
        self.assertFalse(a.state['enabled'])

    async def test_no_reactivation_after_stop_restart(self):
        s=service([message(1)]);a=AutoRescanner(s)
        await a.set_enabled(True);await a.set_enabled(False)
        b=AutoRescanner(s);await b.tick();self.assertFalse(s.jobs)

    async def test_manual_same_target_cannot_race_reservation(self):
        s=service([message(1)]);a=AutoRescanner(s);await a.set_enabled(True)
        original=a.persist
        async def persist(**job):
            self.assertIn('example-player',s.jobs)
            await asyncio.sleep(0)
            return await original(**job)
        a.persist=persist
        await a.tick();self.assertEqual(s.queue.qsize(),1)

    async def test_new_manual_completion_below_cursor_is_not_repeated(self):
        s=service([message(2,'first'),message(1,'second')]);a=AutoRescanner(s)
        await a.set_enabled(True);await a.tick()
        await a.completed('second')
        s.jobs.clear();s.queue.get_nowait();await a.tick()
        self.assertEqual(len(s.checkpoints.reserved),1)

    async def test_future_version_is_not_downgraded(self):
        s=service([message(1,version='fairplay-v999-future')]);a=AutoRescanner(s)
        await a.set_enabled(True);await a.tick();self.assertFalse(s.jobs)

    async def test_owner_permission_denies_admins_with_other_ids(self):
        import sys
        fake=NS(ADMIN_ID=42)
        ctx=NS(user=NS(id=43),response=NS(send_message=AsyncMock()))
        with patch.dict(sys.modules,{'shark_admin':fake}), patch('fairplay_ui.channel_check',AsyncMock(return_value=True)):
            await control(ctx,True)
        self.assertIn('Only Sharkmeister',ctx.response.send_message.call_args.args[0])


class IdentityTests(unittest.TestCase):
    def test_normalization_and_malformed_unknown_versions(self):
        self.assertEqual(report_identity(message(1,'EXAMPLE-player'),123)[0],'example-player')
        self.assertIsNone(report_identity(message(1,version='unknown'),123))
        self.assertIsNone(report_identity(message(1,target='https://evil.invalid'),123))
        self.assertIsNone(report_identity(message(1,author=999),123))

    def test_slash_fairplay_is_direct_username_command(self):
        import bot
        command=bot.command_tree.get_command('fairplay')
        self.assertIsInstance(command,discord.app_commands.Command)
        self.assertNotIsInstance(command,discord.app_commands.Group)
        self.assertEqual([parameter.name for parameter in command.parameters],['username'])
        self.assertIsNone(bot.command_tree.get_command('fairplay_auto'))
        from fairplay_routing import permitted
        from fairplay_config import CHANNEL_ID
        self.assertTrue(permitted({'type':2,'channel_id':str(CHANNEL_ID),
                                   'data':{'name':'fairplay'}}))

    def test_atomic_encrypted_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{'FAIRPLAY_CHECKPOINT_KEY':'synthetic-test-key'}):
            path=Path(folder)/'private.enc'
            store=CheckpointStore(path=path,remote='')
            state={'enabled':True,'version':VERSION,'before':123,'seen':['synthetic-player']}
            self.assertTrue(store.save_auto(state,target='synthetic-player',token='a'*12))
            self.assertNotIn(b'synthetic-player',path.read_bytes())
            restored=CheckpointStore(path=path,remote='')
            self.assertEqual(restored.auto_state(),state)
            self.assertEqual(restored.pending()[0]['target'],'synthetic-player')
            restored.finish('synthetic-player')
            self.assertEqual(restored.auto_state(),state)
