"""Economy market invariants exercised against disposable Git remotes."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import economy_analytics as economy
import economy_reports
import feature_ui
import market_ui
import market_transactions as transactions
import pet_history
import pet_market as market
import pets
import pet_trading
import pet_ui
import shared_leaderboard as ledger
import test_pets as fixture
from holiday_events import HOLIDAY_ZONE,easter_sunday
from test_profile_previews import interaction


class MarketTransactions(unittest.TestCase):
    git=fixture.PetTransactions.git
    origin=fixture.PetTransactions.origin

    def setUp(self):
        fixture.PetTransactions.setUp(self)
        self.data={'42':{'pets':[self.pet('a')],'active':'a','accessories':['crown']},
                   '99':{'pets':[],'active':None}}
        wallet,_=ledger._origin_state();wallet['99']={'name':'Buyer','coins':200};wallet['77']={'name':'Other','coins':200}
        self.seed(wallet)
        market.bootstrap()

    def tearDown(self):fixture.PetTransactions.tearDown(self)

    def pet(self,pid,xp=70):
        return {'id':pid,'name':'Buddy','species':'Dog','rarity':'common','xp':xp,'born_at':self.now-2*pets.DAY,
                'fed_at':self.now-pets.DAY,'happy_at':self.now-pets.DAY,'happiness':80,'accessory':'crown'}

    def seed(self,wallet=None):
        files={pets.FILE:json.dumps(self.data)}
        if wallet:files[ledger.LEGACY_FILE]=ledger._snapshot_json(wallet)
        self.assertTrue(ledger._push_files(files,'Seed'));self.assertTrue(ledger._fetch_retry());ledger._CACHE_SNAPSHOT=None

    def wanted(self,tx='wanted'):
        return market.create_wanted(99,'Buyer','Dog',75,tx)

    def test_surrender_adoption_burns_exactly_25_and_preserves_pet_progression(self):
        old=copy.deepcopy(self.data['42']['pets'][0]);hunger=pets.hunger(old,self.now)
        market.surrender(42,'Shark','a','surrender')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],85)
        self.assertIsNone(self.origin(pets.FILE)['42']['active'])
        self.now+=100*pets.DAY
        with self.assertRaisesRegex(ValueError,'own current'):market.adopt(42,'Shark','a','self-adopt')
        market.adopt(99,'Buyer','a','adopt')
        owner=pets.get_owner(99);pet=pets.current_pet(owner)
        self.assertEqual(pet['xp'],old['xp']);self.assertEqual(pet['born_at'],old['born_at'])
        self.assertEqual(pets.hunger(pet,self.now),hunger);self.assertFalse(pet.get('died_at'))
        self.assertNotIn('accessory',pet);self.assertEqual(self.origin(pets.FILE)['42']['accessories'],['crown'])
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],190)
        self.assertEqual([r['action'] for r in pet['owner_history']],['Tracking started','Shelter','Adopted'])
        self.assertEqual(market.adopt(99,'Buyer','a','adopt')['pet_id'],'a')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],190)
        self.assertFalse(economy._load()['sales'])

    def test_shelter_rejects_full_collection_dead_and_expedition_without_payment(self):
        market.surrender(42,'Shark','a','surrender')
        self.data=pets._read_origin();self.data['99']['pets']=[self.pet('b'+str(i)) for i in range(10)]
        self.seed()
        with self.assertRaisesRegex(ValueError,'10 living'):market.adopt(99,'Buyer','a','full')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],200)
        self.assertIn('a',market.snapshot()['shelter'])
        self.data['42']['pets']=[self.pet('c')];self.data['42']['pets'][0]['died_at']=self.now
        self.seed()
        with self.assertRaises(ValueError):market.surrender(42,'Shark','c','dead')
        self.data['42']['pets'][0].pop('died_at');self.data['42']['expedition']={'pet_id':'c','status':'running'}
        self.seed()
        with self.assertRaisesRegex(ValueError,'expedition'):market.surrender(42,'Shark','c','expedition')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],85)

    def test_shelter_concurrent_adopters_and_lost_ack_do_not_duplicate(self):
        original=ledger._push_files
        def uncertain(files,message):original(files,message);raise OSError('Lost ack')
        with patch.object(ledger,'_push_files',uncertain):market.surrender(42,'Shark','a','surrender')
        def adopt(uid):
            try:return market.adopt(uid,'Buyer','a','adopt-'+str(uid))
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(adopt,(99,77)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(sum(p['id']=='a' for o in pets._read_origin().values() for p in o['pets']),1)
        self.assertEqual(sum(self.origin(ledger.LEGACY_FILE)[str(u)]['coins'] for u in (99,77)),390)

    def test_wanted_lost_ack_keeps_one_market_sale_and_receipt(self):
        listing=self.wanted();original=ledger._push_files
        def lost(files,message):original(files,message);raise OSError('Lost acknowledgement')
        with patch.object(ledger,'_push_files',lost):first=market.fulfil(42,'Shark',listing['id'],'a')
        self.assertEqual(market.fulfil(42,'Shark',listing['id'],'a'),first)
        self.assertEqual(len(economy._load()['sales']),1)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],125)

    def test_adoption_conflict_rebuild_rechecks_full_capacity(self):
        market.surrender(42,'Shark','a','surrender');original=ledger._push_files;calls=0
        def conflict(files,message):
            nonlocal calls
            calls+=1
            if calls==1:
                data=pets._read_origin();data['99']['pets']=[self.pet('b'+str(i)) for i in range(10)]
                original({pets.FILE:json.dumps(data)},'Concurrent pet purchase')
                return False
            return original(files,message)
        with patch.object(ledger,'_push_files',conflict):
            with self.assertRaisesRegex(ValueError,'10 living'):market.adopt(99,'Buyer','a','conflict-adopt')
        self.assertIn('a',market.snapshot()['shelter'])
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],200)
        self.assertIsNone(ledger._origin_file(transactions.event_path('conflict-adopt')))

    def test_weekly_report_freezes_once_and_outbox_claim_has_one_winner(self):
        self.now+=7*pets.DAY
        first=economy_reports.prepare(self.now);second=economy_reports.prepare(self.now)
        self.assertTrue(first['eligible']);self.assertEqual(first,second)
        winner=economy_reports.update(first['week'],'sending',claim='one')
        loser=economy_reports.update(first['week'],'sending',claim='two')
        self.assertEqual(winner['claim'],'one');self.assertEqual(loser['claim'],'one')
        economy_reports.update(first['week'],'sent',123)
        result=economy_reports.update(first['week'],'sending',claim='three')
        self.assertEqual(result['status'],'sent');self.assertEqual(result['message_id'],'123')

    def test_wanted_settles_once_and_owner_history_travels(self):
        wanted=self.wanted();first=market.fulfil(42,'Shark',wanted['id'],'a')
        replay=market.fulfil(77,'Other',wanted['id'],'other-pet')
        self.assertEqual(first,replay)
        self.assertEqual((self.origin(ledger.LEGACY_FILE)['42']['coins'],self.origin(ledger.LEGACY_FILE)['99']['coins']),(175,125))
        self.assertEqual(pets.get_owner(99)['pets'][0]['owner_history'][-1]['owner_name'],'Buyer')
        data=economy._load();self.assertEqual(len(data['receipts']),1);self.assertEqual(len(data['sales']),1)
        self.assertEqual(next(iter(data['sales'].values()))['coins'],75)
        self.assertEqual(market.snapshot()['wanted'][wanted['id']]['status'],'filled')

    def test_wanted_invalid_payment_closes_listing_without_spending(self):
        wanted=self.wanted();wallet,_=ledger._origin_state();wallet['99']['coins']=3
        self.data=pets._read_origin();self.seed(wallet)
        result=market.fulfil(42,'Shark',wanted['id'],'a')
        self.assertFalse(result['fulfilled']);self.assertEqual(market.snapshot()['wanted'][wanted['id']]['status'],'invalid')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],100)
        self.assertEqual(pets.get_owner(42)['pets'][0]['id'],'a')

    def test_wanted_matches_species_rarity_evolution_and_rejects_eggs(self):
        listing=self.wanted();pet=self.pet('b')
        self.assertTrue(market.matches(pet,listing))
        for change in ({'species':'Cat'},{'xp':0},{'died_at':self.now}):
            self.assertFalse(market.matches({**pet,**change},listing))
        self.assertFalse(market.matches(pet,{**listing,'rarity':'rare'}))
        self.assertFalse(market.matches(pet,{**listing,'evolution':'Adult'}))
        with self.assertRaises(ValueError):market.create_wanted(99,'Buyer','Dog',10,'wrong','rare')
        with self.assertRaises(ValueError):market.create_wanted(99,'Buyer','Dog',float('nan'),'nan')

    def test_wanted_cancel_expiry_and_full_buyer_rechecked(self):
        listing=self.wanted();market.cancel_wanted(99,listing['id'],'cancel')
        with self.assertRaises(ValueError):market.fulfil(42,'Shark',listing['id'],'a')
        expired=self.wanted('expires');self.now+=15*pets.DAY
        with self.assertRaisesRegex(ValueError,'expired'):market.fulfil(42,'Shark',expired['id'],'a')
        self.now-=15*pets.DAY
        full=self.wanted('full');self.data=pets._read_origin();self.data['99']['pets']=[self.pet('b'+str(i)) for i in range(10)]
        self.seed()
        with self.assertRaisesRegex(ValueError,'10 living'):market.fulfil(42,'Shark',full['id'],'a')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],200)

    def test_wanted_concurrent_sellers_only_one_payment_and_receipt(self):
        self.data=pets._read_origin();self.data['77']={'pets':[self.pet('b')],'active':'b'};self.seed()
        listing=self.wanted()
        def sell(args):return market.fulfil(args[0],'Seller',listing['id'],args[1])
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(sell,((42,'a'),(77,'b'))))
        self.assertEqual(results[0],results[1]);self.assertEqual(len(economy._load()['sales']),1)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],125)
        self.assertEqual(len(pets.get_owner(99)['pets']),1)

    def test_conflict_rebuild_keeps_concurrent_wallet_and_shelter_fee_once(self):
        original=ledger._push_files;count=0
        def conflict(files,message):
            nonlocal count
            count+=1
            if count==1:
                wallet,_=ledger._origin_state();wallet['42']['coins']+=7
                original({ledger.LEGACY_FILE:ledger._snapshot_json(wallet)},'Concurrent reward')
                return False
            return original(files,message)
        with patch.object(ledger,'_push_files',conflict):market.surrender(42,'Shark','a','conflict')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],92)
        self.assertEqual(len(market.snapshot()['shelter']),1)
        week=economy._load()['weeks'][economy.week_key(self.now)]
        self.assertEqual(week['burned'],15);self.assertEqual(week['minted'],7)

    def test_normal_trade_records_history_sale_and_no_transfer_minting(self):
        ledger.accept_open_trade(42,'Shark',99,'Buyer',{'type':'pet','pet_id':'a'},{'type':'coins','amount':30},'sale')
        data=economy._load();receipt=data['receipts']['sale'];sale=data['sales']['sale']
        self.assertEqual(sale['species'],'Dog');self.assertEqual(receipt['pet_sale']['coins'],30)
        self.assertEqual(pets.get_owner(99)['pets'][0]['owner_history'][-1]['action'],'Traded')
        ledger.accept_open_trade(42,'Shark',77,'Other',{'type':'pet','pet_id':'a'},{'type':'coins','amount':30},'sale')
        self.assertEqual(len(economy._load()['receipts']),1)
        week=data['weeks'][economy.week_key(self.now)]
        self.assertEqual((week['minted'],week['burned'],week['trades'],week['traded_coins']),(0,0,1,30))

    def test_receipt_captures_current_pet_metadata_after_proposal(self):
        ledger.propose_trade(42,'Shark',99,'Buyer',{'type':'pet','pet_id':'a','label':'Old label'},{'type':'coins','amount':30},'proposal')
        self.data=pets._read_origin();self.data['42']['pets'][0]['name']='Renamed';self.data['42']['pets'][0]['xp']=500
        self.seed()
        result=ledger.accept_trade(99,'Buyer','accept','proposal')
        self.assertIn('Renamed',result['offer']['label'])
        self.assertIn('Level '+str(pets.level(self.data['42']['pets'][0])),result['offer']['label'])
        self.assertEqual(economy._load()['receipts']['accept']['offer'],result['offer'])

    def test_eggs_keep_hidden_species_in_receipts_shelter_and_market_data(self):
        self.data=pets._read_origin();self.data['42']['pets'][0]['xp']=0;self.seed()
        ledger.accept_open_trade(42,'Shark',99,'Buyer',{'type':'pet','pet_id':'a','label':'Mysterious Egg'},{'type':'coins','amount':5},'egg-sale')
        data=economy._load()
        self.assertEqual(data['sales']['egg-sale']['species'],'Mysterious Egg')
        self.assertIsNone(data['sales']['egg-sale']['rarity'])
        market.surrender(99,'Buyer','a','egg-shelter')
        row=market.snapshot()['shelter']['a']
        self.assertNotIn('Dog',pet_trading.label(row['pet']))

    def test_badge_and_pet_swaps_have_receipts_but_no_coin_market_sales(self):
        self.data=pets._read_origin();self.data['42']['pets'].append(self.pet('c'));self.data['99']['pets'].append(self.pet('b'))
        wallet,_=ledger._origin_state();wallet['99']['badges']=['🎁'];self.seed(wallet)
        ledger.accept_open_trade(42,'Shark',99,'Buyer',{'type':'pet','pet_id':'a'},{'type':'badge','badge':'🎁'},'badge-swap')
        ledger.accept_open_trade(99,'Buyer',42,'Shark',{'type':'pet','pet_id':'b'},{'type':'pet','pet_id':'c'},'pet-swap')
        data,wallet=economy.snapshot();self.assertFalse(data['sales']);self.assertEqual(len(data['receipts']),2)
        report=economy.report(data,wallet,economy.week_key(self.now))
        self.assertEqual(report['species']['Dog'],3)

    def test_new_egg_history_starts_at_creation_and_hatching_once(self):
        owner,details=pets.buy_egg(42,'Shark','new-egg')
        pet_id=details['pet_id']
        for index in range(4):pets.award_activity(42,'Shark','hatch-'+str(index))
        pet=pets.current_pet(pets.get_owner(42),pet_id)
        self.assertEqual([r['action'] for r in pet['owner_history']],['Created','Hatched'])
        pets.award_activity(42,'Shark','hatch-3')
        self.assertEqual(len(pets.current_pet(pets.get_owner(42),pet_id)['owner_history']),2)

    def test_wallet_source_sink_and_weekly_distribution_are_audited(self):
        ledger.spend_coins(42,'Shark',20,'shop','mystery-box')
        ledger.credit_coins(42,'Shark',5,'reward',source='admin-test')
        data,wallet=economy.snapshot();r=economy.report(data,wallet,economy.week_key(self.now))
        self.assertEqual(r['supply'],485)
        self.assertEqual((r['activity']['minted'],r['activity']['burned']),(5,20))
        self.assertEqual(r['median'],200)
        self.assertEqual(r['wallets'],3)

    def test_wager_escrow_is_not_counted_as_coin_destruction_or_creation(self):
        ledger.reserve_chess_wager(42,'Shark',99,'Buyer',10,'reserve')
        ledger.settle_chess_wager(42,'Shark',99,'Buyer',10,None,'settle')
        week=economy._load()['weeks'][economy.week_key(self.now)]
        self.assertEqual((week['minted'],week['burned']),(0,0))


class MarketViews(unittest.IsolatedAsyncioTestCase):
    async def test_shelter_and_wanted_pagination_permissions_and_empty_views(self):
        pet={'id':'a','xp':0,'name':'','species':'Dragon','rarity':'legendary'}
        data={'shelter':{str(i):{'pet':pet,'original_owner':'42'} for i in range(30)},'wanted':{}}
        view=market_ui.MarketBrowser(42,'shelter',data)
        picker=next(c for c in view.children if isinstance(c,discord.ui.Select))
        self.assertEqual(len(picker.options),25)
        self.assertTrue(next(c for c in view.children if getattr(c,'label','')=='Adopt · 10 coins').disabled)
        self.assertNotIn('Dragon',picker.options[0].label)
        self.assertFalse(await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=99))))
        view.stop()
        for mode in ('shelter','wanted'):
            view=market_ui.MarketBrowser(42,mode,{'shelter':{},'wanted':{}});view.stop()

    async def test_surrender_requires_confirm_and_binds_owner(self):
        view=market_ui.SurrenderConfirm(42,'a')
        self.assertFalse(await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=99))))
        ctx=interaction();ctx.message=SimpleNamespace(id=123)
        with patch.object(market,'surrender',return_value={}) as call:
            await view.confirm.callback(ctx)
            call.assert_called_once_with(42,'Player','a','shelter-surrender:123')
        view.stop()

    async def test_market_value_uses_recent_median_stage_and_minimum(self):
        pet={'species':'Dog','xp':70};now=10000000
        sales={str(i):{'species':'Dog','evolution':'Baby','coins':v,'at':now-1} for i,v in enumerate((10,20,9000))}
        data={'sales':sales}
        self.assertEqual(economy.market_value(pet,data,now),{'coins':20,'count':3})
        del sales['2'];self.assertIsNone(economy.market_value(pet,data,now))
        sales['2']={'species':'Dog','evolution':'Baby','coins':15,'at':now-31*pets.DAY}
        self.assertIsNone(economy.market_value(pet,data,now))
        self.assertIsNone(economy.market_value({'species':'Dragon','xp':0},data,now))

    async def test_next_event_uses_calendar_easter_wrap_and_amsterdam(self):
        embed=feature_ui.next_event_embed(datetime(2026,10,4,tzinfo=HOLIDAY_ZONE))
        self.assertIn('Halloween',embed.title);self.assertIn('in 11 days',embed.description)
        self.assertIn('October 31',embed.description)
        embed=feature_ui.next_event_embed(datetime(2026,12,28,tzinfo=HOLIDAY_ZONE))
        self.assertIn('New Year',embed.title);self.assertIn('January 1',embed.description)
        embed=feature_ui.next_event_embed(datetime(2026,3,15,tzinfo=HOLIDAY_ZONE))
        self.assertIn('Easter',embed.title)
        self.assertIn(str(easter_sunday(2026).day),embed.description)

    async def test_receipt_short_id_and_immutable_datetime_remain_stable(self):
        details={'receipt_id':'same','completed_at':12345,'seller_name':'A','buyer_name':'B',
                 'offer':{'type':'pet','pet_id':'a','label':'Dog · Level 3'},'request':{'type':'coins','amount':10}}
        first=market_ui.receipt_embed(details).to_dict();second=market_ui.receipt_embed(copy.deepcopy(details)).to_dict()
        self.assertEqual(first,second);self.assertIn('10 coins',str(first));self.assertIn('12345',str(first))

    async def test_first_report_publication_uses_nonce_and_durable_delivery(self):
        report={'week':'2026-W40','activity':{},'tracking_since':100,'supply':100,'wallets':1,'mean':100,'median':100,'pet_sales':0,'highest_sale':None,'median_sale':None,'species':{},'top':[]}
        row={'eligible':True,'week':'2026-W40','status':'prepared','prepared_at':100,'report':report}
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)))
        def update(key,status,message_id=None,claim=None):return {**row,'status':status,'claim':claim}
        with patch.object(economy_reports,'prepare',return_value=row),patch.object(economy,'snapshot',return_value=({'reports':{'2026-W40':row}},{})),patch.object(economy_reports,'update',side_effect=update) as save:
            await economy_reports.publish_once(channel)
            channel.send.assert_awaited_once()
            self.assertTrue(channel.send.call_args.kwargs['nonce'])
            self.assertIn('Economy Report 2026-W40',channel.send.call_args.kwargs['embed'].footer.text)
            self.assertEqual(save.call_args.args,('2026-W40','sent',123))

    async def test_week_boundary_dst_and_report_recovery_do_not_duplicate_messages(self):
        self.assertEqual(economy.previous_week(datetime(2026,10,26,0,0,tzinfo=HOLIDAY_ZONE).timestamp()),'2026-W43')
        row={'eligible':True,'week':'2026-W40','status':'sending','prepared_at':100}
        existing=SimpleNamespace(id=22,author=SimpleNamespace(id=42),embeds=[discord.Embed().set_footer(text='Economy Report 2026-W40')])
        async def history(**kwargs):yield existing
        channel=SimpleNamespace(history=history,guild=SimpleNamespace(me=SimpleNamespace(id=42)),send=AsyncMock())
        with patch.object(economy_reports,'prepare',return_value=row),patch.object(economy,'snapshot',return_value=({'reports':{'2026-W40':row}},{})),patch.object(economy_reports,'update') as update:
            await economy_reports.publish_once(channel)
            channel.send.assert_not_awaited();update.assert_called_once_with('2026-W40','sent',22)
