"""Existing trade ledger integration with real disposable Git snapshots."""
import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import bot
import guess_chatter
import pet_trading
import pet_ui
import pets
import shared_leaderboard as ledger
import test_pets as fixture
from test_profile_previews import interaction


class PetTradeTransactions(unittest.TestCase):
    git = fixture.PetTransactions.git
    origin = fixture.PetTransactions.origin

    def setUp(self):
        fixture.PetTransactions.setUp(self)
        self.data = {str(uid): {'pets': [self.pet(uid,i) for i in range(count)], 'active': f'{uid}0',
                               'accessories': ['crown']} for uid,count in ((42,2),(99,1),(77,0))}
        wallet,_=ledger._origin_state()
        wallet.update({'99':{'name':'Buyer','coins':100,'badges':['🎁']},'77':{'name':'Other','coins':100}})
        self.seed(wallet)

    def tearDown(self):
        fixture.PetTransactions.tearDown(self)

    def pet(self,uid,index):
        return {'id':f'{uid}{index}','species':'Dog','rarity':'common','name':f'Dog{index}',
                'xp':70,'born_at':self.now-2*pets.DAY,'fed_at':self.now-pets.DAY,
                'happy_at':self.now,'happiness':85,'accessory':'crown','feed_day':pets.day_key(self.now)}

    def seed(self,wallet=None):
        import json
        files={pets.FILE:json.dumps(self.data)}
        if wallet is not None:
            files[ledger.LEGACY_FILE]=ledger._snapshot_json(wallet)
        self.assertTrue(ledger._push_files(files,'Seed trade test'))
        self.assertTrue(ledger._fetch_retry())
        ledger._CACHE_SNAPSHOT=None

    def asset(self,uid=42,index=0):
        return {'type':'pet','pet_id':f'{uid}{index}'}

    def purchase(self,tx='buy',buyer=99):
        return ledger.accept_open_trade(42,'Shark',buyer,'Buyer',self.asset(),{'type':'coins','amount':10},tx,'listing')

    def direct(self,offer=None,request=None):
        offer=offer or self.asset()
        request=request or {'type':'coins','amount':10}
        ledger.propose_trade(42,'Shark',99,'Buyer',offer,request,'proposal')
        return ledger.accept_trade(99,'Buyer','accepted','proposal')

    def test_direct_purchase_moves_exact_pet_and_payment_in_one_commit(self):
        original=copy.deepcopy(self.data['42']['pets'][0])
        before=int(self.git('rev-list','--count','origin/main').stdout)
        self.direct()
        # Proposal and settlement are separate existing ledger operations.
        self.assertEqual(int(self.git('rev-list','--count','origin/main').stdout)-before,2)
        data=self.origin(pets.FILE);wallet=self.origin(ledger.LEGACY_FILE)
        received=next(p for p in data['99']['pets'] if p['id']=='420')
        original.pop('accessory')
        self.assertEqual({k:v for k,v in received.items() if k!='owner_history'},original)
        self.assertEqual([r['owner_id'] for r in received['owner_history']],['42','99'])
        self.assertEqual(data['42']['active'],'421')
        self.assertEqual(data['99']['active'],'990')
        self.assertEqual((wallet['42']['coins'],wallet['99']['coins']),(110,90))
        self.assertEqual(data['42']['accessories'],['crown'])
        replay=ledger.accept_trade(99,'Buyer','accepted','proposal')
        self.assertEqual(replay['offer']['pet_id'],self.asset()['pet_id'])
        self.assertIn('Dog',replay['offer']['label'])
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],90)

    def test_pet_for_badge_and_reverse_coin_for_pet(self):
        self.direct(request={'type':'badge','badge':'🎁'})
        self.assertIn('🎁',self.origin(ledger.LEGACY_FILE)['42']['badges'])
        self.assertNotIn('🎁',self.origin(ledger.LEGACY_FILE)['99']['badges'])
        # Buy a different owned pet back via a normal direct offer.
        ledger.propose_trade(42,'Shark',99,'Buyer',{'type':'coins','amount':10},self.asset(99),'reverse')
        ledger.accept_trade(99,'Buyer','reverse-accept','reverse')
        self.assertTrue(any(p['id']=='990' for p in self.origin(pets.FILE)['42']['pets']))

    def test_full_collections_can_swap_without_exceeding_limit(self):
        for uid in (42,99):
            self.data[str(uid)]['pets']=[self.pet(uid,i) for i in range(10)]
        self.seed()
        self.direct(request=self.asset(99))
        data=self.origin(pets.FILE)
        self.assertEqual((len(data['42']['pets']),len(data['99']['pets'])),(10,10))
        self.assertIn('990',{p['id'] for p in data['42']['pets']})
        self.assertIn('420',{p['id'] for p in data['99']['pets']})

    def test_acceptance_rechecks_full_capacity_after_proposal_without_spending(self):
        ledger.propose_trade(42,'Shark',99,'Buyer',self.asset(),{'type':'coins','amount':10},'proposal')
        self.data['99']['pets']=[self.pet(99,i) for i in range(10)]
        self.seed()
        before=self.origin(ledger.LEGACY_FILE)
        with self.assertRaisesRegex(ValueError,'10 living pets'):
            ledger.accept_trade(99,'Buyer','full','proposal')
        self.assertEqual(self.origin(ledger.LEGACY_FILE),before)
        self.assertIsNone(ledger._origin_event('full'))
        self.assertTrue(any(p['id']=='420' for p in self.origin(pets.FILE)['42']['pets']))

    def test_open_purchase_at_limit_rejected_and_memorial_does_not_take_slot(self):
        self.data['99']['pets']=[self.pet(99,i) for i in range(10)]
        self.seed()
        with self.assertRaisesRegex(ValueError,'10 living pets'):
            self.purchase()
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],100)
        self.data['99']['pets'][-1]['died_at']=self.now-1
        self.seed()
        self.purchase()
        self.assertEqual(sum(not p.get('died_at') for p in self.origin(pets.FILE)['99']['pets']),10)
        self.assertEqual(len(self.origin(pets.FILE)['99']['pets']),11)

    def test_reverse_open_purchase_checks_sellers_receiving_capacity(self):
        self.data['42']['pets']=[self.pet(42,i) for i in range(10)]
        self.seed()
        with self.assertRaisesRegex(ValueError,'10 living pets'):
            ledger.accept_open_trade(42,'Shark',99,'Buyer',{'type':'coins','amount':10},self.asset(99),'reverse-full')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['42']['coins'],100)
        self.assertEqual(self.origin(pets.FILE),self.data)

    def test_pet_that_dies_after_proposal_cannot_be_accepted(self):
        ledger.propose_trade(42,'Shark',99,'Buyer',self.asset(),{'type':'coins','amount':10},'proposal')
        self.now+=7*pets.DAY
        with self.assertRaises(ValueError):
            ledger.accept_trade(99,'Buyer','late','proposal')
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],100)
        self.assertIsNone(ledger._origin_event('late'))

    def test_dead_or_expedition_pet_is_not_transferable(self):
        for case in ('dead','starved','expedition'):
            with self.subTest(case=case):
                self.data['42']['pets'][0]=self.pet(42,0)
                self.data['42'].pop('expedition',None)
                if case=='dead':self.data['42']['pets'][0]['died_at']=self.now-1
                if case=='starved':self.data['42']['pets'][0]['fed_at']=self.now-7*pets.DAY
                if case=='expedition':self.data['42']['expedition']={'status':'running','pet_id':'420','end':self.now-1}
                self.seed()
                with self.assertRaises(ValueError):self.purchase('invalid-'+case)
                self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],100)
                self.assertIsNone(ledger._origin_event('invalid-'+case))

    def test_uncertain_push_acknowledgement_and_open_restart_replay_do_not_duplicate(self):
        original=ledger._push_files
        def uncertain(files,message):
            self.assertTrue(original(files,message));return False
        with patch.object(ledger,'_push_files',uncertain):
            first=self.purchase()
        second=self.purchase(buyer=77)
        self.assertEqual(first,second)
        data=self.origin(pets.FILE)
        self.assertEqual(sum(p['id']=='420' for owner in data.values() for p in owner['pets']),1)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],90)
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['77']['coins'],100)

    def test_conflict_rebuild_rechecks_capacity_and_preserves_concurrent_wallet(self):
        self.data['99']['pets']=[self.pet(99,i) for i in range(9)]
        self.seed()
        original=ledger._push_files;calls=0
        def conflict(files,message):
            nonlocal calls
            calls+=1
            if calls==1:
                self.data['99']['pets'].append(self.pet(99,9))
                wallet,_=ledger._origin_state();wallet['99']['coins']+=7
                import json
                self.assertTrue(original({pets.FILE:json.dumps(self.data),ledger.LEGACY_FILE:ledger._snapshot_json(wallet)},'Concurrent pet purchase'))
                return False
            return original(files,message)
        with patch.object(ledger,'_push_files',conflict):
            with self.assertRaisesRegex(ValueError,'10 living pets'):self.purchase()
        self.assertEqual(self.origin(ledger.LEGACY_FILE)['99']['coins'],107)
        self.assertIsNone(ledger._origin_event('buy'))

    def test_simultaneous_buyers_cannot_receive_the_same_pet_twice(self):
        def buy(uid):
            try:return self.purchase('buyer-'+str(uid),uid)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(buy,(99,77)))
        self.assertEqual(sum(r is not None for r in results),1)
        data=self.origin(pets.FILE)
        self.assertEqual(sum(p['id']=='420' for owner in data.values() for p in owner['pets']),1)
        self.assertEqual(sum(self.origin(ledger.LEGACY_FILE)[str(uid)]['coins'] for uid in (99,77)),190)


