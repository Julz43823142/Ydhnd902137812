"""Synthetic-only closure alerts; no labels or private accounts in fixtures."""
import copy
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch
import discord
from fairplay_alert_store import AlertStore
from fairplay_ban_alerts import subscribe,unsubscribe,mute,poll,reserve,mark,Settings,FAIRPLAY_CLOSED
from fairplay_ban_alerts_ui import AlertService,AlertView
from fairplay_data import ReviewError

NOW=1700000100
class API:
    status='basic';calls=[];failed=False
    def __init__(self,deadline):pass
    def close(self):pass
    def get(self,target,suffix='',*,profile=False):
        self.calls.append((target,suffix,profile))
        if self.failed:raise ReviewError('Temporary API failure')
        if profile:return {'username':target,'status':self.status}
        if suffix=='/games/archives':return {'archives':[f'https://api.chess.com/pub/player/{target}/games/2023/11']}
        return {'games':[{'white':{'username':target},'black':{'username':'synthetic-opponent'},
                          'url':f'https://www.chess.com/game/live/{i}','end_time':1700000000+i} for i in (1,2)]}


class Alerts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'alerts.enc'
        self.store=AlertStore(self.path,remote='',secret='synthetic-test-key')
        API.status='basic';API.failed=False;API.calls=[]
    def tick(self,now=NOW):return poll(self.store,api_factory=API,now=now)
    def subscribe(self,user=1,target='synthetic-player'):
        with patch('fairplay_ban_alerts.time.time',return_value=NOW-1):subscribe(self.store,user,target)
    def event(self):
        self.subscribe();self.tick();API.status=FAIRPLAY_CLOSED;self.tick(NOW+3601)
        return next(iter(self.store.snapshot()['outbox']))
    def test_multiple_accounts_mute_and_unsubscribe_are_user_scoped(self):
        self.subscribe();self.subscribe(target='synthetic-second');self.subscribe(user=2)
        mute(self.store,1,True);mute(self.store,1,False)
        unsubscribe(self.store,1,'synthetic-player')
        s=self.store.snapshot()
        self.assertEqual(set(s['users']['1']['subscriptions']),{'synthetic-second'})
        self.assertIn('synthetic-player',s['users']['2']['subscriptions'])
        self.assertFalse(s['users']['1']['muted'])
    def test_pings_default_on_and_mute_survives_another_subscription(self):
        self.subscribe()
        self.assertFalse(self.store.snapshot()['users']['1']['muted'])
        mute(self.store,1,True)
        self.subscribe(target='synthetic-second')
        self.assertTrue(self.store.snapshot()['users']['1']['muted'])

    async def test_periodic_checks_never_publish_a_subscription_panel(self):
        channel=SimpleNamespace(send=AsyncMock())
        service=AlertService(SimpleNamespace(user=SimpleNamespace(id=5)),channel,self.store,Settings())
        with (patch('fairplay_ban_alerts_ui.poll',return_value={}) as check,
              patch.object(service,'deliver',new_callable=AsyncMock),
              patch('fairplay_ban_alerts_ui.asyncio.sleep',new_callable=AsyncMock,
                    side_effect=[None,asyncio.CancelledError])):
            with self.assertRaises(asyncio.CancelledError):await service.run()
            self.assertEqual(check.call_count,2)
        channel.send.assert_not_awaited()

    def test_existing_closures_baseline_no_old_spam(self):
        self.subscribe();API.status=FAIRPLAY_CLOSED;self.tick();self.tick(NOW+3601)
        self.assertFalse(self.store.snapshot()['outbox'])
    def test_exact_closure_transition_deduplicates_games_and_restart(self):
        key=self.event();event=self.store.snapshot()['outbox'][key]
        self.assertEqual(event['games'],2)
        self.assertEqual(event['most_recent_game'],1700000002)
        self.assertEqual(event['url'],'https://www.chess.com/game/live/2')
        self.assertIsNotNone(reserve(self.store,key));mark(self.store,key,'sent',123)
        self.store=AlertStore(self.path,remote='',secret='synthetic-test-key')
        self.tick(NOW+7202)
        self.assertEqual(len(self.store.snapshot()['outbox']),1)
        self.assertIsNone(reserve(self.store,key))
        self.assertNotIn(b'synthetic-opponent',self.path.read_bytes())
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
    def test_other_closures_and_api_errors_are_not_fairplay(self):
        self.subscribe();self.tick();API.status='closed:abuse';self.tick(NOW+3601)
        self.assertFalse(self.store.snapshot()['outbox'])
        API.failed=True;self.tick(NOW+7202)
        self.assertFalse(self.store.snapshot()['outbox'])
    def test_shared_opponents_only_one_profile_request_per_cycle(self):
        self.subscribe();self.subscribe(user=2,target='synthetic-second');self.tick()
        self.assertEqual(sum(profile for target,suffix,profile in API.calls),1)
        API.status=FAIRPLAY_CLOSED;self.tick(NOW+3601)
        self.assertEqual(len(self.store.snapshot()['outbox']),2)
    def test_unsubscribe_cancels_pending_delivery(self):
        key=self.event();unsubscribe(self.store,1,'synthetic-player')
        self.assertIsNone(reserve(self.store,key))
    def test_failed_durable_claim_cannot_send(self):
        key=self.event()
        with patch.object(self.store,'_save',return_value=False):
            with self.assertRaises(RuntimeError):reserve(self.store,key)
        self.assertEqual(self.store.snapshot()['outbox'][key]['state'],'pending')
        self.assertTrue(self.store.failed)
    def test_user_limit_error_does_not_disable_storage(self):
        self.subscribe()
        with self.assertRaises(ValueError):subscribe(self.store,1,'synthetic-second',Settings(max_subscriptions=1))
        self.assertFalse(self.store.failed)
        mute(self.store,1,True)
    async def test_delivery_mute_and_repeated_poll_no_duplicates(self):
        key=self.event();mute(self.store,1,True)
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=99)))
        service=AlertService(SimpleNamespace(user=SimpleNamespace(id=5)),channel,self.store,Settings())
        await service.deliver();await service.deliver()
        self.assertEqual(channel.send.await_count,1)
        self.assertIsNone(channel.send.call_args.kwargs['content'])
        self.assertFalse(channel.send.call_args.kwargs['allowed_mentions'].users)
        self.assertEqual(self.store.snapshot()['outbox'][key]['state'],'sent')
        view=channel.send.call_args.kwargs['view']
        self.assertEqual(len(view.children),5)
        self.assertEqual(view.children[0].label,'Subscribe')
    async def test_unknown_discord_ack_not_blindly_retried(self):
        key=self.event()
        async def history(**kwargs):
            for row in ():yield row
        channel=SimpleNamespace(send=AsyncMock(side_effect=TimeoutError()),history=history)
        service=AlertService(SimpleNamespace(user=SimpleNamespace(id=5)),channel,self.store,Settings())
        await service.deliver();await service.deliver()
        self.assertEqual(channel.send.await_count,1)
        self.assertEqual(self.store.snapshot()['outbox'][key]['state'],'sending')
    async def test_explicit_discord_rejection_can_retry(self):
        self.event()
        error=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),{'message':'rejected','code':0})
        channel=SimpleNamespace(send=AsyncMock(side_effect=[error,SimpleNamespace(id=99)]))
        service=AlertService(SimpleNamespace(user=SimpleNamespace(id=5)),channel,self.store,Settings())
        await service.deliver();await service.deliver()
        self.assertEqual(channel.send.await_count,2)
    async def test_unknown_ack_reconciles_by_own_message_marker(self):
        from fairplay_ban_alerts_ui import alert_embed
        key=self.event();event=reserve(self.store,key)
        card=alert_embed(event,key)
        async def history(**kwargs):
            yield SimpleNamespace(id=789,author=SimpleNamespace(id=5),embeds=[card])
        channel=SimpleNamespace(send=AsyncMock(),history=history)
        service=AlertService(SimpleNamespace(user=SimpleNamespace(id=5)),channel,self.store,Settings())
        await service.deliver()
        channel.send.assert_not_called()
        self.assertEqual(self.store.snapshot()['outbox'][key]['message_id'],789)

    def test_alert_embed_has_short_copy_order_and_readable_utc_date(self):
        from fairplay_ban_alerts_ui import alert_embed
        event={'target':'synthetic-player','opponent':'synthetic-opponent',
               'url':'https://www.chess.com/game/live/123','games':2,
               'detected':1792542419}
        card=alert_embed(event,'synthetic-key')
        self.assertEqual([field.name for field in card.fields],[
            'Followed Chess.com account','Opponent','Official status',
            'Public links','Times played','Detected by SharkBot','Get your own alerts'])
        self.assertEqual(card.fields[4].value,'2 games')
        event['most_recent_game']=1700000002
        self.assertEqual(alert_embed(event,'synthetic-key').fields[4].value,
                         '2 games\\nMost recent game: 14 November 2023'.replace('\\n','\n'))
        self.assertEqual(card.fields[5].value,'October 21, 2026')
        self.assertNotIn('Recorded meetings',str(card.to_dict()))
        self.assertNotIn('Alert ID',str(card.to_dict()))
        self.assertTrue(card.url.endswith('#sharkbot-alert-synthetic-key'))
        event['games']=1
        self.assertEqual(alert_embed(event,'synthetic-key').fields[4].value,
                         '1 game\\nMost recent game: 14 November 2023'.replace('\\n','\n'))
        del event['most_recent_game']
        self.assertEqual(alert_embed(event,'synthetic-key').fields[4].value,'1 game')

    async def test_old_footer_marker_still_reconciles(self):
        key=self.event();reserve(self.store,key)
        card=discord.Embed(title='Legacy alert')
        card.set_footer(text='Alert ID: '+key)
        async def history(**kwargs):
            yield SimpleNamespace(id=790,author=SimpleNamespace(id=5),embeds=[card])
        service=AlertService(SimpleNamespace(user=SimpleNamespace(id=5)),
            SimpleNamespace(send=AsyncMock(),history=history),self.store,Settings())
        await service.deliver()
        self.assertEqual(self.store.snapshot()['outbox'][key]['message_id'],790)

    def test_independent_git_writers_cannot_both_claim_a_delivery(self):
        import subprocess
        import os
        root=Path(self.temp.name);bare=root/'remote.git';work=root/'writer';work.mkdir()
        subprocess.run(['git','init','--bare',str(bare)],check=True,capture_output=True)
        subprocess.run(['git','init',str(work)],check=True,capture_output=True)
        subprocess.run(['git','remote','add','origin',str(bare)],cwd=work,check=True,capture_output=True)
        def git(args,data=None):
            env=os.environ.copy()
            env.update(GIT_AUTHOR_NAME='Synthetic',GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                       GIT_COMMITTER_NAME='Synthetic',GIT_COMMITTER_EMAIL='synthetic@example.invalid')
            return subprocess.run(['git',*args],input=data,cwd=work,env=env,capture_output=True,timeout=10)
        with patch('fairplay_alert_store._git',side_effect=git):
            initial=AlertStore(root/'initial.enc',remote='origin',secret='synthetic-test-key')
            subscribe(initial,1,'synthetic-player')
            # Two fresh processes see exactly the same authoritative revision.
            a=AlertStore(root/'a.enc',remote='origin',secret='synthetic-test-key')
            b=AlertStore(root/'b.enc',remote='origin',secret='synthetic-test-key')
            a.update(lambda s:s['outbox'].update(event={'user':'1','target':'synthetic-player',
                'state':'pending','attempts':0}))
            a=AlertStore(root/'a.enc',remote='origin',secret='synthetic-test-key')
            b=AlertStore(root/'b.enc',remote='origin',secret='synthetic-test-key')
            self.assertIsNotNone(reserve(a,'event'))
            with self.assertRaises(RuntimeError):reserve(b,'event')
            fresh=AlertStore(root/'fresh.enc',remote='origin',secret='synthetic-test-key')
            self.assertEqual(fresh.snapshot()['outbox']['event']['state'],'sending')

    def test_old_opponent_is_still_watched_months_after_game(self):
        self.subscribe();self.tick()
        API.status=FAIRPLAY_CLOSED
        self.tick(NOW+100*86400)
        self.assertEqual(len(self.store.snapshot()['outbox']),1)

    def test_poll_cancellation_keeps_state_unchanged(self):
        import threading
        self.subscribe();before=self.store.snapshot()
        cancel=threading.Event();cancel.set()
        result=poll(self.store,api_factory=API,now=NOW,cancel=cancel)
        self.assertTrue(result['cancelled'])
        self.assertEqual(before,self.store.snapshot())

    def test_subscription_added_during_poll_does_not_get_old_closure(self):
        self.subscribe();self.tick();API.status=FAIRPLAY_CLOSED
        original=API.get;added=[]
        def get(api,target,suffix='',*,profile=False):
            if profile and not added:
                added.append(True)
                with patch('fairplay_ban_alerts.time.time',return_value=NOW+3602):
                    subscribe(self.store,2,'synthetic-player')
            return original(api,target,suffix,profile=profile)
        with patch.object(API,'get',get):self.tick(NOW+3601)
        state=self.store.snapshot()
        self.assertNotIn('synthetic-opponent',state['users']['2']['subscriptions']['synthetic-player']['seen'])
        self.tick(NOW+7202)
        self.assertEqual({e['user'] for e in self.store.snapshot()['outbox'].values()},{'1'})

    def test_empty_or_oversized_state_never_silently_resets_receipts(self):
        self.subscribe()
        with patch('fairplay_alert_store.MAX_PLAINTEXT',16):
            with self.assertRaises(ValueError):AlertStore(self.path,remote='',secret='synthetic-test-key')
        self.path.write_bytes(b'')
        with self.assertRaises(ValueError):AlertStore(self.path,remote='',secret='synthetic-test-key')

    def test_persistent_namespace_and_review_independence(self):
        from fairplay_routing import permitted
        view=AlertView();self.assertIsNone(view.timeout)
        for item in view.children:
            self.assertTrue(item.custom_id.startswith('shark:fairplay:ban-alert:'))
            self.assertTrue(permitted({'channel_id':'1311445685492781186','type':3,'data':{'custom_id':item.custom_id}}))
        source=Path('fairplay_ban_alerts.py').read_text()
        self.assertNotIn('EngineScanner',source);self.assertNotIn('annotate_history',source)

if __name__=='__main__':unittest.main()
