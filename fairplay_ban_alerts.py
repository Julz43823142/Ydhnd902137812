"""Opt-in official account-closure notifications, NEVER an analytical feature."""
import copy
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import re
import time
from fairplay_data import PubAPI, ReviewError, ScanDeadline, username

FAIRPLAY_CLOSED='closed:fair_play_violations'


@dataclass(frozen=True)
class Settings:
    interval: int=3600
    lookback_days: int=90
    watch_days: int=365
    max_accounts: int=50
    max_opponents: int=2000
    max_games: int=2000
    max_subscriptions: int=5
    max_total_subscriptions: int=500
    cycle_seconds: int=3000


def subscribe(store,user,target,settings=Settings()):
    target=username(target);uid=str(int(user))
    def change(s):
        u=s['users'].setdefault(uid,{'muted':False,'subscriptions':{}})
        if target in u['subscriptions']:return False
        if sum(len(row['subscriptions']) for row in s['users'].values())>=settings.max_total_subscriptions:
            raise ValueError('Global subscription capacity reached')
        if len(u['subscriptions'])>=settings.max_subscriptions:raise ValueError('Subscription limit reached')
        accounts={t for row in s['users'].values() for t in row['subscriptions']}
        if target not in accounts and len(accounts)>=settings.max_accounts:raise ValueError('Tracked-account limit reached')
        u['subscriptions'][target]={'created':time.time(),'seen':{}}
        return True
    return store.update(change)


def unsubscribe(store,user,target):
    target=username(target);uid=str(int(user))
    def change(s):
        u=s['users'].get(uid)
        if not u:return False
        removed=u['subscriptions'].pop(target,None) is not None
        for event in s['outbox'].values():
            if event['user']==uid and event['target']==target and event['state']=='pending':event['state']='cancelled'
        return removed
    return store.update(change)


def mute(store,user,enabled):
    def change(s):s['users'].setdefault(str(int(user)),{'muted':False,'subscriptions':{}})['muted']=bool(enabled)
    store.update(change)


def _opponent(row,target):
    if not isinstance(row,dict):return None
    try:
        white=username(row['white']['username']);black=username(row['black']['username'])
        other=black if white==target else white if black==target else None
        link=row.get('url','');ended=float(row['end_time'])
        if not other or not re.fullmatch(r'https://www\.chess\.com/game/(?:live|daily)/[0-9]+',link):return None
        if not 0<ended<10**12:return None
        return other,link,ended
    except (KeyError,TypeError,ValueError,ReviewError):return None


def _game_details(row,target):
    """Public game metadata from the followed player's perspective."""
    try:
        side='white' if username(row['white']['username'])==target else 'black'
        player=row[side]
        outcome=str(player.get('result','')).lower()
        result=('Win' if outcome=='win' else 'Draw' if outcome in
                ('agreed','repetition','stalemate','insufficient','50move','timevsinsufficient')
                else 'Loss' if outcome else 'Unknown')
        rating=player.get('rating')
        rating=rating if isinstance(rating,int) and not isinstance(rating,bool) else None
        return {'rated':row.get('rated') if isinstance(row.get('rated'),bool) else None,
                'result':result,'rating':rating,'time_class':row.get('time_class'),
                'time_control':row.get('time_control')}
    except (KeyError,TypeError,ValueError,ReviewError):
        return {'rated':None,'result':'Unknown','rating':None}


def collect_history(api,target,old,now,settings):
    cutoff=now-settings.lookback_days*86400
    result=copy.deepcopy(old or {'games':{},'last_success':0})
    archives=api.get(target,'/games/archives')
    if not isinstance(archives,dict):raise ReviewError('History unavailable')
    months=[]
    for value in archives.get('archives',[]):
        match=re.fullmatch(r'https://api\.chess\.com/pub/player/'+re.escape(target)+r'/games/([0-9]{4})/(0[1-9]|1[0-2])',str(value),re.I)
        if match:
            year,month=map(int,match.groups())
            # One extra month avoids month-boundary gaps; completion times filter.
            if datetime(year,month,1,tzinfo=timezone.utc).timestamp()>=cutoff-32*86400:months.append((year,month))
    months=sorted(set(months),reverse=True)[:14]
    if result['last_success']:
        last=datetime.fromtimestamp(result['last_success'],timezone.utc)
        # Revisit the previous month too; PubAPI caches can lag final results.
        boundary=last.timestamp()-32*86400
        months=[m for m in months if datetime(*m,1,tzinfo=timezone.utc).timestamp()>=boundary]
    complete=True
    for year,month in months:
        payload=api.get(target,f'/games/{year}/{month:02d}')
        if not isinstance(payload,dict) or not isinstance(payload.get('games'),list):complete=False;continue
        for row in payload['games']:
            entry=_opponent(row,target)
            if entry and entry[2]>=cutoff:
                other,link,ended=entry
                result['games'][link]={'opponent':other,'url':link,'ended':ended,\n                    **_game_details(row,target)}
    # Estimate Elo change only from a known preceding game in the same control.
    # Missing history or unrated games must never produce an invented delta.
    chronological=sorted(result['games'].values(),key=lambda g:g['ended'])
    previous_ratings={}
    for game in chronological:
        control=(game.get('time_class'),game.get('time_control'))
        rating=game.get('rating')
        if game.get('rated') is True and isinstance(rating,int) and all(control):
            previous=previous_ratings.get(control)
            game['rating_delta']=rating-previous if previous is not None else None
            previous_ratings[control]=rating
        else:
            game['rating_delta']=None
    # Retain discovered opponents independently of the history download window.
    opponents=result.setdefault('opponents',{})
    for row in result['games'].values():
        o=opponents.setdefault(row['opponent'],{'url':row['url'],'ended':row['ended'],'games':[]})
        if row['url'] not in o['games']:o['games'].append(row['url'])
        o['games']=o['games'][-settings.max_games:]
        if row['ended']>=o['ended']:\n            o.update(url=row['url'],ended=row['ended'],rated=row.get('rated'),\n                result=row.get('result','Unknown'),rating_delta=row.get('rating_delta'))
    result['opponents']={o:g for o,g in opponents.items() if g['ended']>=now-settings.watch_days*86400}
    result['games']=dict(sorted(result['games'].items(),key=lambda kv:kv[1]['ended'],reverse=True)[:settings.max_games])
    if complete:result['last_success']=now
    return result


