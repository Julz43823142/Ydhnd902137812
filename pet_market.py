"""Shelter and Wanted listings. Wallet/pet mutations share one Git transaction."""
import copy
import json
import math
import time
import uuid

import pets
import pet_trading
import pet_history
import shared_leaderboard as ledger
from market_transactions import read, run

FILE = 'pet_market.json'
SURRENDER_COST = 15
ADOPT_COST = 10


def state():
    data = read(FILE, {'shelter':{}, 'wanted':{}})
    if not isinstance(data,dict) or not isinstance(data.get('shelter'),dict) or not isinstance(data.get('wanted'),dict):
        raise RuntimeError('Invalid market snapshot; refusing to overwrite it.')
    return data


def snapshot():
    with ledger.REPOSITORY_LOCK:
        if not ledger.refresh_for_read():
            raise RuntimeError('Market cannot be refreshed.')
        return copy.deepcopy(state())


def _files(wallet, data, market):
    return {ledger.LEGACY_FILE:ledger._snapshot_json(wallet), pets.FILE:json.dumps(data,ensure_ascii=False)+'\n',
            FILE:json.dumps(market,ensure_ascii=False)+'\n'}


def _entry(wallet,uid,name):
    entry=ledger._normalize_entry(wallet.get(str(uid),{'name':name}))
    entry['name']=name;wallet[str(uid)]=entry
    return entry


def surrender(uid, name, pet_id, txid):
    def build():
        market=state();data=pets._read_origin();wallet,_=ledger._origin_state();now=time.time()
        owner=pets._owner(data,uid,now);pet=pet_trading.ensure_tradable(owner,pet_id)
        entry=_entry(wallet,uid,name)
        if entry['coins']<SURRENDER_COST:raise ValueError('Surrender costs 15 coins.')
        if pet_id in market['shelter']:raise ValueError('This pet is already in the Shelter.')
        pet_history.ensure(pet,uid,name,now=now)
        pet_history.append(pet,'Shelter',uid,name,now=now)
        pet.pop('accessory',None)
        owner['pets'].remove(pet);pets.expire(owner,now)
        market['shelter'][pet_id]={'pet':pet,'original_owner':str(uid),'sheltered_at':now}
        entry['coins']=round(entry['coins']-SURRENDER_COST,3)
        return _files(wallet,data,market),{'pet_id':pet_id,'user_id':str(uid),'spent':SURRENDER_COST},'shelter-surrender'
    return run(txid,build)


def adopt(uid,name,pet_id,txid):
    def build():
        market=state();data=pets._read_origin();wallet,_=ledger._origin_state();now=time.time()
        row=market['shelter'].get(pet_id)
        if row is None:raise ValueError('This pet has already been adopted.')
        if row['original_owner']==str(uid):raise ValueError('You cannot adopt your own current Shelter pet.')
        owner=pets._owner(data,uid,now);entry=_entry(wallet,uid,name)
        if sum(not p.get('died_at') for p in owner['pets'])>=pets.MAX_LIVING:raise ValueError('Maximum 10 living pets.')
        if any(p['id']==pet_id for p in owner['pets']):raise ValueError('Pet ID already exists in your collection.')
        if entry['coins']<ADOPT_COST:raise ValueError('Adoption costs 10 coins.')
        pet=copy.deepcopy(row['pet'])
        if pet.get('died_at'):raise ValueError('Memorial pets cannot be adopted.')
        paused=max(0,now-row['sheltered_at'])
        for key in ('fed_at','happy_at'):
            pet[key]=pet.get(key,pet['born_at'])+paused
        pet_history.append(pet,'Adopted',uid,name,now=now)
        owner['pets'].append(pet);pets.expire(owner,now)
        del market['shelter'][pet_id]
        entry['coins']=round(entry['coins']-ADOPT_COST,3)
        return _files(wallet,data,market),{'pet_id':pet_id,'user_id':str(uid),'spent':ADOPT_COST},'shelter-adopt'
    return run(txid,build)


