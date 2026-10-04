"""Shared market/report transactions against disposable Git; scheduling and UX rules."""
import asyncio
import copy
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

import discord
import bot
import economy_analytics as economy
import economy_health
import feature_usage
import market_listings as listings
import next_batch_ui as ui
import pet_market
import pets
import shark_activity as activity
import shark_publications as publications
import shark_reports as reports
import shared_leaderboard as ledger
import showcase_cards
import test_economy_market as fixture
import twitch_presentation as live
from holiday_events import HOLIDAY_ZONE
from test_profile_previews import interaction


class Transactions(unittest.TestCase):
    git=fixture.MarketTransactions.git
    origin=fixture.MarketTransactions.origin
    pet=fixture.MarketTransactions.pet
    seed=fixture.MarketTransactions.seed
    def setUp(self):
        self.old_live_cache=live._cache
        fixture.MarketTransactions.setUp(self)

    def tearDown(self):
        live._cache=self.old_live_cache
        fixture.MarketTransactions.tearDown(self)

    def listing(self,key='sale',price=70):
        row={'trade_id':key,'seller_id':'42','seller_name':'Shark','offer':{'type':'pet','pet_id':'a'},
             'request':{'type':'coins','amount':price},'status':'open','created_at':self.now,'channel_id':1,'guild_id':1,'message_id':'2'}
        listings.register([row],'daily');return row

    def wanted(self,uid=99):return pet_market.create_wanted(uid,'Buyer','Dog',80,'wanted:'+str(uid))

    def test_matching_respects_filters_price_balance_ownership_and_capacity(self):
        row=self.listing();wanted=self.wanted()
        data=pet_market.state();wallet,_=ledger._origin_state();pets_data=pets._read_origin()
        match=listings.find_matches(data,wallet,pets_data,uid=99)
        self.assertEqual((len(match),match[0]['price']), (1,70))
        bad=copy.deepcopy(data);bad['wanted'][wanted['id']]['coins']=69
        self.assertFalse(listings.find_matches(bad,wallet,pets_data))
        bad=copy.deepcopy(data);bad['wanted'][wanted['id']]['evolution']='Evolved'
        self.assertFalse(listings.find_matches(bad,wallet,pets_data))
        wallet['99']['coins']=69;self.assertFalse(listings.find_matches(data,wallet,pets_data))
        wallet['99']['coins']=200;pets_data['99']['pets']=[self.pet('b'+str(i)) for i in range(10)]
        self.assertFalse(listings.find_matches(data,wallet,pets_data))
        pets_data['99']['pets']=[];pets_data['42']['pets']=[]
        self.assertFalse(listings.find_matches(data,wallet,pets_data))

    def test_match_purchase_is_atomic_idempotent_and_closes_both_listings(self):
        self.listing();wanted=self.wanted()
        receipt=listings.buy_match(99,'Buyer',wanted['id'],'sale')
        self.assertEqual(receipt['request']['amount'],70)
        self.assertEqual(listings.buy_match(99,'Buyer',wanted['id'],'sale'),receipt)
        wallet=self.origin(ledger.LEGACY_FILE)
        self.assertEqual((wallet['42']['coins'],wallet['99']['coins']), (170,130))
        data=pet_market.state();self.assertEqual(data['open']['sale']['status'],'completed');self.assertEqual(data['wanted'][wanted['id']]['status'],'filled')
        self.assertEqual(pets.current_pet(pets.get_owner(99))['id'],'a')
        with self.assertRaises(ValueError):listings.buy_match(77,'Other',wanted['id'],'sale')

    def test_concurrent_matches_sell_one_pet_once(self):
        self.listing();first=self.wanted(99);second=self.wanted(77)
        def buy(uid,row):
            try:return listings.buy_match(uid,'Buyer',row['id'],'sale')
            except ValueError:return None
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(buy,99,first),pool.submit(buy,77,second)]
            results=[f.result() for f in futures]
        self.assertEqual(sum(r is not None for r in results),1)
        wallet=self.origin(ledger.LEGACY_FILE);self.assertEqual(wallet['42']['coins'],170)
        self.assertEqual(sum(w['coins'] for w in wallet.values()),500)

    def test_listing_exact_expiry_and_tampered_terms_fail_without_wallet_changes(self):
        row=self.listing();self.now+=listings.LIFETIME
        before=self.origin(ledger.LEGACY_FILE)
        with self.assertRaisesRegex(ValueError,'expired'):
            ledger.accept_open_trade(42,'Shark',99,'Buyer',row['offer'],row['request'],'expired-accept','sale')
        self.assertEqual(self.origin(ledger.LEGACY_FILE),before)
        self.now-=listings.LIFETIME
        with self.assertRaisesRegex(ValueError,'terms changed'):
            ledger.accept_open_trade(42,'Shark',99,'Buyer',row['offer'],{'type':'coins','amount':1},'tampered','sale')

    def test_expired_relist_has_fresh_identity_and_rechecks_owner(self):
        self.listing();self.now+=listings.LIFETIME
        # Keep test pet alive without granting XP.
        self.data=pets._read_origin();self.data['42']['pets'][0].update(fed_at=self.now,happy_at=self.now)
        self.seed();listings.expire()
        new=listings.relist(42,'open','sale')
        self.assertNotEqual(new['trade_id'],'sale');self.assertEqual(new['expires_at']-new['created_at'],14*pets.DAY)
        self.assertEqual(listings.relist(42,'open','sale'),new)
        with self.assertRaises(ValueError):listings.relist(99,'open','sale')

    def test_wanted_migration_extends_once_without_renewing_creation(self):
        wanted=self.wanted();data=pet_market.state();data['wanted'][wanted['id']]['expires_at']=self.now+7*pets.DAY
        ledger._push_files({pet_market.FILE:json.dumps(data)},'Old lifetime');ledger._fetch_retry()
        listings.migrate();self.now+=pets.DAY;listings.migrate()
        row=pet_market.state()['wanted'][wanted['id']]
        self.assertEqual(row['expires_at'],wanted['created_at']+14*pets.DAY)

    def test_outbox_freezes_payload_and_deduplicates_match_notification(self):
        build=Mock(return_value={'embed':discord.Embed(title='Match').to_dict()})
        first=publications.prepare('match:wanted:sale',build)
        second=publications.prepare('match:wanted:sale',build)
        self.assertEqual(first,second);build.assert_called_once()
        with ThreadPoolExecutor(2) as pool:
            claims=list(pool.map(lambda token:publications.update('match:wanted:sale','sending',claim=token),['one','two']))
        self.assertEqual(claims[0]['claim'],claims[1]['claim'])
        publications.update('match:wanted:sale','sent',message_id=123)
        self.assertEqual(publications.update('match:wanted:sale','failed')['status'],'sent')

    def test_uncertain_match_ack_does_not_duplicate_sale(self):
        self.listing();wanted=self.wanted();original=ledger._push_files
        def uncertain(files,message):
            original(files,message);return False
        # Staged ledger writers require successful staging, so lose only the outer publish ACK.
        import repository_transaction
        real=repository_transaction._publish
        def lost(base,files):real(base,files);return False
        with patch.object(repository_transaction,'_publish',lost):
            listings.buy_match(99,'Buyer',wanted['id'],'sale')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],130)
        self.assertEqual(len(economy._load()['sales']),1)

    def test_conflict_rebuild_rechecks_buyer_balance(self):
        self.listing();wanted=self.wanted();import repository_transaction
        real=repository_transaction._publish;attempt=[0]
        def conflict(base,files):
            attempt[0]+=1
            if attempt[0]==1:
                wallet,_=ledger._origin_state();wallet['99']['coins']=2
                # The outer staging context has ended before publish; this is a separate committed writer.
                ledger._push_files({ledger.LEGACY_FILE:ledger._snapshot_json(wallet)},'Concurrent spend');return False
            return real(base,files)
        with patch.object(repository_transaction,'_publish',conflict),self.assertRaises(ValueError):
            listings.buy_match(99,'Buyer',wanted['id'],'sale')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],100)
        self.assertEqual(pets.current_pet(pets.get_owner(42))['id'],'a')

    def test_immutable_week_snapshot_survives_later_wallet_changes(self):
        economy_data=economy._load();key='2026-W39';economy_data['weeks'][key]={'opening_supply':500,'closing_supply':510,'minted':10,'burned':0,'active':['42']}
        ledger._push_files({economy.FILE:json.dumps(economy_data)},'Seed weekly observations');ledger._fetch_retry()
        economy.freeze_completed_weeks(self.now)
        frozen=copy.deepcopy(economy._load()['weekly_snapshots'][key])
        ledger.credit_coins(42,'Shark',5,'later-credit',source='test')
        self.now+=pets.DAY;economy.freeze_completed_weeks(self.now)
        self.assertEqual(economy._load()['weekly_snapshots'][key],frozen)

    def test_live_offline_live_same_stream_id_and_no_cosmetic_changes(self):
        before=self.origin(ledger.LEGACY_FILE)
        live.set_status({'id':'stream'});self.assertTrue(live.live())
        live.set_status(None);self.assertFalse(live.live())
        live.set_status({'id':'stream'});self.assertTrue(live.live())
        self.assertEqual(self.origin(ledger.LEGACY_FILE),before)

    def test_aggregate_usage_is_admin_only_and_persists_no_raw_identity(self):
        from shark_admin import ADMIN_ID
        feature_usage._pending.clear();feature_usage._sketches.clear();feature_usage._batch=None
        feature_usage.note('page:economy',42,self.now);feature_usage.note('page:economy',42,self.now)
        feature_usage.note('page:economy',99,self.now);feature_usage.flush()
        rows=feature_usage.summary(ADMIN_ID,7,self.now)
        self.assertEqual((rows[0]['count'],rows[0]['unique']), (3,2))
        self.assertNotIn('user_id',json.dumps(self.origin(feature_usage.FILE)))
        with self.assertRaises(PermissionError):feature_usage.summary(42,7,self.now)

    def test_scheduled_wrapped_newspaper_monthly_event_and_milestone_publish_once(self):
        self.now=datetime(2026,12,5,tzinfo=HOLIDAY_ZONE).timestamp()
        act={'tracking_since':datetime(2026,10,1,tzinfo=HOLIDAY_ZONE).timestamp(),'days':{},'totals':{},'records':{}}
        activity.add(act,42,'Shark','puzzle_solve',100,self.now-86400)
        community=__import__('community_progress')
        challenge={'id':'event:animal_day:2026-10-01','event':'animal_day','title':'Animal Day',
                   'start':datetime(2026,10,1,tzinfo=HOLIDAY_ZONE).timestamp(),
                   'end':datetime(2026,10,8,tzinfo=HOLIDAY_ZONE).timestamp(),
                   'goals':{'puzzle_solve':5000},'progress':{'puzzle_solve':10},'users':{'42':{'puzzle_solve':10}}}
        ledger._push_files({activity.FILE:json.dumps(act),community.FILE:json.dumps({'weeks':{},'challenges':{challenge['id']:challenge}})},'Seed records')
        ledger._fetch_retry()
        async def run():
            channel=SimpleNamespace(guild=SimpleNamespace(me=SimpleNamespace(id=7)),send=AsyncMock(return_value=SimpleNamespace(id=88)))
            async def history(**kwargs):
                for _ in []:yield None
            channel.history=history
            await reports.tick(channel,self.now)
            count=channel.send.await_count
            await reports.tick(channel,self.now)
            self.assertEqual(channel.send.await_count,count)
            keys=publications.read()['items']
            for prefix in ['wrapped:2026','newspaper:','monthly:','event-recap:','milestone:puzzle_solve:100']:
                self.assertTrue(any(key.startswith(prefix) for key in keys),prefix)
            self.assertIn('personal-wrapped:2026:42',keys)
            wrapped=keys['wrapped:2026'];self.assertEqual(wrapped['status'],'sent')
            self.assertEqual(wrapped['payload']['summary']['totals']['puzzle_solve'],100)
        asyncio.run(run())

    def test_wrap_not_published_before_december5_and_catches_up_next_year(self):
        self.now=datetime(2026,12,4,23,59,tzinfo=HOLIDAY_ZONE).timestamp()
        act={'tracking_since':datetime(2026,10,1,tzinfo=HOLIDAY_ZONE).timestamp(),'days':{},'totals':{},'records':{}}
        activity.add(act,42,'Shark','puzzle_solve',12,self.now)
        ledger._push_files({activity.FILE:json.dumps(act)},'Seed annual activity');ledger._fetch_retry()
        async def run():
            channel=SimpleNamespace(guild=SimpleNamespace(me=SimpleNamespace(id=7)),send=AsyncMock(return_value=SimpleNamespace(id=88)))
            await reports.tick(channel,self.now)
            self.assertNotIn('wrapped:2026',publications.read()['items'])
            self.now=datetime(2027,1,2,tzinfo=HOLIDAY_ZONE).timestamp()
            await reports.tick(channel,self.now)
            self.assertEqual(publications.read()['items']['wrapped:2026']['status'],'sent')
        asyncio.run(run())

    def test_usage_uncertain_ack_uses_same_batch_receipt_on_retry(self):
        feature_usage._pending.clear();feature_usage._sketches.clear();feature_usage._batch=None
        feature_usage.note('page:shop',42,self.now)
        original=feature_usage.run
        calls=[]
        def lost(tx,build):
            calls.append(tx);result=original(tx,build)
            if len(calls)==1:raise RuntimeError('Acknowledgement lost')
            return result
        with patch.object(feature_usage,'run',lost):
            with self.assertRaises(RuntimeError):feature_usage.flush()
            feature_usage.flush()
        self.assertEqual(calls[0],calls[1]);self.assertEqual(self.origin(feature_usage.FILE)['all']['page:shop']['count'],1)