def poll(store,settings=Settings(),api_factory=PubAPI,now=None,cancel=None):
    now=time.time() if now is None else now
    snapshot=store.snapshot();targets=sorted({t for u in snapshot['users'].values() for t in u['subscriptions']})
    if not targets:return {'profiles':0,'targets':0}
    api=api_factory(ScanDeadline(time.monotonic()+settings.cycle_seconds,cancel))
    histories={};profiles={};requests=0
    try:
        for target in targets:
            if cancel is not None and cancel.is_set():break
            try:histories[target]=collect_history(api,target,snapshot['accounts'].get(target),now,settings)
            except (ReviewError,ValueError):continue
        watched={o for target in targets for o in histories.get(target,snapshot['accounts'].get(target,{})).get('opponents',{})}
        cached=snapshot['profiles']
        # Rotate stale profiles by oldest successful check, not alphabetic starvation.
        watched=sorted(watched,key=lambda o:(cached.get(o,{}).get('checked',0),o))[:settings.max_opponents]
        baseline={o for u in snapshot['users'].values() for target,sub in u['subscriptions'].items()
                  for o in histories.get(target,snapshot['accounts'].get(target,{})).get('opponents',{})
                  if o not in sub['seen'] and cached.get(o,{}).get('checked',0)<sub['created']}
        for other in watched:
            if cancel is not None and cancel.is_set():break
            previous=cached.get(other,{})
            if other not in baseline and now-previous.get('checked',0)<settings.interval:
                profiles[other]=previous;continue
            try:
                requests+=1
                profile=api.get(other,profile=True)
                if not isinstance(profile,dict) or not isinstance(profile.get('status'),str) or not profile['status']:continue
                profiles[other]={'status':profile['status'],'checked':now}
            except ReviewError:profiles[other]={'status':None,'checked':now}  # Cache failure, never treat it as active.
    finally:api.close()
    if cancel is not None and cancel.is_set():return {'profiles':requests,'targets':len(targets),'cancelled':True}
    def change(s):
        active={t for u in s['users'].values() for t in u['subscriptions']}
        s['accounts'].update({t:h for t,h in histories.items() if t in active})
        s['accounts']={t:h for t,h in s['accounts'].items() if t in active}
        s['profiles'].update(profiles)
        for uid,u in s['users'].items():
            for target,sub in u['subscriptions'].items():
                opponents=s['accounts'].get(target,{}).get('opponents',{})
                sub['seen']={o:v for o,v in sub['seen'].items() if o in opponents}
                for other,game in opponents.items():
                    profile=profiles.get(other)
                    if profile is None or not isinstance(profile.get('status'),str):continue
                    if other not in sub['seen'] and profile['checked']<sub['created']:continue
                    closed=profile['status']==FAIRPLAY_CLOSED
                    previous=sub['seen'].get(other)
                    sub['seen'][other]=closed
                    if previous is not False or not closed:continue
                    key=hashlib.sha256(f'{uid}:{target}:{other}:{FAIRPLAY_CLOSED}'.encode()).hexdigest()[:24]
                    s['outbox'].setdefault(key,{'user':uid,'target':target,'opponent':other,'url':game['url'],
                        'games':len(game['games']),'most_recent_game':game['ended'],\n                        'rated':game.get('rated'),'result':game.get('result','Unknown'),\n                        'rating_delta':game.get('rating_delta'),'detected':profile['checked'],'state':'pending','attempts':0})
    store.update(change)
    return {'profiles':requests,'targets':len(targets)}


def reserve(store,key):
    def change(s):
        event=s['outbox'][key]
        user=s['users'].get(event['user'],{})
        if event['target'] not in user.get('subscriptions',{}) or event['state']!='pending':return None
        event['state']='sending';event['attempts']+=1
        return copy.deepcopy(event)
    return store.update(change)


def mark(store,key,state,message_id=None):
    def change(s):
        event=s['outbox'][key];event['state']=state
        if message_id is not None:event['message_id']=int(message_id)
    store.update(change)
