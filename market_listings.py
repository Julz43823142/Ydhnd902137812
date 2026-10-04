"""Shared index of existing Open/Wanted listings; settlement stays in the ledger."""
import copy
import json
import time
import uuid

import shared_leaderboard as ledger
import pets
import pet_market
import pet_trading
from market_transactions import run

LIFETIME = 14 * pets.DAY


def expires(row):
    # Existing explicit expiry remains authoritative; new Wanted listings use 14 days.
    return float(row.get('expires_at', float(row.get('created_at', 0)) + LIFETIME))


def active(row, now=None):
    return row.get('status', 'open') == 'open' and expires(row) > (time.time() if now is None else now)


def timing(row, now=None):
    now=time.time() if now is None else now
    def duration(seconds):
        hours=max(0,int(seconds//3600));return f'{hours//24}d {hours%24}h'
    return f"🕒 Listed {duration(now-float(row.get('created_at',now)))} ago\n⏳ {'Expires in '+duration(expires(row)-now) if active(row,now) else 'Expired / closed'}"


def register(rows, worker):
    rows=copy.deepcopy(list(rows))
    if not rows:return
    # Unique batch receipt reflects values: retries/restarts preserve later authoritative status.
    import hashlib
    token=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    def build():
        data=pet_market.state();index=data.setdefault('open',{})
        for row in rows:
            key=row['trade_id']
            existing=index.get(key)
            if existing:
                if row.get('message_id'):existing.update(message_id=row['message_id'],channel_id=row['channel_id'])
                continue
            row.update(worker=worker,expires_at=expires(row))
            receipt=ledger._origin_event('open-trade-accept:'+key)
            if receipt:row['status']='completed'
            elif not active(row):row['status']='expired' if row.get('status','open')=='open' else row['status']
            index[key]=row
        return {pet_market.FILE:json.dumps(data)}, {'registered':len(rows)},'listing-register'
    return run('listing-register:'+token,build)


def close(uid, listing_id, status='cancelled'):
    def build():
        data=pet_market.state();row=data.get('open',{}).get(listing_id)
        if not row or row['seller_id']!=str(uid):raise ValueError('Only the listing owner can close it.')
        if ledger._origin_event('open-trade-accept:'+listing_id):raise ValueError('This listing was already accepted.')
        elif row.get('status','open')=='open':row['status']=status
        return {pet_market.FILE:json.dumps(data)},row,'listing-close'
    return run(f'listing-close:{listing_id}',build)


def validate(listing_id, now, fallback_expiry=None):
    data=pet_market.state();row=data.get('open',{}).get(str(listing_id))
    if row and not active(row,now):raise ValueError('This listing has expired or closed.')
    if not row and fallback_expiry is not None and fallback_expiry<=now:raise ValueError('This listing has expired.')
    return data,row


def expire():
    now=time.time()
    with ledger.REPOSITORY_LOCK:
        data=pet_market.state()
        candidates=[section+':'+key for section in ('open','wanted') for key,row in data.get(section,{}).items()
                    if row.get('status','open')=='open' and expires(row)<=now]
    if not candidates:return {'expired':0}
    def build():
        data=pet_market.state();count=0
        for section in ('open','wanted'):
            for key,row in data.get(section,{}).items():
                if row.get('status','open')=='open' and expires(row)<=now:
                    receipt=ledger._origin_event('open-trade-accept:'+key) if section=='open' else None
                    row.update(status='completed' if receipt else 'expired',closed_at=now);count+=1
        return {pet_market.FILE:json.dumps(data)}, {'expired':count},'listing-expiry'
    import hashlib
    key=hashlib.sha256('|'.join(sorted(candidates)).encode()).hexdigest()
    return run('listing-expiry:'+key,build)


def find_matches(data, wallet, pet_data, now=None, uid=None):
    now=time.time() if now is None else now;result=[]
    view=copy.deepcopy(pet_data);owners={}
    def owner_for(uid):
        key=str(uid)
        if key not in owners:owners[key]=pets._owner(view,key,now)
        return owners[key]
    for wid,wanted in data.get('wanted',{}).items():
        buyer=wanted['buyer_id']
        if (uid is not None and buyer!=str(uid)) or not active(wanted,now):continue
        owner=owner_for(buyer)
        if sum(not p.get('died_at') for p in owner['pets'])>=pets.MAX_LIVING:continue
        for oid,row in data.get('open',{}).items():
            if not active(row,now) or row['seller_id']==buyer:continue
            offer,request=row['offer'],row['request']
            if offer.get('type')!='pet' or request.get('type')!='coins':continue
            price=float(request['amount'])
            if price>wanted['coins'] or price>float(wallet.get(buyer,{}).get('coins',0)):continue
            try:
                seller=owner_for(row['seller_id'])
                pet=pet_trading.ensure_tradable(seller,offer['pet_id'])
            except ValueError:continue
            if pet_market.matches(pet,wanted):
                result.append({'id':wid+':'+oid,'wanted_id':wid,'open_id':oid,'buyer_id':buyer,
                               'species':wanted['species'],'price':price,'listing':copy.deepcopy(row)})
    return result


def buy_match(uid,name,wanted_id,open_id):
    import repository_transaction
    def build():
        data=pet_market.state();wallet,_=ledger._origin_state();pet_data=pets._read_origin()
        match=next((m for m in find_matches(data,wallet,pet_data,uid=uid)
                    if m['wanted_id']==wanted_id and m['open_id']==open_id),None)
        if not match:raise ValueError('This match is no longer available. Refresh the market.')
        row=match['listing']
        receipt=ledger.accept_open_trade(row['seller_id'],row['seller_name'],uid,name,
                    row['offer'],row['request'],'open-trade-accept:'+open_id,open_id)
        if str(receipt['buyer_user_id'])!=str(uid):raise ValueError('Another buyer already accepted this listing.')
        def finish():
            updated=pet_market.state();updated['wanted'][wanted_id].update(status='filled',open_id=open_id)
            return {pet_market.FILE:json.dumps(updated)},receipt,'match-complete'
        return run('match-complete:'+wanted_id,finish)
    receipt=repository_transaction.run('market-match:'+wanted_id,build)
    if str(receipt.get('buyer_user_id'))!=str(uid):raise ValueError('This match belongs to another buyer.')
    return receipt


def dismiss(uid,match_id):
    def build():
        data=pet_market.state();data.setdefault('dismissed',{}).setdefault(str(uid),[])
        if match_id not in data['dismissed'][str(uid)]:data['dismissed'][str(uid)].append(match_id)
        return {pet_market.FILE:json.dumps(data)}, {'dismissed':True},'match-dismiss'
    return run(f'match-dismiss:{uid}:{match_id}',build)


def relist(uid,section,key):
    new_id=uuid.uuid4().hex
    def build():
        data=pet_market.state();row=data.get(section,{}).get(key);now=time.time()
        if not row or str(row.get('seller_id',row.get('buyer_id')))!=str(uid):raise ValueError('Only the owner can relist.')
        if active(row,now) or row.get('status') not in ('expired','open'):raise ValueError('Only expired listings can be relisted.')
        wallet,_=ledger._origin_state()
        owner_id=row.get('seller_id',row.get('buyer_id'))
        if section=='open':
            if not ledger._asset_available(ledger._normalize_entry(wallet.get(str(uid),{})),row['offer'],str(uid),pets._read_origin()):
                raise ValueError('You no longer own the offered asset.')
            if sum(active(r,now) and r['seller_id']==str(uid) for r in data.get('open',{}).values())>=5:raise ValueError('Too many active listings.')
        else:
            if wallet.get(str(uid),{}).get('coins',0)<row['coins']:raise ValueError('You cannot currently afford this offer.')
            if sum(active(r,now) and r['buyer_id']==str(uid) for r in data.get('wanted',{}).values())>=5:raise ValueError('Too many active listings.')
        row['status']='expired';new=copy.deepcopy(row)
        new.update(status='open',created_at=now,expires_at=now+LIFETIME,relisted_from=key)
        if section=='open':
            new['trade_id']=new_id;new.pop('message_id',None)
        else:new['id']=new_id
        data[section][new_id]=new
        return {pet_market.FILE:json.dumps(data)},new,'listing-relist'
    row=run('listing-relist:'+key,build)
    if str(row.get('seller_id',row.get('buyer_id')))!=str(uid):raise ValueError('Only the owner can relist.')
    return row


def migrate():
    def build():
        data=pet_market.state()
        for row in data.get('wanted',{}).values():
            if row.get('status')=='open':row['expires_at']=float(row['created_at'])+LIFETIME
        return {pet_market.FILE:json.dumps(data)}, {'migrated':True},'listing-migrate'
    return run('listing-lifetime-v2-14-days',build)


async def sync_views(rows,save,refresh):
    import asyncio
    while True:
        try:
            data=await asyncio.to_thread(pet_market.snapshot)
            changed=False
            for key,row in list(rows().items()):
                canonical=data.get('open',{}).get(key)
                status=canonical.get('status') if canonical else None
                if status=='completed' and row.get('status')=='open':
                    receipt=await asyncio.to_thread(ledger.get_open_trade_acceptance,key)
                    if receipt:row.update(status='completed',receipt=receipt,buyer_name=receipt.get('buyer_name','Player'),buyer_id=receipt.get('buyer_user_id'));changed=True
                elif row.get('status','open')=='open' and (status in ('expired','cancelled','invalid') or expires(row)<=time.time()):
                    row['status']=status if status in ('expired','cancelled','invalid') else 'expired';changed=True
                else:continue
                await refresh(row)
            if changed:await save()
        except Exception:pass  # Settlement still validates fresh state if Discord/Git is unavailable.
        await asyncio.sleep(60)