class SchedulingAndUI(unittest.TestCase):
    def test_legacy_chess_rematch_preserves_original_clock(self):
        self.assertEqual(bot.chess_rematch_settings({'clock':{'label':'5+10','increment':10}}),(300,10))
        self.assertEqual(bot.chess_rematch_settings({'clock':{'daily':True,'increment':0}}),(86400,0))
        self.assertEqual(bot.chess_rematch_settings({'base_seconds':180,'increment':2}),(180,2))

    def test_month_year_and_december5_cutoffs_are_amsterdam(self):
        start,end=reports.bounds(2026,12)
        self.assertEqual(datetime.fromtimestamp(end,HOLIDAY_ZONE).isoformat(),'2027-01-01T00:00:00+01:00')
        _,cutoff=reports.wrapped_bounds(2026)
        self.assertEqual(datetime.fromtimestamp(cutoff,HOLIDAY_ZONE).isoformat(),'2026-12-05T00:00:00+01:00')
        self.assertEqual(reports.bounds(2026,3)[1]-reports.bounds(2026,3)[0],31*86400-3600)

    def test_health_is_explainable_and_escrow_is_not_inflation(self):
        report={'supply':1000,'top':[],'activity':{'opening_supply':1000,'closing_supply':1500,'minted':0,'burned':0,'active':['1','2','3'],'trades':1}}
        self.assertEqual(economy_health.assess(report)['label'],'Healthy')
        report['activity']['minted']=160
        self.assertEqual(economy_health.assess(report)['label'],'High Inflation')
        self.assertEqual(economy_health.assess({'historical':True})['label'],'Not enough tracked data')

    def test_wrapped_omits_december5_activity_and_unknown_features(self):
        start,end=reports.wrapped_bounds(2026)
        data={'tracking_since':start,'days':{},'totals':{},'records':{}}
        activity.add(data,42,'Shark','puzzle_solve',12,end-86400)
        activity.add(data,42,'Shark','puzzle_solve',999,end)
        with patch.object(activity,'read',return_value=data),patch.object(economy,'_load',return_value=None):
            personal=reports.wrapped_payload(2026,42)
        self.assertEqual(personal['summary']['totals']['puzzle_solve'],12)
        self.assertNotIn('999',str(personal['embed']))
        self.assertNotIn('Survival runs',str(personal['embed']))

    def test_notable_records_are_real_not_random_games(self):
        data={'tracking_since':0,'days':{},'totals':{},'records':{}}
        activity.add(data,42,'Shark','minigame_completed',1,1700000000,{'kind':'math','score':10})
        activity.add(data,99,'Other','minigame_completed',1,1700000001,{'kind':'math','score':5})
        self.assertEqual(sum(n['type']=='record' for n in next(iter(data['days'].values()))['notable']),1)
        self.assertEqual(data['records']['math']['score'],10)

    def test_event_recap_calendar_includes_events_without_challenge_activity(self):
        start=datetime(2026,10,1,tzinfo=HOLIDAY_ZONE).timestamp()
        end=datetime(2026,11,2,tzinfo=HOLIDAY_ZONE).timestamp()
        events=reports.finished_event_definitions(start,end)
        self.assertEqual({e['event'] for e in events.values()},{'animal_day','halloween'})

    def test_milestones_use_tiers_and_recorded_counts(self):
        candidates=reports.milestone_candidates({'totals':{'puzzle_solve':10001}})
        self.assertIn(('puzzle_solve',10000,'puzzles solved'),candidates)
        self.assertNotIn(('puzzle_solve',50000,'puzzles solved'),candidates)

    def test_main_menu_has_economy_and_admin_access_is_checked(self):
        async def run():
            view=bot.MainMenuView();labels=[getattr(c,'label','') for c in view.children]
            self.assertEqual(labels.count('Economy'),1)
            self.assertLessEqual(len(view.children),25)
            ctx=interaction();await ui.send_usage(ctx)
            ctx.response.send_message.assert_awaited_once()
            self.assertTrue(ctx.response.send_message.call_args.kwargs['ephemeral'])
        asyncio.run(run())

    def test_live_cache_key_follows_verified_state(self):
        with patch.object(live,'live',side_effect=[False,True,False]),patch.object(showcase_cards,'_render_profile_card',return_value=b'image') as renderer:
            for _ in range(3):showcase_cards.render_profile_card('classic','Classic','','','','','',(),lambda *a:'')
            self.assertEqual([call.args[-1] for call in renderer.call_args_list],[False,True,False])
        svg='<svg width="1200" height="820"></svg>'
        self.assertIn('LIVE',live.overlay(svg,enabled=True));self.assertEqual(live.overlay(svg,enabled=False),svg)

    def test_racer_rematch_uses_clicked_finished_card_and_rejects_duplicate(self):
        async def run():
            ctx=interaction();ctx.message=SimpleNamespace(id=11);ctx.channel_id=22
            old={'id':'old','message_id':'11','status':'finished','rematch_requested':True}
            latest={'id':'latest','message_id':'12','status':'finished'}
            with patch.object(bot,'_puzzle_racer_state',return_value={'games':{'old':old,'latest':latest}}),patch.object(bot,'_puzzle_racer_players',return_value=[('42','Shark'),('99','Other')]):
                self.assertIs(bot._rematch_racer_game(ctx),old)
                await bot.create_puzzle_racer_rematch(ctx)
            ctx.response.defer.assert_awaited_once_with(ephemeral=True)
            self.assertIn('already',ctx.followup.send.call_args.args[0])
        asyncio.run(run())

    def test_hll_approximate_uniques_and_no_per_user_log(self):
        registers=[0]*256
        for uid in range(10000):feature_usage.sketch_add(registers,uid)
        self.assertLess(abs(feature_usage.unique(registers)-10000),2000)
        self.assertEqual(len(registers),256)

    def test_unknown_discord_delivery_reconciles_and_does_not_resend(self):
        async def run():
            key='wrapped:2026';marker='SharkBot Publication '+__import__('hashlib').sha256(key.encode()).hexdigest()[:16]
            embed=discord.Embed(title='Wrapped');embed.set_footer(text=marker)
            message=SimpleNamespace(id=88,author=SimpleNamespace(id=7),embeds=[embed])
            async def history(**kwargs):yield message
            channel=SimpleNamespace(guild=SimpleNamespace(me=SimpleNamespace(id=7)),history=history,send=AsyncMock())
            row={'key':key,'eligible':True,'status':'sending','prepared_at':1700000000,'payload':{'embed':embed.to_dict()}}
            with patch.object(publications,'prepare',return_value=row),patch.object(publications,'read',return_value={'items':{key:row}}),patch.object(publications,'update',return_value=row) as update:
                await publications.publish(channel,key,lambda:row['payload'])
            channel.send.assert_not_awaited();update.assert_called_once_with(key,'sent',message_id=88)
        asyncio.run(run())
