"""Observed, audited economy statistics; separate from transaction business rules."""
import copy
import hashlib
import json
import statistics
import time
from datetime import datetime, timedelta

import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE
from community_progress import week_key

FILE='economy_analytics.json'


def _load():
    raw=ledger._origin_file(FILE)
    return json.loads(raw) if raw is not None else None


def initialize(files,wallet,now=None):
    if FILE in files or _load() is not None:return
    now=time.time() if now is None else now
    files[FILE]=json.dumps({'tracking_since':now,'baseline_supply':supply(wallet),'last_wallet_supply':supply(wallet),'weeks':{},'receipts':{},'sales':{},'reports':{}},ensure_ascii=False)


def supply(wallet):
    return round(sum(float(e.get('coins',0)) for e in wallet.values()),3)


def _week(data,now,before_supply):
    return data['weeks'].setdefault(week_key(now),{'opening_supply':before_supply,'closing_supply':before_supply,
          'minted':0,'burned':0,'trades':0,'traded_coins':0,'surrenders':0,'adoptions':0,'categories':{},'active':[]})


def observe(files,message):
    """Called inside _push_files, including staged writers, before their commit.

    The staged origin read sees earlier staged wallet changes. Thus individual
    reward sources and sinks remain observable even inside one atomic group.
    Conflicts reread this snapshot, and immutable event IDs deduplicate receipts.
    """
    if FILE in files:data=json.loads(files[FILE])
    else:data=_load()
    if data is None:
        # Bootstrap creates the baseline deliberately; no speculative backfill.
        return
    before_weeks=copy.deepcopy(data["weeks"])
    now=time.time()
    before_raw=ledger._origin_file(ledger.LEGACY_FILE)
    before=json.loads(before_raw) if before_raw else {}
    after=json.loads(files[ledger.LEGACY_FILE]) if ledger.LEGACY_FILE in files else before
    events=[]
    for path,raw in list(files.items()):
        if path.startswith(('shared_score_events/','market_events/','pet_events/')) and path.endswith('.json'):
            event=json.loads(raw)
            if ledger._origin_file(path) is None:events.append((path,event))
    # Ledger event directory is configurable; include its canonical prefix.
    for path,raw in list(files.items()):
        if path.startswith(ledger.EVENT_DIR+'/') and not any(p==path for p,_ in events) and ledger._origin_file(path) is None:
            events.append((path,json.loads(raw)))
    changed={uid for uid in set(before)|set(after) if before.get(uid)!=after.get(uid)}
    if not changed and not events:return
    week=_week(data,now,supply(before))
    gap=round(supply(before)-data.get('last_wallet_supply',supply(before)),3)
    if gap:week['unattributed_wallet_change']=round(week.get('unattributed_wallet_change',0)+gap,3)
    delta=round(supply(after)-supply(before),3)
    data['last_wallet_supply']=supply(after)
    is_escrow=any(e.get('operation') in ('chess-wager-reserve','chess-wager-settle','multiplayer-wager-reserve','multiplayer-wager-settle') for _,e in events)
    escrow_change=sum(float(e.get('details',{}).get('escrow_change',0)) for _,e in events)
    economic_delta=0 if is_escrow else round(delta+escrow_change,3)
    week['minted']=round(week['minted']+max(0,economic_delta),3)
    week['burned']=round(week['burned']+max(0,-economic_delta),3)
    week['closing_supply']=supply(after)
    week['active']=sorted(set(week['active'])|changed)
    operations=sorted({str(e.get('details',{}).get('source') or e.get('source') or e.get('operation') or 'pet-care') for _,e in events})
    category=', '.join(operations) or str(message)
    if economic_delta:
        week['categories'][category]=round(week['categories'].get(category,0)+economic_delta,3)
    pets_data=json.loads(files.get('pets_state.json') or ledger._origin_file('pets_state.json') or '{}')
    for path,event in events:
        op=event.get('operation');details=event.get('details',{})
        event_week=_week(data,event.get('created_at',now),supply(before))
        if op=='shelter-surrender':event_week['surrenders']+=1
        if op=='shelter-adopt':event_week['adoptions']+=1
        if op not in ('trade-accept','open-trade-accept','wanted-fulfill'):continue
        txid=event['transaction_id']
        if txid in data['receipts']:continue
        offer,request=details.get('offer',{}),details.get('request',{})
        if not offer or not request:continue
        sale=details.get('pet_sale')
        pet_asset=offer if offer.get('type')=='pet' and request.get('type')=='coins' else request if request.get('type')=='pet' and offer.get('type')=='coins' else None
        if pet_asset and sale is None:
            import pets
            pet=next((p for owner in pets_data.values() for p in owner['pets'] if p['id']==pet_asset['pet_id']),None)
            if pet:
                coin_asset=request if pet_asset is offer else offer
                sale={'pet_id':pet['id'],'species':pet['species'] if pets.level(pet)>0 else 'Mysterious Egg','rarity':pet['rarity'] if pets.level(pet)>0 else None,'level':pets.level(pet),
                      'evolution':pets.evolution(pet),'coins':coin_asset['amount']}
                details['pet_sale']=sale
                event['details']=details;files[path]=json.dumps(event,ensure_ascii=False)+'\n'
        import pets
        transfers=[]
        for asset in (offer,request):
            if asset.get('type')!='pet':continue
            transferred=next((p for owner in pets_data.values() for p in owner['pets'] if p['id']==asset['pet_id']),None)
            if transferred:transfers.append({'pet_id':transferred['id'],'species':transferred['species'] if pets.level(transferred)>0 else 'Mysterious Egg'})
        details['pet_transfers']=transfers
        event['details']=details;files[path]=json.dumps(event,ensure_ascii=False)+'\n'
        receipt=copy.deepcopy(details)
        receipt.update(receipt_id=txid,completed_at=event.get('created_at',now))
        data['receipts'][txid]=receipt
        event_week['trades']+=1
        event_week['traded_coins']=round(event_week['traded_coins']+sum(float(a.get('amount',0)) for a in (offer,request) if a.get('type')=='coins'),3)
        if sale:
            data['sales'][txid]={**sale,'at':event.get('created_at',now),'receipt_id':txid}
    day=datetime.fromtimestamp(now,HOLIDAY_ZONE).date().isoformat()
    daily=data.setdefault('days',{}).setdefault(day,{'opening_supply':supply(before),
                         'minted':0,'burned':0,'trades':0,'traded_coins':0,'surrenders':0,'adoptions':0})
    daily.update(closing_supply=supply(after),saved_at=now)
    for key in ('minted','burned','trades','traded_coins','surrenders','adoptions'):
        delta=sum(w.get(key,0)-before_weeks.get(k,{}).get(key,0) for k,w in data['weeks'].items())
        daily[key]=round(daily.get(key,0)+delta,3)
    files[FILE]=json.dumps(data,ensure_ascii=False,sort_keys=True)+'\n'


