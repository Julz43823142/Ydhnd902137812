"""Aggregate product counts and approximate uniques; no event-level user profiles."""
import asyncio
import hashlib
import json
import math
import time
import threading
from collections import Counter
from datetime import datetime,timedelta

import shared_leaderboard as ledger
from holiday_events import HOLIDAY_ZONE
from market_transactions import run
from shark_admin import require_admin

FILE='feature_usage.json'
_pending=Counter();_sketches={};_flush_lock=asyncio.Lock()
_batch=None
_plays={}
_release_started=int(time.time())
_buffer_lock=threading.RLock()

# Public feature names only. Game counts come from saved starts, never menu clicks.
FEATURE_NAMES={
    'profile':'Profile','shop':'Shop','pets':'Pets','collection':'Collection Book',
    'trades':'Trades','wanted':'Wanted Market','shelter':'Pet Shelter','event':'Event Hub',
    'economy':'Economy Pages','week':'My Week','community':'Community Challenge',
    'random-puzzle':'Random Puzzle','daily':'Daily Puzzle','practice':'Puzzle Practice',
    'quests':'Quests','leaderboards':'Leaderboards','guess':'Guess the Chatter',
    'quote':'Quote Hunt','themes':'Profile Themes','boards':'Boards','pieces':'Pieces',
    'colors':'Name Colors','accessories':'Pet Accessories','receipts':'Trade Receipts',
    'matches':'Market Matches','open-market':'Open Market','boxes':'Mystery Boxes',
    'menu':'Menu','help':'Help / Rules',
    'connect':'Connect Four','ships':'Battleships','rps':'RPS','trivia':'Trivia Arena',
    'mines':'Minefield','escape':'Escape Room','chessle':'Chessle','lingo':'Lingo',
    'bomb':'Bomb Defusal','math':'Math Rush','detective':'Detective Case',
    'codebreaker':'Codebreaker','cluest':'Cluest','blackjack':'Blackjack','poker':'Poker',
    'puzzle-battle':'Puzzle Battle','rush':'Puzzle Rush','survival':'Survival',
    'chess':'PvP Chess','chess960':'Chess960','bot-chess':'Bot Chess',
}
PLAY_FEATURES=set('connect ships rps trivia mines escape chessle lingo bomb math detective codebreaker cluest blackjack poker puzzle-battle rush survival chess chess960 bot-chess'.split())
COMMAND_FEATURES={
    **dict.fromkeys(('profile','me'),'profile'),**dict.fromkeys(('pet','pets'),'pets'),
    'shop':'shop','collection':'collection','trade':'trades','event':'event',
    'week':'week','community':'community','challenge':'community',
    'economy':'economy','eco':'economy','r':'random-puzzle','rp':'random-puzzle',
    'randompuzzle':'random-puzzle','daily':'daily','practice':'practice',
    'quests':'quests','leaderboard':'leaderboards','lb':'leaderboards',
    'theme':'themes','board':'boards','piece':'pieces','box':'boxes',
    'm':'menu','menu':'menu','help':'help','info':'help','i':'help',
}
BUTTON_FEATURES={
    'Profile':'profile','My Profile':'profile','View Profile':'profile','Shop':'shop',
    'Pets':'pets','View Pet':'pets','View Pets':'pets','Collection':'collection',
    'Collection Book':'collection','Trade':'trades','Trade Player':'trades',
    'Trade Inbox':'trades','Wanted Market':'wanted','Pet Shelter':'shelter',
    'Event Hub':'event','Next Event':'event','Previous Event Recaps':'event',
    'My Week':'week','Community':'community','Quests':'quests','Leaderboards':'leaderboards',
    'Random Puzzle':'random-puzzle','Practice':'practice','Themes':'themes',
    'Boards':'boards','Pieces':'pieces','Name Colors':'colors','Pet Accessories':'accessories',
    'Trade Receipts':'receipts',
    'Open Trade':'open-market','Browse Trades':'open-market',
    'Free Daily Mystery Box':'boxes','Badge Box':'boxes',
    'Start / Next Round':'guess',
    'Menu':'menu','Back to Menu':'menu','Info':'help','Rules':'help',
    'Last Week Economy':'economy',
}