class PetTradeUI(unittest.IsolatedAsyncioTestCase):
    def pet(self,uid='abc123',xp=20):
        import time
        return {'id':uid,'species':'Dog','rarity':'legendary','name':'Buddy','xp':xp,
                'born_at':time.time(),'fed_at':time.time(),'happy_at':time.time(),'happiness':80}

    async def test_both_workers_resolve_trade_pets_for_the_correct_owner(self):
        owner={'pets':[self.pet()],'active':'abc123'}
        for parser in (bot._shop_trade_asset_from_text,guess_chatter._guess_shop_trade_asset_from_text):
            with patch.object(pets,'get_owner',return_value=copy.deepcopy(owner)) as read:
                result=await parser('pet Buddy',[],99)
                read.assert_called_once_with(99)
                self.assertEqual(result['pet_id'],'abc123')
                self.assertIn('Dog',ledger.format_trade_asset(result))
            self.assertEqual(await parser('10',[],99),{'type':'coins','amount':10})
            self.assertEqual((await parser('pet:abc123',None))['pet_id'],'abc123')

    async def test_ambiguous_names_and_hidden_egg_identity(self):
        owner={'pets':[self.pet(),self.pet('def456')],'active':'abc123'}
        with patch.object(pets,'get_owner',return_value=owner):
            with self.assertRaisesRegex(ValueError,'More than one'):
                pet_trading.asset_from_text('pet Dog',42)
            self.assertEqual(pet_trading.asset_from_text('pet:abc123',42)['pet_id'],'abc123')
        egg=self.pet(xp=0);owner={'pets':[egg],'active':egg['id']}
        with patch.object(pets,'get_owner',return_value=owner):
            asset=pet_trading.asset_from_text('pet:abc123',42)
            rendered=ledger.format_trade_asset(asset)
            self.assertNotIn('Dog',rendered);self.assertNotIn('Legendary',rendered)
            with self.assertRaises(ValueError):pet_trading.asset_from_text('pet Dog',42)
        fields=pet_ui.profile_embed(owner).to_dict()['fields']
        self.assertTrue(any('pet:abc123' in f['value'] for f in fields))

    async def test_direct_trade_modal_resolves_both_players_and_preserves_existing_flow(self):
        for worker,create in ((bot,bot.create_direct_shop_trade),(guess_chatter,guess_chatter.create_guess_direct_trade)):
            ctx=interaction();ctx.id=123;ctx.channel=SimpleNamespace(send=AsyncMock())
            owners={42:{'pets':[self.pet('abc123')],'active':'abc123'},99:{'pets':[self.pet('def456')],'active':'def456'}}
            with patch.object(worker,'get_cosmetic_profile',return_value={'badges':[]}),patch.object(pets,'get_owner',side_effect=lambda uid: copy.deepcopy(owners[int(uid)])),patch.object(worker,'shared_propose_trade',return_value={'trade_id':'offer'}) as propose:
                ctx.followup.send=AsyncMock()
                await create(ctx,99,'Target','pet:abc123','pet:def456')
                args=propose.call_args.args
                self.assertEqual(args[4]['pet_id'],'abc123')
                self.assertEqual(args[5]['pet_id'],'def456')
                self.assertIn('pet:abc123',ctx.channel.send.call_args.args[0])

    async def test_public_offer_duplicates_are_rejected_in_both_workers(self):
        owner={'pets':[self.pet()],'active':'abc123'}
        profile={'user_id':'42','coins':100,'badges':[]}
        existing=[{'seller_id':'42','offer':{'type':'pet','pet_id':'abc123'}}]
        for worker,create,active in ((bot,bot.create_open_shop_trade,'_active_open_shop_trades'),(guess_chatter,guess_chatter.create_guess_open_trade,'_active_guess_open_trades')):
            ctx=interaction()
            with patch.object(worker,'get_cosmetic_profile',return_value=profile),patch.object(pets,'get_owner',return_value=copy.deepcopy(owner)),patch.object(worker,active,return_value=existing):
                with self.assertRaisesRegex(ValueError,'already listed'):
                    await create(ctx,'pet:abc123','10')