def create_wanted(uid,name,species,coins,txid,rarity=None,evolution=None):
    species=next((s for s in sum(pets.SPECIES.values(),()) if s.casefold()==str(species).strip().casefold()),None)
    if not species:raise ValueError('Choose a valid pet species.')
    coins=round(float(coins),3)
    if not math.isfinite(coins) or coins<=0:raise ValueError('Coin offer must be positive and finite.')
    rarity=str(rarity or '').strip().casefold() or None
    evolution=str(evolution or '').strip().title() or None
    if rarity and rarity not in pets.SPECIES:raise ValueError('Invalid rarity.')
    if rarity and species not in pets.SPECIES[rarity]:raise ValueError('This species has a different rarity.')
    if evolution and evolution not in ('Baby','Young','Adult','Evolved'):raise ValueError('Invalid evolution stage.')
    listing_id=uuid.uuid4().hex
    def build():
        market=state();wallet,_=ledger._origin_state();now=time.time()
        if sum(l['buyer_id']==str(uid) and l['status']=='open' and l['expires_at']>now for l in market['wanted'].values())>=5:
            raise ValueError('Maximum five active Wanted listings.')
        entry=_entry(wallet,uid,name)
        if entry['coins']<coins:raise ValueError('You cannot currently afford this offer.')
        listing={'id':listing_id,'buyer_id':str(uid),'buyer_name':name,'species':species,'coins':coins,
                 'rarity':rarity,'evolution':evolution,'status':'open','created_at':now,'expires_at':now+7*pets.DAY}
        market['wanted'][listing_id]=listing
        return {FILE:json.dumps(market,ensure_ascii=False)},listing,'wanted-create'
    return run(txid,build)


def cancel_wanted(uid,listing_id,txid):
    def build():
        market=state();listing=market['wanted'].get(listing_id)
        if not listing or listing['buyer_id']!=str(uid):raise ValueError('Only the maker can cancel this listing.')
        if listing['status']!='open':raise ValueError('This listing has already closed.')
        listing['status']='cancelled'
        return {FILE:json.dumps(market)}, {'id':listing_id,'cancelled':True},'wanted-cancel'
    return run(txid,build)


def matches(pet,listing):
    return (not pet.get('died_at') and pets.level(pet)>0 and pet['species']==listing['species']
            and (not listing.get('rarity') or pet['rarity']==listing['rarity'])
            and (not listing.get('evolution') or pets.evolution(pet)==listing['evolution']))


def fulfil(uid,name,listing_id,pet_id):
    def build():
        market=state();listing=market['wanted'].get(listing_id);now=time.time()
        if not listing or listing['status']!='open':raise ValueError('Listing is no longer open.')
        if listing['expires_at']<=now:raise ValueError('Listing has expired.')
        buyer=listing['buyer_id']
        if buyer==str(uid):raise ValueError('You cannot fulfil your own listing.')
        wallet,_=ledger._origin_state();data=pets._read_origin()
        owner=pets._owner(data,uid,now);pet=pet_trading.ensure_tradable(owner,pet_id)
        if not matches(pet,listing):raise ValueError('This pet does not match the Wanted listing.')
        seller_entry=_entry(wallet,uid,name);buyer_name=wallet.get(buyer,{}).get('name',listing['buyer_name'])
        buyer_entry=_entry(wallet,buyer,buyer_name)
        if buyer_entry['coins']<listing['coins']:
            listing['status']='invalid'
            return {FILE:json.dumps(market)}, {'fulfilled':False,'reason':'Buyer no longer has enough coins. Listing removed.'},'wanted-invalid'
        offer={'type':'pet','pet_id':pet_id,'label':pet_trading.label(pet)}
        request={'type':'coins','amount':listing['coins']}
        sale={'pet_id':pet_id,'species':pet['species'],'rarity':pet['rarity'],'level':pets.level(pet),
              'evolution':pets.evolution(pet),'coins':listing['coins']}
        pet_trading.settle(data,uid,buyer,offer,request,names={str(uid):name,buyer:buyer_name})
        ledger._move_asset(buyer_entry,seller_entry,request)
        listing.update(status='filled',seller_id=str(uid),pet_id=pet_id)
        details={'fulfilled':True,'seller_user_id':str(uid),'seller_name':name,'buyer_user_id':buyer,
                 'buyer_name':buyer_name,'offer':offer,'request':request,'pet_sale':sale,'listing_id':listing_id}
        return _files(wallet,data,market), details,'wanted-fulfill'
    return run('wanted-fulfill:'+listing_id,build)


def bootstrap(txid='economy-market-bootstrap-v1'):
    def build():
        data=pets._read_origin();wallet,_=ledger._origin_state()
        for uid,owner in data.items():
            for pet in owner['pets']:pet_history.ensure(pet,uid,wallet.get(uid,{}).get('name',uid))
        import economy_analytics
        files={pets.FILE:json.dumps(data,ensure_ascii=False)}
        economy_analytics.initialize(files,wallet)
        return files, {'initialized':True},'market-bootstrap'
    return run(txid,build)