def note_command(command,uid,now=None):
    feature=COMMAND_FEATURES.get(str(command).lower().lstrip('!/'))
    if feature:note('use:'+feature,uid,now)


def note_interaction(interaction):
    """Resolve only allowlisted action labels/names; never store custom IDs or text."""
    data=interaction.data or {}
    if data.get('name'):
        note_command(data['name'],interaction.user.id);return
    custom=data.get('custom_id')
    message=getattr(interaction,'message',None)
    for row in getattr(message,'components',[]):
        for component in getattr(row,'children',[]):
            if getattr(component,'custom_id',None)==custom:
                feature=BUTTON_FEATURES.get(getattr(component,'label',None))
                if feature:note('use:'+feature,interaction.user.id)
                return


def note_play(kind,session_id,started_at):
    if kind not in PLAY_FEATURES or not session_id:return
    if isinstance(started_at,str):
        try:started_at=datetime.fromisoformat(started_at).timestamp()
        except ValueError:return
    if not isinstance(started_at,(int,float)):return
    key=hashlib.sha256((kind+':'+str(session_id)).encode()).hexdigest()
    with _buffer_lock:_plays[key]=(kind,float(started_at))


def collect_saved_plays(daily,survival,cutoff):
    """Recover recorded starts after restart; pending invitations are not plays."""
    for key,game in daily.get('chess_games',{}).items():
        kind='bot-chess' if game.get('mode')=='bot' else 'chess960' if str(game.get('variant','')).lower() in ('chess960','960') else 'chess'
        note_play(kind,game.get('game_id',key),game.get('started_at'))
    for key,game in daily.get('puzzle_racers_v1',{}).get('games',{}).items():
        note_play('puzzle-battle',game.get('id',key),game.get('started_at'))
    for key,game in daily.get('puzzle_rush',{}).items():
        if game.get('puzzle') is not None:
            note_play('rush',game.get('run_id',str(key)+':'+str(game.get('started_at'))),game.get('started_at'))
    for key,game in daily.get('puzzle_rush_runs_universal_v1',{}).items():
        note_play('rush',game.get('run_id',key),game.get('started_at'))
    for team in survival.get('teams',{}).values():
        for game in [team.get('current')]+list(team.get('history',[])):
            if isinstance(game,dict):note_play('survival',game.get('run_id'),game.get('started_at'))
    with _buffer_lock:
        for key,(_,at) in list(_plays.items()):
            if at<cutoff:_plays.pop(key,None)


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
    key=(day,feature)
    with _buffer_lock:
        _pending[key]+=1
        sketch_add(_sketches.setdefault(key,[0]*256),uid)
    # These pages have a single shared entrypoint; pagination is a user action.
    if feature.startswith('page:economy:'):note('use:economy',uid,now)
    elif feature.startswith('page:market:'):
        note('use:'+{'matches':'matches','open':'open-market','expired':'trades'}.get(feature.split(':')[-1],'trades'),uid,now)


def flush():
    global _batch
    if _batch is None:
        with _buffer_lock:
            pending={k:v for k,v in _pending.items() if v}
            plays=dict(_plays)
            if not pending and not plays:return
            sketches={k:list(_sketches[k]) for k in pending}
            for k,v in pending.items():
                _pending[k]-=v
                if not _pending[k]:_pending.pop(k,None);_sketches.pop(k,None)
            for key in plays:_plays.pop(key,None)
        import uuid
        _batch=(uuid.uuid4().hex,pending,sketches,plays)
    token,pending,sketches,plays=_batch
    def build():
        raw=ledger._origin_file(FILE);data=json.loads(raw) if raw else {'tracking_since':int(time.time()),'days':{},'all':{}}
        data.setdefault('usage_since',_release_started)
        for (day,feature),count in pending.items():
            for bucket in (data['days'].setdefault(day,{}),data['all']):
                row=bucket.setdefault(feature,{'count':0,'uniques':[0]*256});row['count']+=count
                row['uniques']=[max(a,b) for a,b in zip(row['uniques'],sketches[(day,feature)])]
        for key,(kind,at) in plays.items():
            if at<data['usage_since'] or key in data.get('plays_seen',{}):continue
            add_play(data,kind,key,at)
        return {FILE:json.dumps(data)}, {'saved':True},'usage-flush'
    run('usage-flush:'+token,build)
    _batch=None


