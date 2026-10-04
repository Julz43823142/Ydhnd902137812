"""Aggregate product counts and approximate uniques; no event-level user profiles."""
import asyncio
import hashlib
import json
import math
import time
from collections import Counter
from datetime import datetime,timedelta

import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE
from market_transactions import run
from shark_admin import require_admin

FILE='feature_usage.json'
_pending=Counter();_sketches={};_flush_lock=asyncio.Lock()
_batch=None


def sketch_add(registers,uid):
    n=int.from_bytes(hashlib.sha256(str(uid).encode()).digest()[:8],'big')
    index=n&255;remaining=n>>8
    rank=57-remaining.bit_length() if remaining else 57
    registers[index]=max(registers[index],rank)


def unique(registers):
    value=.7213/(1+1.079/256)*256**2/sum(2**(-r) for r in registers)
    zeros=registers.count(0)
    if zeros and value<=640:value=256*math.log(256/zeros)
    return round(value)


def note(feature,uid,now=None):
    # Only normalized feature names; never command arguments, raw text or target IDs.
    import re
    if str(feature).startswith('command:'):
        allowed={'!m','!menu','!r','!rp','!randompuzzle','!profile','!me','!pet','!pets','!shop','!theme','!board','!piece','!coins','!balance','!trade','!donate','!rush','!survival','!pvp','!playbot','!daily','!event','!week','!economy','!eco','!collection','!community','!challenge','!box','!help','!i','!info','!stats','!leaderboard','!lb','!quests'}
        if str(feature).split(':',1)[1] not in allowed:feature='command:other'
    feature=':'.join(part if len(part)<=30 and re.fullmatch(r'[a-zA-Z!_-]+[0-9]?',part) else 'control' for part in str(feature).split(':'))[:80]
    day=datetime.fromtimestamp(time.time() if now is None else now,HOLIDAY_ZONE).date().isoformat()
    key=(day,feature);_pending[key]+=1
    sketch_add(_sketches.setdefault(key,[0]*256),uid)


def flush():
    global _batch
    if _batch is None:
        pending={k:v for k,v in _pending.items() if v}
        if not pending:return
        sketches={k:list(_sketches[k]) for k in pending}
        for k,v in pending.items():
            _pending[k]-=v
            if not _pending[k]:_pending.pop(k,None);_sketches.pop(k,None)
        import uuid
        _batch=(uuid.uuid4().hex,pending,sketches)
    token,pending,sketches=_batch
    def build():
        raw=ledger._origin_file(FILE);data=json.loads(raw) if raw else {'tracking_since':int(time.time()),'days':{},'all':{}}
        for (day,feature),count in pending.items():
            for bucket in (data['days'].setdefault(day,{}),data['all']):
                row=bucket.setdefault(feature,{'count':0,'uniques':[0]*256});row['count']+=count
                row['uniques']=[max(a,b) for a,b in zip(row['uniques'],sketches[(day,feature)])]
        return {FILE:json.dumps(data)}, {'saved':True},'usage-flush'
    run('usage-flush:'+token,build)
    _batch=None


def summary(actor_id,days=None,now=None):
    require_admin(actor_id)
    with ledger.REPOSITORY_LOCK:
        if not ledger.refresh_for_read():raise RuntimeError('Analytics cannot be refreshed.')
        raw=ledger._origin_file(FILE);data=json.loads(raw) if raw else {'days':{},'all':{}}
    if days is None:rows=data['all']
    else:
        today=datetime.fromtimestamp(time.time() if now is None else now,HOLIDAY_ZONE).date()
        first=today-timedelta(days=days-1);rows={}
        for day,bucket in data['days'].items():
            if not first.isoformat()<=day<=today.isoformat():continue
            for feature,row in bucket.items():
                target=rows.setdefault(feature,{'count':0,'uniques':[0]*256});target['count']+=row['count']
                target['uniques']=[max(a,b) for a,b in zip(target['uniques'],row['uniques'])]
    previous={}
    if days is not None:
        begin=first-timedelta(days=days)
        for day,bucket in data['days'].items():
            if begin.isoformat()<=day<first.isoformat():
                for feature,row in bucket.items():previous[feature]=previous.get(feature,0)+row['count']
    result=[]
    for feature,row in rows.items():
        old=previous.get(feature)
        trend=('New / no prior recorded uses' if not old else 'Rising' if row['count']>old*1.2 else 'Falling' if row['count']<old*.8 else 'Stable') if days else None
        result.append({'feature':feature,'count':row['count'],'unique':unique(row['uniques']),'trend':trend})
    return sorted(result,key=lambda r:r['count'],reverse=True)


async def loop():
    while True:
        await asyncio.sleep(60)
        try:
            async with _flush_lock:await asyncio.to_thread(flush)
        except Exception:pass  # keep buffered counts and retry; never block a game interaction


def attach_activity(files,events):
    """Game starts/completions are counted atomically with their immutable audit."""
    rows=[]
    for event in events:
        for item in event.get('details',{}).get('activity',[]):
            if item['action'] in ('minigame_started','minigame_completed','chess_completed','survival_complete','puzzle_battle_completed'):
                rows.append((event['created_at'],item))
    if not rows:return
    raw=files.get(FILE) or ledger._origin_file(FILE)
    data=json.loads(raw) if raw else {'tracking_since':int(time.time()),'days':{},'all':{}}
    for at,item in rows:
        day=datetime.fromtimestamp(at,HOLIDAY_ZONE).date().isoformat()
        feature='game:'+item.get('kind',item['action'])+':'+('started' if item['action'].endswith('started') else 'completed')
        for bucket in (data['days'].setdefault(day,{}),data['all']):
            row=bucket.setdefault(feature,{'count':0,'uniques':[0]*256});row['count']+=1;sketch_add(row['uniques'],item['uid'])
    files[FILE]=json.dumps(data)
