"""Audited public game/economy summaries, attached to existing commits."""
import copy
import json
import time
from datetime import datetime

import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE

FILE='shark_activity.json'


def read():
    raw=ledger._origin_file(FILE)
    return json.loads(raw) if raw else {'tracking_since':int(time.time()),'days':{},'totals':{},'records':{}}


def add(data,uid,name,action,amount,at,metadata=None):
    day=datetime.fromtimestamp(at,HOLIDAY_ZONE).date().isoformat()
    bucket=data['days'].setdefault(day,{'users':{},'totals':{},'notable':[]})
    user=bucket['users'].setdefault(str(uid),{'name':name,'counts':{}})
    if name and name not in {'Player',str(uid),'server'}:user['name']=name
    user['counts'][action]=round(user['counts'].get(action,0)+amount,3)
    bucket['totals'][action]=round(bucket['totals'].get(action,0)+amount,3)
    data['totals'][action]=round(data['totals'].get(action,0)+amount,3)
    # Records use explicit scored results, never random ordinary games.
    metadata=metadata or {}
    score=metadata.get('score')
    if isinstance(score,(int,float)) and score>0:
        mode=str(metadata.get('record_kind') or metadata.get('kind') or action)
        old=data['records'].get(mode)
        if not old or score>old['score']:
            record={'feature':mode,'score':score,'user_id':str(uid),'name':name,'at':at}
            data['records'][mode]=record;bucket['notable'].append({'type':'record',**record})
    if isinstance(score,(int,float)) and score>0:
        records=data.setdefault('personal_records',{}).setdefault(str(uid),{})
        feature=str(metadata.get('record_kind') or metadata.get('kind') or action)
        if score>records.get(feature,0):
            records[feature]=score
            bucket['notable'].append({'type':'personal_record','feature':feature,'score':score,'user_id':str(uid),'name':name,'at':at})
    if metadata.get('notable'):
        bucket['notable'].append(metadata['notable'])


def observe(files,message):
    events=[]
    for path,raw in list(files.items()):
        if path.endswith('.json') and path.split('/')[0] in {ledger.EVENT_DIR,'quest_events','pet_events','market_events'} and ledger._origin_file(path) is None:
            events.append(json.loads(raw))
    if not events:return
    import feature_usage
    feature_usage.attach_activity(files,events)
    data=json.loads(files[FILE]) if FILE in files else read()
    for event in events:
        at=event.get('created_at',time.time());details=event.get('details',{});uid=event.get('user_id',details.get('user_id','server'))
        name=event.get('display_name',details.get('buyer_name',str(uid)))
        op=event.get('operation','');action=event.get('action')
        if op=='quest-progress' and action and action!='minigame_win':
            add(data,uid,name,action,float(event.get('amount',1)),at,event.get('metadata'))
        for entry in details.get('activity',[]):
            add(data,entry['uid'],entry.get('name','Player'),entry['action'],entry.get('amount',1),at,entry)
        if op in ('trade-accept','open-trade-accept','wanted-fulfill') and details.get('offer') and details.get('request'):
            add(data,'server','Server','trades',1,at)
            for key in ('seller_user_id','buyer_user_id','from_user_id','recipient_user_id'):
                if key in details:add(data,details[key],details.get(key.replace('_user_id','_name'),'Player'),'personal_trades',1,at)
            moved=sum(float(a.get('amount',0)) for a in (details['offer'],details['request']) if a.get('type')=='coins')
            add(data,'server','Server','coins_moved',moved,at)
            first=details.get('seller_user_id',details.get('from_user_id'))
            second=details.get('buyer_user_id',details.get('recipient_user_id'))
            for asset,giver,receiver in ((details['offer'],first,second),(details['request'],second,first)):
                if asset.get('type')=='pet':
                    if giver:add(data,giver,'Player','pets_traded',1,at)
                    if receiver:add(data,receiver,'Player','pets_received',1,at)

        if op in ('shelter-surrender','shelter-adopt'):add(data,uid,name,op,1,at)
        if op=='badge-box':
            add(data,uid,name,'boxes_opened',1,at)
            holiday=details.get('holiday') or details.get('event')
            if holiday:
                add(data,uid,name,'holiday_box:'+str(holiday),1,at)
                add(data,uid,name,'holiday_spent:'+str(holiday),float(details.get('spent',0)),at)
                add(data,uid,name,'holiday_badges:'+str(holiday),1,at)
                day=datetime.fromtimestamp(at,HOLIDAY_ZONE).date().isoformat()
                data['days'][day]['notable'].append({'type':'holiday_reward','holiday':str(holiday),'badge':details.get('badge'),'rarity':details.get('rarity')})
        if details.get('fed'):add(data,uid,name,'pet_feed',1,at)
        if details.get('completed') and str(event.get('transaction_id','')).startswith('pet'):add(data,uid,name,'pet_puzzle',1,at)
        # Each pet transaction carries only safe public hatch metadata.
        for hatch in details.get('hatches',[]):
            add(data,uid,name,'pets_hatched',1,at,{'notable':{'type':'hatch','name':name,**hatch}} if hatch['rarity'] in ('epic','legendary') else {})
    if ledger.LEGACY_FILE in files:
        before=json.loads(ledger._origin_file(ledger.LEGACY_FILE) or '{}');after=json.loads(files[ledger.LEGACY_FILE])
        ignored=any(any(token in str(e.get('operation','')).lower() for token in ('wager','admin','adjust')) for e in events)
        # Minigames provides its own wallet summaries, so don't count those twice.
        if not ignored and not any(e.get('operation')=='minigames' for e in events):
            for uid,row in after.items():
                delta=float(row.get('coins',0))-float(before.get(uid,{}).get('coins',0))
                if delta:add(data,uid,row.get('name','Player'),'coins_earned' if delta>0 else 'coins_spent',abs(delta),time.time())
    if 'pets_state.json' in files:
        import pets
        owners=json.loads(files['pets_state.json']);wallet=json.loads(files.get(ledger.LEGACY_FILE) or ledger._origin_file(ledger.LEGACY_FILE) or '{}');day=datetime.fromtimestamp(time.time(),HOLIDAY_ZONE).date().isoformat()
        bucket=data['days'].setdefault(day,{'users':{},'totals':{},'notable':[]})
        for uid,owner in owners.items():
            user=bucket['users'].setdefault(uid,{'name':wallet.get(uid,{}).get('name',uid),'counts':{}})
            user['pets_owned']=sum(not p.get('died_at') for p in owner['pets'])
            user['highest_pet_level']=max((pets.level(p) for p in owner['pets']),default=0)
    files[FILE]=json.dumps(data,ensure_ascii=False,sort_keys=True)+'\n'


def period(data,start,end,uid=None):
    totals={};users={};notable=[]
    for day,bucket in sorted(data['days'].items()):
        stamp=datetime.fromisoformat(day).replace(tzinfo=HOLIDAY_ZONE).timestamp()
        if not start<=stamp<end:continue
        for user_id,user in bucket['users'].items():
            if uid is not None and str(uid)!=user_id:continue
            target=users.setdefault(user_id,{'name':user['name'],'counts':{}})
            for key in ('pets_owned','highest_pet_level'):
                if key in user:target[key]=user[key]
            for action,value in user['counts'].items():
                totals[action]=round(totals.get(action,0)+value,3)
                target['counts'][action]=round(target['counts'].get(action,0)+value,3)
        notable.extend(bucket.get('notable',[]))
    return {'totals':totals,'users':users,'notable':copy.deepcopy(notable),'tracking_since':data['tracking_since']}