def add_play(data,kind,key,at):
    data.setdefault('usage_since',_release_started)
    if at<data['usage_since'] or key in data.setdefault('plays_seen',{}):return
    data['plays_seen'][key]=int(at)  # opaque session hashes, never player IDs
    day=datetime.fromtimestamp(at,HOLIDAY_ZONE).date().isoformat()
    for bucket in (data['days'].setdefault(day,{}),data['all']):
        row=bucket.setdefault('use:'+kind,{'count':0,'uniques':[0]*256});row['count']+=1


def recover_plays():
    with ledger.REPOSITORY_LOCK:
        if not ledger.refresh_for_read():return
        data=json.loads(ledger._origin_file(FILE) or '{}')
        daily=json.loads(ledger._origin_file('daily_puzzle_state.json') or '{}')
        survival=json.loads(ledger._origin_file('survival_runs.json') or '{}')
    collect_saved_plays(daily,survival,data.get('usage_since',_release_started))
    with _buffer_lock:
        for key in data.get('plays_seen',{}):_plays.pop(key,None)


def popularity(actor_id,all_time=False,now=None):
    """Readable counts from this release; Monday calendar week in Amsterdam."""
    require_admin(actor_id)
    with ledger.REPOSITORY_LOCK:
        if not ledger.refresh_for_read():raise RuntimeError('Analytics cannot be refreshed.')
        data=json.loads(ledger._origin_file(FILE) or '{}')
    today=datetime.fromtimestamp(time.time() if now is None else now,HOLIDAY_ZONE).date()
    monday=today-timedelta(days=today.weekday())
    counts=Counter()
    buckets=[data.get('all',{})] if all_time else [bucket for day,bucket in data.get('days',{}).items() if monday.isoformat()<=day<=today.isoformat()]
    for bucket in buckets:
        for feature,row in bucket.items():
            if feature.startswith('use:') and feature[4:] in FEATURE_NAMES and row.get('count',0)>0:
                counts[feature[4:]]+=row['count']
    rows=[{'feature':key,'name':FEATURE_NAMES[key],'count':count,'plays':key in PLAY_FEATURES} for key,count in counts.items()]
    return {'rows':sorted(rows,key=lambda r:(-r['count'],r['name'])),
            'since':data.get('usage_since'),'week_start':monday.isoformat()}


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
            async with _flush_lock:
                await asyncio.to_thread(recover_plays)
                await asyncio.to_thread(flush)
        except Exception:pass  # keep buffered counts and retry; never block a game interaction


def attach_activity(files,events):
    """Game starts/completions are counted atomically with their immutable audit."""
    rows=[]
    for event in events:
        for item in event.get('details',{}).get('activity',[]):
            if item['action'] in ('feature_played','minigame_started','minigame_completed','chess_completed','survival_complete','puzzle_battle_completed'):
                rows.append((event['created_at'],item))
    if not rows:return
    raw=files.get(FILE) or ledger._origin_file(FILE)
    data=json.loads(raw) if raw else {'tracking_since':int(time.time()),'days':{},'all':{}}
    for at,item in rows:
        if item['action']=='feature_played':
            key=hashlib.sha256((item['kind']+':'+item['session_id']).encode()).hexdigest()
            add_play(data,item['kind'],key,at)
            continue
        day=datetime.fromtimestamp(at,HOLIDAY_ZONE).date().isoformat()
        feature='game:'+item.get('kind',item['action'])+':'+('started' if item['action'].endswith('started') else 'completed')
        for bucket in (data['days'].setdefault(day,{}),data['all']):
            row=bucket.setdefault(feature,{'count':0,'uniques':[0]*256});row['count']+=1;sketch_add(row['uniques'],item['uid'])
    files[FILE]=json.dumps(data)