def snapshot():
    with ledger.REPOSITORY_LOCK:
        if not ledger.refresh_for_read():raise RuntimeError('Economy snapshot cannot be refreshed.')
        return copy.deepcopy(_load()),copy.deepcopy(ledger._origin_state()[0])


def market_value(pet,data=None,now=None):
    import pets
    if pets.level(pet)==0:return None
    if data is None:data=_load()
    if not data:return None
    now=time.time() if now is None else now
    sales=[s for s in data['sales'].values() if now-30*86400<=s['at']<=now and s['species']==pet['species']]
    bucket=[s for s in sales if s['evolution']==pets.evolution(pet)]
    if len(bucket)>=3:sales=bucket
    if len(sales)<3:return None
    return {'coins':statistics.median(s['coins'] for s in sales),'count':len(sales)}


def report(data,wallet,key):
    week=copy.deepcopy(data['weeks'].get(key,{}))
    values=[float(e.get('coins',0)) for e in wallet.values()]
    sales=[s for s in data['sales'].values() if week_key(s['at'])==key]
    counts={}
    for receipt in data['receipts'].values():
        if week_key(receipt['completed_at'])!=key:continue
        for pet in receipt.get('pet_transfers',[]):counts[pet['species']]=counts.get(pet['species'],0)+1
    result={'week':key,'tracking_since':data['tracking_since'],'supply':supply(wallet),
            'wallets':len(values),'mean':statistics.mean(values) if values else 0,
            'median':statistics.median(values) if values else 0,'top':sorted(wallet.items(),key=lambda item:float(item[1].get('coins',0)),reverse=True)[:3],
            'activity':week,'pet_sales':len(sales),'highest_sale':max((s['coins'] for s in sales),default=None),
            'median_sale':statistics.median(s['coins'] for s in sales) if sales else None,
            'species':counts}
    from economy_health import assess
    previous_key=previous_week(week_bounds_start(key))
    previous=data.get('reports',{}).get(previous_key,{}).get('report')
    result['health']=assess(result,previous)
    return result


def previous_week(now):
    local=datetime.fromtimestamp(now,HOLIDAY_ZONE)
    monday=local.date()-timedelta(days=local.weekday())
    return week_key(datetime.combine(monday-timedelta(days=1),datetime.min.time(),HOLIDAY_ZONE).timestamp())


def week_bounds_start(key):
    year,week=key.split('-W')
    return datetime.fromisocalendar(int(year),int(week),1).replace(tzinfo=HOLIDAY_ZONE).timestamp()


def freeze_completed_weeks(now=None):
    """Retain missing closed-week snapshots after downtime; never replace a frozen week."""
    from market_transactions import run
    from economy_history import week_bounds
    now=time.time() if now is None else now
    def build():
        data=_load()
        if not data:return {},{'frozen':0},'economy-history-empty'
        wallet,_=ledger._origin_state();frozen=data.setdefault('weekly_snapshots',{});count=0
        for key in data['weeks']:
            if key not in frozen and week_bounds(key)[1]<=now:
                row=data.get('reports',{}).get(key,{}).get('report') or report(data,wallet,key)
                frozen[key]=copy.deepcopy(row);count+=1
        return {FILE:json.dumps(data)}, {'frozen':count},'economy-history-freeze'
    local=datetime.fromtimestamp(now,HOLIDAY_ZONE).date().isoformat()
    return run('economy-history-freeze:'+local,build)
