"""Pure rules for fifteen minigames. All amounts here are coins, never points."""
import copy
import math
import random
import time
import re
from fractions import Fraction
from datetime import datetime, timezone

import minigames_poker as poker
import minigames_blackjack as blackjack
import minigames_chessle as chessle
from minigames_content import select_ladder
from minigames_escape import ESCAPE_BUILD, build_escape, answer_matches

KINDS={'connect':'Connect Four','ships':'Battleships','rps':'Rock Paper Scissors',
       'trivia':'Trivia Arena','mines':'Minefield','escape':'Escape Room','chessle':'Chessle',
       'lingo':'Lingo','bomb':'Bomb Defusal','math':'Math Rush','detective':'Detective Case',
       'codebreaker':'Codebreaker','cluest':'Cluest','poker':'Poker','blackjack':'Blackjack'}
LIMITS={'connect':2,'ships':2,'rps':2,'trivia':12,'mines':8,'escape':8,'chessle':1,
        'lingo':1,'bomb':6,'math':1,'detective':4,'codebreaker':1,'cluest':1,'poker':6,'blackjack':1}
MINIMUM={'connect':2,'ships':2,'rps':2,'trivia':1,'mines':1,'escape':1,'chessle':1,
         'lingo':1,'bomb':2,'math':1,'detective':1,'codebreaker':1,'cluest':1,'poker':2,'blackjack':1}
MAX_STAKE=50
TURN_SECONDS=120

# Minefield stays five buttons wide because Discord ActionRows cap at five
# buttons. Difficulty comes from a much taller board and a denser mine ratio;
# the UI renders this as four stacked 5x4 panels.
MINEFIELD_WIDTH=5
MINEFIELD_HEIGHT=16
MINEFIELD_CELLS=MINEFIELD_WIDTH*MINEFIELD_HEIGHT
MINEFIELD_MINES=19


def member(wallets, uid, name):
    # Supplied snapshots have already been normalized by shared_leaderboard.
    if uid not in wallets:
        wallets[uid]={'name':name,'points':0.0,'coins':0.0}
    return wallets[uid]


def move_coins(wallets, player, delta):
    e=member(wallets,player['id'],player['name']);balance=round(e.get('coins',0)+delta,3)
    if balance<0:raise ValueError(f'{player["name"]} does not have enough coins.')
    e['coins']=balance


def reward(state,wallets,g,uid,amount,now):
    day=datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%d')
    state['daily']={k:v for k,v in state.get('daily',{}).items() if k.startswith(day+':')}
    key=day+':'+uid;used=state['daily'].get(key,0)
    amount=max(0,amount)
    player=next(p for p in g['players'] if p['id']==uid)
    move_coins(wallets,player,amount);state['daily'][key]=used+amount
    return amount



def _empty_stat_block():
    return {
        'played': 0, 'wins': 0, 'losses': 0, 'draws': 0,
        'coin_net': 0.0, 'current_streak': 0, 'best_streak': 0,
    }


def _ensure_stats(state):
    state.setdefault('stats', {})
    state.setdefault('stats_seen', [])
    return state['stats']


def _stats_bucket(g, uid):
    if uid in g.get('winners', []):
        return 'win'
    result = str(g.get('result', '') or '').casefold()
    if 'draw' in result or 'push' in result:
        return 'draw'
    return 'loss'


def _stats_net(g, uid, bucket):
    explicit = g.get('stats_net', {})
    if uid in explicit:
        return float(explicit.get(uid, 0) or 0)
    paid = float(g.get('paid', {}).get(uid, 0) or 0)
    stake = float(g.get('stake', 0) or 0)
    if g.get('kind') == 'blackjack':
        if not stake:
            return paid
        if bucket == 'draw':
            return 0.0
        if bucket == 'win':
            # Blackjack stores profit in paid, not the returned stake.
            return paid
        return -stake
    if stake:
        if bucket == 'draw':
            return 0.0
        return paid - stake if bucket == 'win' else -stake
    return paid


def _record_one_finished_game(state, g):
    """Record one completed, non-void game exactly once."""
    if g.get('status') != 'finished' or not g.get('started') or g.get('stats_void'):
        return
    ended = g.get('ended')
    token = f"{g.get('id', '')}:{ended}"
    seen = state.setdefault('stats_seen', [])
    if token in seen:
        return

    stats = _ensure_stats(state)
    kind = str(g.get('kind', 'unknown'))
    for player in g.get('players', []):
        uid = str(player.get('id'))
        if not uid:
            continue
        entry = stats.setdefault(uid, {
            'name': player.get('name') or uid,
            'total': _empty_stat_block(),
            'games': {},
            'blackjack_naturals': 0,
            'blackjack_splits': 0,
            'blackjack_doubles': 0,
            'trivia_correct': 0,
            'trivia_best': 0,
        })
        entry['name'] = player.get('name') or entry.get('name') or uid
        total = entry.setdefault('total', _empty_stat_block())
        game = entry.setdefault('games', {}).setdefault(kind, _empty_stat_block())
        bucket = _stats_bucket(g, uid)
        net = round(_stats_net(g, uid, bucket), 3)

        for block in (total, game):
            block['played'] = int(block.get('played', 0)) + 1
            if bucket == 'win':
                block['wins'] = int(block.get('wins', 0)) + 1
                block['current_streak'] = int(block.get('current_streak', 0)) + 1
                block['best_streak'] = max(int(block.get('best_streak', 0)), block['current_streak'])
            elif bucket == 'draw':
                block['draws'] = int(block.get('draws', 0)) + 1
            else:
                block['losses'] = int(block.get('losses', 0)) + 1
                block['current_streak'] = 0
            block['coin_net'] = round(float(block.get('coin_net', 0) or 0) + net, 3)

        if kind == 'blackjack':
            result = str(g.get('result', '') or '').casefold()
            if uid in g.get('winners', []) and 'natural blackjack' in result:
                entry['blackjack_naturals'] = int(entry.get('blackjack_naturals', 0)) + 1
            data = g.get('data', {}) or {}
            hands = data.get('hands', []) or []
            entry['blackjack_splits'] = int(entry.get('blackjack_splits', 0)) + max(0, len(hands) - 1)
            entry['blackjack_doubles'] = int(entry.get('blackjack_doubles', 0)) + sum(
                1 for hand in hands if float(hand.get('bet_mult', 1) or 1) > 1
            )
        elif kind == 'trivia':
            correct = int((g.get('data', {}) or {}).get('correct', {}).get(uid, 0) or 0)
            entry['trivia_correct'] = int(entry.get('trivia_correct', 0)) + correct
            entry['trivia_best'] = max(int(entry.get('trivia_best', 0)), correct)

    seen.append(token)
    # Only the retained game history can ever be backfilled, so a bounded token
    # list is enough and keeps the encrypted metadata small.
    state['stats_seen'] = seen[-240:]


def sync_stats_from_history(state):
    """Backfill any retained finished games, then keep future results current."""
    _ensure_stats(state)
    finished = sorted(
        (g for g in state.get('games', {}).values() if g.get('status') == 'finished'),
        key=lambda g: float(g.get('ended', 0) or 0),
    )
    for game in finished:
        _record_one_finished_game(state, game)
    return state.get('stats', {})


def stats_for_user(state, uid, name='Player'):
    sync_stats_from_history(state)
    uid = str(uid)
    entry = state.get('stats', {}).get(uid)
    if entry:
        return copy.deepcopy(entry)
    return {
        'name': name,
        'total': _empty_stat_block(),
        'games': {},
        'blackjack_naturals': 0,
        'blackjack_splits': 0,
        'blackjack_doubles': 0,
        'trivia_correct': 0,
        'trivia_best': 0,
    }

def finish(state,wallets,g,winners,now,reason,amount=1,refund=False):
    if g['status']=='finished':return
    g['status']='finished';g['ended']=now;g['result']=reason;g['winners']=winners;g['paid']={};g['stats_void']=bool(refund)
    if g.get('reserved'):
        if refund or not winners:
            for p in g['players']:move_coins(wallets,p,g['stake'])
            g['result']+=' Stakes refunded.'
        else:
            pot=g['stake']*len(g['players'])
            if g['kind']=='mines':pot=2*g['stake']
            # Wager results have no extra minted free-win reward.
            for uid in winners:
                p=next(p for p in g['players'] if p['id']==uid)
                value=pot/len(winners);move_coins(wallets,p,value);g['paid'][uid]=value
        g['reserved']=False
    elif not refund:
        for uid in winners:
            value=amount.get(uid,0) if isinstance(amount,dict) else amount
            g['paid'][uid]=reward(state,wallets,g,uid,value,now)
    sync_stats_from_history(state)


def _finish_blackjack(state, wallets, g, now, outcome):
    """Resolve opening natural Blackjack, forced loss or legacy single-hand result."""
    if g.get('status') == 'finished':
        return
    player = g['players'][0]; uid = player['id']
    stake = float(g.get('stake', 0) or 0)
    reserved_total = float(g.get('blackjack_reserved', stake if g.get('reserved') else 0) or 0)
    g['status']='finished';g['ended']=now;g['winners']=[];g['paid']={};g['stats_void']=False
    g.setdefault('data',{})['phase']='finished'

    if outcome == 'push':
        if reserved_total:
            move_coins(wallets, player, reserved_total)
        g['reserved']=False;g['blackjack_reserved']=0
        g['result']=g['data'].get('notice') or 'Push.'
        if reserved_total and 'refund' not in g['result'].casefold():g['result']+=' Stake refunded.'
        g['stats_net']={uid:0.0};sync_stats_from_history(state)
        return

    if outcome in ('win','blackjack'):
        g['winners']=[uid]
        if stake:
            profit = stake * (1.5 if outcome=='blackjack' else 1.0)
            total_return = stake + profit
            move_coins(wallets,player,total_return)
            g['paid'][uid]=round(profit,3)
            g['result']=g['data'].get('notice') or ('Natural Blackjack!' if outcome=='blackjack' else 'Blackjack win!')
            g['result']+=f' Stake returned + {profit:g} coin profit.'
        else:
            reward_amount=3 if outcome=='blackjack' else 2
            g['paid'][uid]=reward(state,wallets,g,uid,reward_amount,now)
            g['result']=g['data'].get('notice') or ('Natural Blackjack!' if outcome=='blackjack' else 'Blackjack win!')
        g['reserved']=False;g['blackjack_reserved']=0
        g['stats_net']={uid:float(g['paid'].get(uid,0) or 0)};sync_stats_from_history(state)
        return

    # Loss / timeout / forfeit: all reserved Blackjack bets are lost.
    lost=float(reserved_total or stake or 0)
    g['reserved']=False;g['blackjack_reserved']=0
    g['result']=g['data'].get('notice') or 'Dealer wins. No coin reward.'
    g['stats_net']={uid:-lost};sync_stats_from_history(state)


def _finish_blackjack_round(state, wallets, g, now):
    """Settle every hand after Hit/Stand/Double/Split play is complete."""
    if g.get('status') == 'finished':return
    player=g['players'][0];uid=player['id'];stake=float(g.get('stake',0) or 0)
    results=blackjack.outcomes(g['data'])
    g['status']='finished';g['ended']=now;g['winners']=[];g['paid']={};g['stats_void']=False;g['data']['phase']='finished'
    labels=[]
    for idx,r in enumerate(results,1):
        word={'win':'WIN','loss':'LOSS','push':'PUSH'}[r['outcome']]
        labels.append(f'Hand {idx}: {word} ({r["value"]} vs dealer {r["dealer"]})')

    if stake:
        total_bet=sum(stake*r['bet_mult'] for r in results)
        total_return=0.0
        for r in results:
            bet=stake*r['bet_mult']
            if r['outcome']=='win':total_return+=bet*2
            elif r['outcome']=='push':total_return+=bet
        if total_return:move_coins(wallets,player,total_return)
        net=round(total_return-total_bet,3)
        g['stats_net']={uid:net}
        if net>0:
            g['winners']=[uid];g['paid'][uid]=net
        g['result']=' • '.join(labels)+f'. Net: {net:+g} coins.'
    else:
        g['stats_net']={uid:0.0}
        any_win=any(r['outcome']=='win' for r in results)
        all_push=all(r['outcome']=='push' for r in results)
        if any_win:
            g['winners']=[uid];g['paid'][uid]=reward(state,wallets,g,uid,2,now)
            g['result']=' • '.join(labels)+'. Free-table win: +2 coins total.'
        elif all_push:
            g['result']=' • '.join(labels)+'. Push.'
        else:
            g['result']=' • '.join(labels)+'. No coin reward.'
    g['reserved']=False;g['blackjack_reserved']=0
    if not stake:g['stats_net']={uid:float(g['paid'].get(uid,0) or 0)}
    sync_stats_from_history(state)


def _replay_blackjack(state,wallets,g,uid,name,now,rng,new_stake=None):
    if g.get('kind')!='blackjack' or g.get('status')!='finished':raise ValueError('This Blackjack table is not ready for a replay.')
    players=[p['id'] for p in g['players']]
    if uid not in players:raise ValueError('Only the player from this Blackjack table can replay it.')
    if any(x is not g and x.get('status')!='finished' and uid not in x.get('left',[]) and any(p['id']==uid for p in x['players']) for x in state['games'].values()):
        raise ValueError('Finish or leave your current minigame first.')
    if new_stake is None:
        stake=float(g.get('stake',0) or 0)
    else:
        try:stake=float(new_stake)
        except (TypeError,ValueError):raise ValueError(f'Stake must be a whole number from 0 to {MAX_STAKE}.')
        if stake<0 or stake>MAX_STAKE or int(stake)!=stake:raise ValueError(f'Stake must be a whole number from 0 to {MAX_STAKE}.')
        g['stake']=int(stake)
    player=g['players'][0]
    if stake:
        move_coins(wallets,player,-stake)
        g['reserved']=True;g['blackjack_reserved']=stake
    else:
        g['reserved']=False;g['blackjack_reserved']=0
    g['status']='playing';g['started']=now;g['ended']=None;g['deadline']=now+TURN_SECONDS;g['expires']=now+2400
    g['result']='';g['paid']={};g['winners']=[];g['left']=[];g.pop('stats_net',None);g['stats_void']=False
    g['data']=blackjack.new(rng);g['data']['phase_rev']=g['rev']+1
    player_natural=blackjack.is_blackjack(g['data']['player']);dealer_natural=blackjack.is_blackjack(g['data']['dealer'])
    if player_natural or dealer_natural:
        if player_natural and dealer_natural:
            g['data']['notice']='Both player and dealer have Blackjack.';_finish_blackjack(state,wallets,g,now,'push')
        elif player_natural:
            g['data']['notice']='Natural Blackjack!';_finish_blackjack(state,wallets,g,now,'blackjack')
        else:
            g['data']['notice']='Dealer has a natural Blackjack.';_finish_blackjack(state,wallets,g,now,'loss')


def lose_mines(state,wallets,g,now,hitter=None):
    # Minefield is free entry. In co-op, one mine ends the shared board for
    # everyone, while a solo run keeps the same behavior as before.
    g['reserved']=False
    if hitter and len([p for p in g.get('players',[]) if p['id'] not in g.get('left',[])]) > 1:
        reason=f'{hitter} hit a mine. The team run is over. No coin reward.'
    else:
        reason='A mine was hit. No coin reward.'
    finish(state,wallets,g,[],now,reason)




# ---- New replayable Minigames -------------------------------------------------

# ---- Logic deduction games ----------------------------------------------------


def codebreaker_score(secret, guess):
    """Return (exact-position, present-but-wrong-position) with duplicate digits handled once."""
    secret=tuple(int(x) for x in secret);guess=tuple(int(x) for x in guess)
    if len(secret)!=len(guess):raise ValueError('Code and clue row must have the same length.')
    exact=0;left_secret=[0]*10;left_guess=[0]*10
    for a,b in zip(secret,guess):
        if a==b:exact+=1
        else:left_secret[a]+=1;left_guess[b]+=1
    wrong=sum(min(left_secret[i],left_guess[i]) for i in range(10))
    return exact,wrong


CODEBREAKER_MODES={'easy':3,'normal':4,'hard':5}
CODEBREAKER_LABELS={'easy':'Easy · 3 digits','normal':'Normal · 4 digits','hard':'Hard · 5 digits'}


def codebreaker_normalize_mode(mode):
    value=str(mode or 'easy').strip().casefold()
    return value if value in CODEBREAKER_MODES else 'easy'


def codebreaker_new(rng, mode='easy'):
    """Build a uniquely solvable transcript Codebreaker for 3, 4 or 5 digits."""
    from itertools import product
    mode=codebreaker_normalize_mode(mode);length=CODEBREAKER_MODES[mode]
    universe=list(product(range(10),repeat=length))
    secret=tuple(rng.randrange(10) for _ in range(length))
    candidates=list(universe);clues=[];used=set()
    minimum_rows={'easy':3,'normal':4,'hard':5}[mode]
    maximum_rows={'easy':7,'normal':8,'hard':9}[mode]

    # Estimate useful transcript rows against a bounded sample, then perform one
    # full candidate filter. This keeps the 100,000-code Hard mode fast enough
    # for Discord while still verifying that exactly one solution remains.
    for _ in range(maximum_rows):
        if len(candidates)<=1 and len(clues)>=minimum_rows:break
        sample=candidates if len(candidates)<=700 else rng.sample(candidates,700)
        probes=[]
        if candidates:
            probes.extend(rng.choice(candidates) for _ in range(min(16,len(candidates))))
        probes.extend(tuple(rng.randrange(10) for _ in range(length)) for _ in range(24))
        probes.extend(tuple([digit]*length) for digit in rng.sample(range(10),4))
        best=None
        for guess in probes:
            if guess in used or guess==secret:continue
            target=codebreaker_score(secret,guess)
            same=sum(1 for candidate in sample if codebreaker_score(candidate,guess)==target)
            key=(same,-sum(target))
            if best is None or key<best[0]:best=(key,guess,target)
        if best is None:break
        _,guess,target=best;used.add(guess)
        candidates=[candidate for candidate in candidates if codebreaker_score(candidate,guess)==target]
        clues.append({'guess':''.join(map(str,guess)),'exact':target[0],'wrong':target[1]})

    # Defensive candidate-vs-secret probes finish any rare stubborn transcript.
    while len(candidates)>1 and len(clues)<12:
        guess=next((candidate for candidate in candidates if candidate!=secret and candidate not in used),None)
        if guess is None:break
        target=codebreaker_score(secret,guess);used.add(guess)
        candidates=[candidate for candidate in candidates if codebreaker_score(candidate,guess)==target]
        clues.append({'guess':''.join(map(str,guess)),'exact':target[0],'wrong':target[1]})

    if len(candidates)!=1 or candidates[0]!=secret:
        raise ValueError('Could not generate a uniquely solvable Codebreaker transcript.')

    while len(clues)<minimum_rows:
        guess=next((candidate for candidate in universe if candidate not in used and candidate!=secret),None)
        if guess is None:break
        target=codebreaker_score(secret,guess);used.add(guess)
        clues.append({'guess':''.join(map(str,guess)),'exact':target[0],'wrong':target[1]})

    return {'mode':mode,'length':length,'answer':''.join(map(str,secret)),'clues':clues,
            'notice':f'{CODEBREAKER_LABELS[mode]}. All clue rows are visible. Deduce the one code that fits every score.',
            'attempts':0}


def codebreaker_answer_matches(data,value):
    guess=re.sub(r'\D','',str(value or ''))
    return len(guess)==int(data.get('length',3) or 3) and guess==str(data.get('answer',''))


CLUEST_NAMES=(
    'Alex','Bobby','Casey','Drew','Emery','Frank','George','Harper','Indigo','Jordan','Kai','Louis',
    'Morgan','Nico','Ollie','Parker','Quincy','Riley','Sam','Taylor','Uma','Victor','Will','Xena',
    'Yara','Zane','Avery','Blake','Cameron','Devon','Ellis','Flynn','Greer','Hayden','Jules','Kendall',
)
CLUEST_JOBS=('firefighter','teacher','astronaut','scientist','chef','engineer')
CLUEST_ROWS=4;CLUEST_COLS=3;CLUEST_SIZE=CLUEST_ROWS*CLUEST_COLS;CLUEST_CRIMINALS=4


def cluest_label(index):
    r,c=divmod(int(index),CLUEST_COLS);return chr(65+c)+str(r+1)


def _cluest_row(r):return [r*CLUEST_COLS+c for c in range(CLUEST_COLS)]
def _cluest_col(c):return [r*CLUEST_COLS+c for r in range(CLUEST_ROWS)]
def _cluest_neighbors(index):
    r,c=divmod(index,CLUEST_COLS);out=[]
    for dr in (-1,0,1):
        for dc in (-1,0,1):
            if not (dr or dc):continue
            rr,cc=r+dr,c+dc
            if 0<=rr<CLUEST_ROWS and 0<=cc<CLUEST_COLS:out.append(rr*CLUEST_COLS+cc)
    return out


def _cluest_eval(clue,criminals):
    criminals=set(criminals);kind=clue['type']
    if kind=='direct':return (int(clue['idx']) in criminals)==bool(clue['criminal'])
    if kind=='count':return sum(int(i) in criminals for i in clue['indices'])==int(clue['count'])
    if kind=='parity':return sum(int(i) in criminals for i in clue['indices'])%2==int(clue['parity'])
    if kind=='compare':
        left=sum(int(i) in criminals for i in clue['a']);right=sum(int(i) in criminals for i in clue['b']);op=clue['op']
        return left>right if op=='>' else left<right if op=='<' else left==right
    raise ValueError('Unknown Cluest clue type.')


def _cluest_worlds():
    from itertools import combinations
    return [set(x) for x in combinations(range(CLUEST_SIZE),CLUEST_CRIMINALS)]


def _cluest_candidates(data):
    known={int(k):bool(v) for k,v in data.get('known',{}).items()}
    clues=[data['clues'][int(i)] for i in data.get('revealed',[])]
    out=[]
    for world in _cluest_worlds():
        if any(((idx in world)!=status) for idx,status in known.items()):continue
        if all(_cluest_eval(clue,world) for clue in clues):out.append(world)
    return out


def cluest_forced(data):
    candidates=_cluest_candidates(data);known={int(k) for k in data.get('known',{})}
    result={}
    for idx in range(CLUEST_SIZE):
        if idx in known:continue
        values={idx in world for world in candidates}
        if len(values)==1:result[idx]=next(iter(values))
    return result,candidates


def _cluest_clue_pool(hidden,jobs):
    hidden=set(hidden);pool=[]
    def count_clue(indices,text):
        indices=list(indices);n=sum(i in hidden for i in indices)
        pool.append({'type':'count','indices':indices,'count':n,'text':text.format(n=n)})
    for idx in range(CLUEST_SIZE):
        status=idx in hidden
        pool.append({'type':'direct','idx':idx,'criminal':status,
                     'text':f'{cluest_label(idx)} is definitely {"criminal" if status else "innocent"}.'})
    for r in range(CLUEST_ROWS):count_clue(_cluest_row(r),f'Row {r+1} contains exactly {{n}} criminals.')
    for c in range(CLUEST_COLS):count_clue(_cluest_col(c),f'Column {chr(65+c)} contains exactly {{n}} criminals.')
    for job in sorted(set(jobs)):
        ids=[i for i,x in enumerate(jobs) if x==job];count_clue(ids,f'Exactly {{n}} of the {job}s are criminals.')
    for idx in range(CLUEST_SIZE):
        ids=_cluest_neighbors(idx);count_clue(ids,f'{cluest_label(idx)} has exactly {{n}} criminal neighbors.')
        n=sum(i in hidden for i in ids)
        pool.append({'type':'parity','indices':ids,'parity':n%2,
                     'text':f'{cluest_label(idx)} has an {"odd" if n%2 else "even"} number of criminal neighbors.'})
    edge=[i for i in range(CLUEST_SIZE) if divmod(i,CLUEST_COLS)[0] in (0,CLUEST_ROWS-1) or divmod(i,CLUEST_COLS)[1] in (0,CLUEST_COLS-1)]
    corners=[0,CLUEST_COLS-1,(CLUEST_ROWS-1)*CLUEST_COLS,CLUEST_SIZE-1]
    count_clue(edge,'The outer edge contains exactly {n} criminals.')
    count_clue(corners,'The four corners contain exactly {n} criminals.')
    for r in range(CLUEST_ROWS-1):
        a=_cluest_row(r);b=_cluest_row(r+1);ca=sum(i in hidden for i in a);cb=sum(i in hidden for i in b);op='=' if ca==cb else '>' if ca>cb else '<'
        phrase='more' if op=='>' else 'fewer' if op=='<' else 'the same number of'
        bridge='than' if op!='=' else 'as'
        pool.append({'type':'compare','a':a,'b':b,'op':op,
                     'text':f'Row {r+1} has {phrase} criminals {bridge} row {r+2}.'})
    return pool


def _cluest_closure(data):
    """Simulate perfect play; used only while generating a guaranteed deduction chain."""
    work=copy.deepcopy(data)
    while True:
        forced,candidates=cluest_forced(work)
        if not candidates:return False
        if not forced:return len(work['known'])==CLUEST_SIZE
        for idx,status in forced.items():
            work['known'][str(idx)]=bool(status)
            if idx not in work['revealed']:work['revealed'].append(idx)
        if len(work['known'])==CLUEST_SIZE:return True


def cluest_new(rng):
    names=rng.sample(list(CLUEST_NAMES),CLUEST_SIZE)
    # Keep each occupation represented while still varying the board every game.
    jobs=[CLUEST_JOBS[(i+rng.randrange(len(CLUEST_JOBS)))%len(CLUEST_JOBS)] for i in range(CLUEST_SIZE)]
    hidden=set(rng.sample(range(CLUEST_SIZE),CLUEST_CRIMINALS));pool=_cluest_clue_pool(hidden,jobs)
    non_direct=[c for c in pool if c['type']!='direct'];direct=[c for c in pool if c['type']=='direct']
    for _ in range(800):
        # Counts/comparisons are deliberately more common than direct statements.
        clues=[copy.deepcopy(rng.choice(non_direct if rng.random()<0.82 else direct)) for _ in range(CLUEST_SIZE)]
        faceup=sorted(rng.sample(range(CLUEST_SIZE),3))
        known={str(i):(i in hidden) for i in faceup}
        data={'names':names,'jobs':jobs,'criminal_total':CLUEST_CRIMINALS,'clues':clues,'revealed':faceup[:],
              'known':known,'notice':'Start from the three face-up suspects. Only make a call when the current evidence proves it.',
              'hint_stage':0}
        if _cluest_closure(data):return data
    # Guaranteed fallback: a direct-information chain, still using the exact same no-guess validation.
    order=list(range(CLUEST_SIZE));rng.shuffle(order);clues=[]
    for i in range(CLUEST_SIZE):
        target=order[(order.index(i)+1)%CLUEST_SIZE];status=target in hidden
        clues.append({'type':'direct','idx':target,'criminal':status,
                      'text':f'{cluest_label(target)} is definitely {"criminal" if status else "innocent"}.'})
    faceup=sorted(order[:3])
    return {'names':names,'jobs':jobs,'criminal_total':CLUEST_CRIMINALS,'clues':clues,'revealed':faceup,
            'known':{str(i):(i in hidden) for i in faceup},'notice':'Start from the face-up suspects and follow the evidence.',
            'hint_stage':0}


def cluest_visible_text(g):
    if g.get('kind')!='cluest' or g.get('status')!='playing':raise ValueError('Cluest evidence is available only during an active case.')
    d=g['data'];rows=[]
    for r in range(CLUEST_ROWS):
        row=[]
        for c in range(CLUEST_COLS):
            idx=r*CLUEST_COLS+c;key=str(idx);status=d.get('known',{}).get(key)
            mark='🟥 CRIMINAL' if status is True else '🟩 INNOCENT' if status is False else '⬜ UNKNOWN'
            row.append(f'**{cluest_label(idx)} · {d["names"][idx]}** — {d["jobs"][idx]} — {mark}')
        rows.append('\n'.join(row))
    clues=[]
    for idx in d.get('revealed',[]):clues.append(f'**{cluest_label(idx)} · {d["names"][idx]} says:** {d["clues"][idx]["text"]}')
    return ('**CLUEST BOARD**\nExactly **%d criminals** are on the board.\n\n%s\n\n**REVEALED CLUES**\n%s' %
            (d.get('criminal_total',CLUEST_CRIMINALS),'\n\n'.join(rows),'\n'.join(clues) or 'No clues revealed.'))


def cluest_parse_call(value):
    parts=str(value or '').strip().replace(':',' ').replace('-',' ').split()
    if len(parts)<2:raise ValueError('Use a grid label and status, for example: B3 criminal or A1 innocent.')
    label=parts[0].upper();status=' '.join(parts[1:]).casefold()
    match=re.fullmatch(r'([A-C])([1-4])',label)
    if not match:raise ValueError('Grid label must be A1 through C4.')
    idx=(int(match.group(2))-1)*CLUEST_COLS+(ord(match.group(1))-65)
    if status in ('criminal','guilty','red','c'):criminal=True
    elif status in ('innocent','green','i','safe'):criminal=False
    else:raise ValueError('Status must be innocent or criminal.')
    return idx,criminal


def cluest_call(data,value):
    idx,claimed=cluest_parse_call(value);key=str(idx)
    if key in data.get('known',{}):raise ValueError(f'{cluest_label(idx)} is already revealed.')
    forced,candidates=cluest_forced(data)
    if not candidates:raise ValueError('The current Cluest evidence became inconsistent.')
    if idx not in forced:
        data['notice']=f'Not enough evidence yet to prove {cluest_label(idx)}. Nothing was lost.'
        return 'unproven'
    actual=forced[idx]
    if claimed!=actual:
        data['notice']=f'The evidence proves {cluest_label(idx)} is {"criminal" if actual else "innocent"}, not {"criminal" if claimed else "innocent"}. Nothing was lost.'
        return 'opposite'
    data.setdefault('known',{})[key]=bool(actual)
    if idx not in data.setdefault('revealed',[]):data['revealed'].append(idx)
    data['notice']=f'{cluest_label(idx)} confirmed {"CRIMINAL" if actual else "INNOCENT"}. Their clue is now face up.'
    data['hint_stage']=0
    return 'solved' if len(data['known'])==CLUEST_SIZE else 'revealed'


def cluest_hint(data):
    forced,candidates=cluest_forced(data)
    if not candidates:return 'The visible evidence is inconsistent.'
    if not forced:return 'No single suspect is forced yet; reread the revealed count clues together.'
    idx=sorted(forced)[0]
    if int(data.get('hint_stage',0))%2==0:
        data['hint_stage']=1
        return f'Hint 1/2: the current evidence is enough to resolve **{cluest_label(idx)}**.'
    data['hint_stage']=0
    return f'Hint 2/2: **{cluest_label(idx)}** must be **{"CRIMINAL" if forced[idx] else "INNOCENT"}**.'


LINGO_WORDS=tuple(dict.fromkeys(w for w in """
ABOUT ABOVE ABUSE ACTOR ACUTE ADMIT ADOPT ADULT AFTER AGAIN AGENT AGREE AHEAD ALARM ALBUM ALERT ALIEN ALIGN ALIKE ALIVE ALLOW ALONE ALONG ALTER AMONG ANGER ANGLE ANGRY APART APPLE APPLY ARGUE ARISE ARRAY ASIDE ASSET AUDIO AUDIT AVOID AWARD AWARE BADLY BAKER BASES BASIC BEACH BEGAN BEGIN BEGUN BEING BELOW BENCH BILLY BIRTH BLACK BLAME BLIND BLOCK BLOOD BOARD BOOST BOOTH BOUND BRAIN BRAND BREAD BREAK BREED BRIEF BRING BROAD BROKE BROWN BUILD BUILT BUYER CABLE CALIF CARRY CATCH CAUSE CHAIN CHAIR CHART CHASE CHEAP CHECK CHEST CHIEF CHILD CHINA CHOSE CIVIL CLAIM CLASS CLEAN CLEAR CLICK CLOCK CLOSE COACH COAST COULD COUNT COURT COVER CRAFT CRASH CREAM CRIME CROSS CROWD CROWN CURVE CYCLE DAILY DANCE DATED DEALT DEATH DEBUT DELAY DEPTH DOING DOUBT DOZEN DRAFT DRAMA DRAWN DREAM DRESS DRILL DRINK DRIVE DROVE DYING EAGER EARLY EARTH EIGHT ELITE EMPTY ENEMY ENJOY ENTER ENTRY EQUAL ERROR EVENT EVERY EXACT EXIST EXTRA FAITH FALSE FAULT FIBER FIELD FIFTH FIFTY FIGHT FINAL FIRST FIXED FLASH FLEET FLOOR FLUID FOCUS FORCE FORTH FORTY FORUM FOUND FRAME FRANK FRAUD FRESH FRONT FRUIT FULLY FUNNY GIANT GIVEN GLASS GLOBE GOING GRACE GRADE GRAND GRANT GRASS GREAT GREEN GROSS GROUP GROWN GUARD GUESS GUEST GUIDE HAPPY HARRY HEART HEAVY HENCE HENRY HORSE HOTEL HOUSE HUMAN IDEAL IMAGE INDEX INNER INPUT ISSUE JAPAN JIMMY JOINT JONES JUDGE KNOWN LABEL LARGE LASER LATER LAUGH LAYER LEARN LEAST LEAVE LEGAL LEVEL LEWIS LIGHT LIMIT LINKS LIVES LOCAL LOGIC LOOSE LOWER LUCKY LUNCH MAGIC MAJOR MAKER MARCH MATCH MAYBE MAYOR MEANT MEDIA METAL MIGHT MINOR MINUS MIXED MODEL MONEY MONTH MORAL MOTOR MOUNT MOUSE MOUTH MOVIE MUSIC NEEDS NEVER NEWLY NIGHT NOISE NORTH NOTED NOVEL NURSE OCCUR OCEAN OFFER OFTEN ORDER OTHER OUGHT PAINT PANEL PAPER PARTY PEACE PETER PHASE PHONE PHOTO PIECE PILOT PITCH PLACE PLAIN PLANE PLANT PLATE POINT POUND POWER PRESS PRICE PRIDE PRIME PRINT PRIOR PRIZE PROOF PROUD PROVE QUEEN QUICK QUIET QUITE RADIO RAISE RANGE RAPID RATIO REACH READY REFER RIGHT RIVAL RIVER ROBIN ROGER ROMAN ROUGH ROUND ROUTE ROYAL RURAL SCALE SCENE SCOPE SCORE SENSE SERVE SEVEN SHALL SHAPE SHARE SHARP SHEET SHELF SHELL SHIFT SHIRT SHOCK SHOOT SHORT SHOWN SIGHT SINCE SIXTH SIXTY SIZED SKILL SLEEP SLIDE SMALL SMART SMILE SMITH SMOKE SOLID SOLVE SORRY SOUND SOUTH SPACE SPARE SPEAK SPEED SPEND SPENT SPLIT SPOKE SPORT STAFF STAGE STAKE STAND START STATE STEAM STEEL STICK STILL STOCK STONE STOOD STORE STORM STORY STRIP STUCK STUDY STUFF STYLE SUGAR SUITE SUPER SWEET TABLE TAKEN TASTE TAXES TEACH TEETH TERRY TEXAS THANK THEFT THEIR THEME THERE THESE THICK THING THINK THIRD THOSE THREE THREW THROW TIGHT TIMES TIRED TITLE TODAY TOPIC TOTAL TOUCH TOUGH TOWER TRACK TRADE TRAIN TREAT TREND TRIAL TRIED TRUCK TRULY TRUST TRUTH TWICE UNDER UNION UNITY UNTIL UPPER UPSET URBAN USAGE USUAL VALID VALUE VIDEO VISIT VITAL VOICE WASTE WATCH WATER WHEEL WHERE WHICH WHILE WHITE WHOLE WHOSE WOMAN WOMEN WORLD WORRY WORSE WORST WORTH WOULD WRITE WRONG WROTE YIELD YOUNG YOUTH
""".split() if len(w)==5 and w.isalpha()))


def lingo_new(state, rng):
    recent=list(state.get('lingo_recent',[]))[-120:]
    choices=[w for w in LINGO_WORDS if w not in recent] or list(LINGO_WORDS)
    word=rng.choice(choices)
    state['lingo_recent']=(recent+[word])[-120:]
    return {'target':word,'guesses':[],'notice':'Guess the hidden 5-letter word. You have 6 attempts.'}


def lingo_feedback(target, guess):
    target=str(target).upper();guess=str(guess).upper();marks=['X']*5;counts={}
    for i,(a,b) in enumerate(zip(target,guess)):
        if a==b:marks[i]='G'
        else:counts[a]=counts.get(a,0)+1
    for i,ch in enumerate(guess):
        if marks[i]=='G':continue
        if counts.get(ch,0)>0:
            marks[i]='Y';counts[ch]-=1
    return ''.join(marks)


def lingo_submit(data, value):
    guess=re.sub(r'[^A-Za-z]','',str(value or '')).upper()
    if len(guess)!=5:raise ValueError('Enter exactly five letters.')
    if len(data.get('guesses',[]))>=6:raise ValueError('No guesses remain.')
    marks=lingo_feedback(data['target'],guess)
    data['guesses'].append({'word':guess,'marks':marks})
    if guess==data['target']:
        data['notice']=f'Solved in {len(data["guesses"])} guesses!';return 'solved'
    if len(data['guesses'])>=6:
        data['notice']=f'No guesses left. The word was {data["target"]}.';return 'failed'
    data['notice']=f'{6-len(data["guesses"])} guesses remaining.';return 'continue'


def _bomb_module_wires(rng):
    colors=['RED','BLUE','YELLOW','WHITE','BLACK','GREEN']
    wires=[rng.choice(colors) for _ in range(6)];serial=rng.randint(1000,9999);even=serial%2==0
    if even:
        idx=next((i for i,c in enumerate(wires) if c=='BLUE'),None)
        answer=(idx+1) if idx is not None else 6
    else:
        idx=next((i for i in range(5,-1,-1) if wires[i]=='RED'),None)
        answer=(idx+1) if idx is not None else 1
    return {'title':'Wire Matrix','device':f'Serial {serial}. Wires top→bottom: '+', '.join(f'{i+1}:{c}' for i,c in enumerate(wires)),
            'manuals':['Check the LAST serial digit. If it is EVEN: cut the first BLUE wire; if there is no BLUE wire, cut wire 6.',
                       'If the last serial digit is ODD: cut the last RED wire; if there is no RED wire, cut wire 1.'],
            'answer':str(answer),'hint':'Serial parity chooses which wire rule applies.'}


def _bomb_module_xor(rng):
    signal=rng.randint(0,255);mask=rng.randint(1,255);answer=signal^mask
    return {'title':'XOR Pulse Decoder','device':f'Incoming pulse: {signal:08b}. The panel asks for a decimal unlock value.',
            'manuals':[f'Decoder mask: {mask:08b}. XOR the incoming pulse with this mask bit-by-bit.',
                       'After XOR, convert the resulting 8-bit binary number to DECIMAL and enter that decimal number.'],
            'answer':str(answer),'hint':'Same bits → 0, different bits → 1; then convert binary to decimal.'}


def _bomb_module_button(rng):
    color=rng.choice(['RED','BLUE','WHITE','YELLOW']);label=rng.choice(['ARM','HOLD','VENT','SAFE']);batteries=rng.randint(1,5)
    if color=='RED' and label=='HOLD':answer='HOLD'
    elif batteries>=3 and label=='ARM':answer='PRESS'
    elif color=='BLUE':answer='HOLD'
    else:answer='PRESS'
    return {'title':'Command Button','device':f'A {color} button reads {label}. Battery modules: {batteries}.',
            'manuals':['Rule priority: (1) RED + HOLD → HOLD. (2) 3+ batteries + ARM → PRESS.',
                       '(3) Otherwise BLUE → HOLD. (4) If none of the earlier rules matched → PRESS. Enter PRESS or HOLD.'],
            'answer':answer,'hint':'Apply the rules from top to bottom and stop at the first match.'}


def _bomb_module_dial(rng):
    start=rng.randint(0,11);steps=rng.randint(5,27);gear=rng.randint(2,5);answer=(start+steps*gear)%12
    return {'title':'Rotary Synchronizer','device':f'Dial starts at {start}. Motor receives {steps} pulses. Gear ratio display: ×{gear}. Positions are 0–11.',
            'manuals':['Multiply the pulse count by the gear ratio before moving the dial.',
                       'Final dial = (start + adjusted pulses) mod 12. Enter a number 0–11.'],
            'answer':str(answer),'hint':'Compute start + pulses×gear, then keep the remainder after division by 12.'}


def _bomb_module_keypad(rng):
    symbols=rng.sample(['△','○','□','◇','☆','+','%','@'],4);digits=rng.sample(range(1,10),4);mapping=dict(zip(symbols,digits));order=rng.sample(symbols,4)
    return {'title':'Glyph Keypad','device':'Keypad flashes: '+' → '.join(order),
            'manuals':['Glyph map: '+', '.join(f'{s}={mapping[s]}' for s in symbols)+'.',
                       'Translate the four flashing glyphs in EXACT display order and concatenate the four digits.'],
            'answer':''.join(str(mapping[s]) for s in order),'hint':'Use the glyph map, preserving the display order.'}


def _bomb_module_checksum(rng):
    digits=[rng.randint(0,9) for _ in range(6)];weights=rng.choice([(1,2,3,4,5,6),(7,5,3,1,2,4),(2,3,5,7,11,13)]);mod=rng.choice([11,17,23]);answer=sum(a*b for a,b in zip(digits,weights))%mod
    return {'title':'Packet Checksum','device':'Packet digits: '+' '.join(map(str,digits)),
            'manuals':['Multiply the six digits left→right by weights: '+' '.join(map(str,weights))+'.',f'Add the products, then take the remainder modulo {mod}. Enter only the remainder.'],
            'answer':str(answer),'hint':'Weighted sum first, modulo last.'}


def _bomb_module_route(rng):
    dirs={'N':(0,-1),'E':(1,0),'S':(0,1),'W':(-1,0)};moves=[rng.choice('NESW') for _ in range(8)];x=y=0
    for m in moves:dx,dy=dirs[m];x+=dx;y+=dy
    answer=f'{x},{y}'
    return {'title':'Drone Route','device':'Drone route: '+' '.join(moves)+'. Start coordinate is (0,0).',
            'manuals':['Coordinate convention: EAST adds +1 to x; WEST adds -1 to x.',
                       'SOUTH adds +1 to y; NORTH adds -1 to y. Enter final coordinate as x,y.'],
            'answer':answer,'hint':'Track x and y separately through all eight moves.'}


def _bomb_module_frequency(rng):
    vals=rng.sample(range(20,100),5);indicator=rng.choice(['AMBER','CYAN']);sortedv=sorted(vals);answer=sortedv[2] if indicator=='AMBER' else max(vals)-min(vals)
    return {'title':'Frequency Bank','device':f'Indicator: {indicator}. Channels: '+', '.join(map(str,vals))+'.',
            'manuals':['If the indicator is AMBER, sort the five channels and enter the MEDIAN (middle) value.',
                       'If the indicator is CYAN, enter the RANGE: largest channel minus smallest channel.'],
            'answer':str(answer),'hint':'Amber = median; Cyan = max−min.'}

BOMB_BUILDERS=(_bomb_module_wires,_bomb_module_xor,_bomb_module_button,_bomb_module_dial,_bomb_module_keypad,_bomb_module_checksum,_bomb_module_route,_bomb_module_frequency)
BOMB_APPROACHABLE=(_bomb_module_wires,_bomb_module_button,_bomb_module_dial,_bomb_module_keypad)


def bomb_new(rng):
    # Slight ease-up: every bomb contains at least one of the clearer modules,
    # while the other two are still drawn from the full bank. Rules/answers stay intact.
    first=rng.choice(BOMB_APPROACHABLE)
    remaining=[builder for builder in BOMB_BUILDERS if builder is not first]
    builders=[first]+rng.sample(remaining,2);rng.shuffle(builders)
    return {'modules':[fn(rng) for fn in builders],'module':0,'strikes':0,'attempts':{},'active':[],
            'notice':'Bomb armed. Share your private information and clear all three modules.'}


def _simple_answer(value):
    return re.sub(r'\s+','',str(value or '').strip().upper()).replace('(','').replace(')','')


def bomb_answer_matches(module, value):
    return _simple_answer(value)==_simple_answer(module.get('answer',''))


def bomb_clue_text(g, uid):
    if g.get('kind')!='bomb' or g.get('status')!='playing':raise ValueError('Bomb information is only available during an active defusal.')
    current=[p['id'] for p in g.get('players',[]) if p['id'] not in g.get('left',[])]
    if uid not in current:raise ValueError('This information is only for current defusers.')
    d=g['data'];module=d['modules'][d['module']];operator=current[d['module']%len(current)]
    if uid==operator:
        return f'**You are the DEVICE OPERATOR — Module {d["module"]+1}/3: {module["title"]}**\n\n{module["device"]}\n\nDo not guess from the device alone. Ask your teammates for the manual rules.'
    manuals=list(module.get('manuals',[]));helpers=[x for x in current if x!=operator]
    seat=helpers.index(uid)
    if len(helpers)==1:visible=manuals
    else:visible=[line for j,line in enumerate(manuals) if j%len(helpers)==seat] or [manuals[seat%len(manuals)]]
    return f'**You are a MANUAL EXPERT — Module {d["module"]+1}/3: {module["title"]}**\n\n'+'\n\n'.join(visible)+'\n\nTell the device operator what rule to apply. Do not post the final code publicly.'


MATH_DURATION_MODES={60:'1 Minute',120:'2 Minutes'}


def _math_problem(rng, score):
    tier=min(5,max(0,int(score)//4))
    if tier==0:
        if rng.random()<0.5:
            a=rng.randint(12,99);b=rng.randint(8,90);return {'q':f'{a} + {b} = ?','a':str(a+b)}
        a=rng.randint(25,130);b=rng.randint(5,a-1);return {'q':f'{a} − {b} = ?','a':str(a-b)}
    if tier==1:
        a=rng.randint(11,39);b=rng.randint(6,29);return {'q':f'{a} × {b} = ?','a':str(a*b)}
    if tier==2:
        b=rng.randint(4,19);ans=rng.randint(8,45);a=b*ans
        if rng.random()<0.5:return {'q':f'{a} ÷ {b} = ?','a':str(ans)}
        c=rng.randint(2,9);return {'q':f'({a} ÷ {b}) × {c} = ?','a':str(ans*c)}
    if tier==3:
        percent=rng.choice([10,20,25,40,50,75]);base=rng.choice([40,60,80,100,120,160,200,240]);ans=base*percent//100
        return {'q':f'{percent}% of {base} = ?','a':str(ans)}
    if tier==4:
        x=rng.randint(4,28);m=rng.randint(2,9);b=rng.randint(3,30);rhs=m*x+b
        return {'q':f'Solve x: {m}x + {b} = {rhs}','a':str(x)}
    if rng.random()<0.5:
        n=rng.randint(12,35);return {'q':f'{n}² − {n} = ?','a':str(n*n-n)}
    a=rng.randint(12,40);b=rng.randint(8,30);c=rng.randint(2,9);return {'q':f'{a} × {b} + {c}² = ?','a':str(a*b+c*c)}


def math_new(duration, rng):
    duration=int(duration)
    if duration not in MATH_DURATION_MODES:raise ValueError('Math Rush must be 1 or 2 minutes.')
    return {'duration':duration,'score':0,'misses':0,'attempts':0,'current':_math_problem(rng,0),'last':'','notice':'Type each numeric answer directly in the Minigames chat.'}


def _numeric_equal(a,b):
    def parse(v):
        t=str(v or '').strip().replace(',','.').replace(' ','')
        try:return Fraction(t)
        except Exception:return None
    x=parse(a);y=parse(b);return x is not None and y is not None and x==y


def _detective_minutes(value):
    hour, minute = str(value).split(':', 1)
    return int(hour) * 60 + int(minute)


def detective_new(rng):
    """Build a denser eight-suspect case that requires several clues together."""
    scenarios=[
        ('The Museum Blackout','a prototype vanished during a 90-second blackout'),
        ('The Observatory Sabotage','the tracking array was deliberately disabled'),
        ('The Midnight Archive','a sealed historical file disappeared'),
        ('The Lab Intrusion','a restricted sample was removed from cold storage'),
        ('The Gallery Switch','an original artwork was replaced by a replica'),
        ('The Train Vault','a locked courier case was opened between stations'),
    ]
    first=['Alex','Blair','Casey','Drew','Emery','Flynn','Gray','Harper','Jules','Morgan','Parker','Quinn','Reese','Riley','Rowan','Sage','Taylor','Winter']
    last=['Vale','Stone','Cross','Reed','Blake','Hayes','Frost','Lane','Shaw','Brooks','Wells','Knight']
    # Every profile attribute repeats. A single exact profile fact can therefore
    # never identify one person by itself.
    pools={
        'time':['18:10','18:25','18:40','18:55'],
        'tool':['maintenance key','magnetic card','fiber cable','glass cutter'],
        'shoe':[39,41,43,45],
        'zone':['North Wing','Archive','Service Hall','Roof Access'],
        'badge':['amber','blue','green','red'],
    }
    names=[]
    while len(names)<8:
        name=rng.choice(first)+' '+rng.choice(last)
        if name not in names:names.append(name)

    # Shuffle two copies of every value until no two complete profiles match.
    for _ in range(100):
        columns={}
        for key,values in pools.items():
            column=list(values)*2;rng.shuffle(column);columns[key]=column
        profiles=[tuple(columns[key][i] for key in ('time','tool','shoe','zone','badge')) for i in range(8)]
        if len(set(profiles))==8:break
    suspects=[{
        'name':names[i],
        'time':columns['time'][i],
        'tool':columns['tool'][i],
        'shoe':columns['shoe'][i],
        'zone':columns['zone'][i],
        'badge':columns['badge'][i],
    } for i in range(8)]
    culprit=rng.randrange(8);c=suspects[culprit];all_ids=frozenset(range(8))
    clue_pool=[];seen_masks=set()

    def add(family,text,predicate):
        matches=frozenset(i for i,suspect in enumerate(suspects) if predicate(suspect))
        # Keep clues broad enough that none gives the culprit away by itself.
        if culprit not in matches or not 3<=len(matches)<=7 or matches==all_ids:return
        if matches in seen_masks:return
        seen_masks.add(matches)
        clue_pool.append({'family':family,'text':text,'matches':sorted(matches),'_mask':matches})

    pair_specs=[
        ('time','arrival time','The damaged access log narrows the offender’s arrival to **{a}** or **{b}**.'),
        ('tool','equipment','Recovered residue is consistent with either a **{a}** or a **{b}**.'),
        ('shoe','footwear','The partial footwear impression fits size **{a}** or **{b}**.'),
        ('zone','location','Motion sensors place the offender in either **{a}** or **{b}** during the critical window.'),
        ('badge','badge','The cloned access signal used an **{a}** or **{b}** badge signature.'),
    ]
    for key,family,template in pair_specs:
        current=c[key]
        for other in pools[key]:
            if other==current:continue
            selected={current,other}
            add(family,template.format(a=current,b=other),lambda suspect,k=key,v=selected:suspect[k] in v)

    exclusions=[
        ('time','arrival time','Forensic timing rules out anyone who arrived at **{value}**.'),
        ('tool','equipment','The trace analysis rules out anyone carrying a **{value}**.'),
        ('shoe','footwear','The impression definitely was **not** shoe size **{value}**.'),
        ('zone','location','The offender was **not** assigned to **{value}**.'),
        ('badge','badge','The copied credential was **not** a **{value}** badge.'),
    ]
    for key,family,template in exclusions:
        for value in pools[key]:
            if value==c[key]:continue
            add(family,template.format(value=value),lambda suspect,k=key,v=value:suspect[k]!=v)

    # Comparison clues force players to read other profiles instead of just
    # matching a highlighted value in one line.
    for reference_index,reference in enumerate(suspects):
        if reference_index==culprit:continue
        ref_minutes=_detective_minutes(reference['time'])
        culprit_minutes=_detective_minutes(c['time'])
        if culprit_minutes<=ref_minutes:
            add('timeline',f'The offender arrived **no later than {reference["name"]}**.',
                lambda suspect,m=ref_minutes:_detective_minutes(suspect['time'])<=m)
        if culprit_minutes>=ref_minutes:
            add('timeline',f'The offender arrived **no earlier than {reference["name"]}**.',
                lambda suspect,m=ref_minutes:_detective_minutes(suspect['time'])>=m)
        if c['shoe']<=reference['shoe']:
            add('comparison',f'The offender’s shoe size was **no larger than {reference["name"]}’s**.',
                lambda suspect,n=reference['shoe']:suspect['shoe']<=n)
        if c['shoe']>=reference['shoe']:
            add('comparison',f'The offender’s shoe size was **no smaller than {reference["name"]}’s**.',
                lambda suspect,n=reference['shoe']:suspect['shoe']>=n)
        if c['badge']!=reference['badge']:
            add('comparison',f'The offender did **not** use the same badge color as **{reference["name"]}**.',
                lambda suspect,b=reference['badge']:suspect['badge']!=b)
        if c['zone']!=reference['zone']:
            add('comparison',f'The offender was **not assigned to the same area as {reference["name"]}**.',
                lambda suspect,z=reference['zone']:suspect['zone']!=z)

    # Find a six-clue deduction chain. Every clue must reduce the current
    # candidate set, but the culprit may become unique only on the final clue.
    rng.shuffle(clue_pool)
    def search(current,picked,families):
        if len(picked)==6:return picked if current==frozenset({culprit}) and len(families)>=4 else None
        options=[]
        for clue in clue_pool:
            if clue in picked:continue
            new=current & clue['_mask']
            if culprit not in new or len(new)>=len(current):continue
            remaining=6-len(picked)-1
            if len(new)==1 and remaining>0:continue
            family_bonus=0 if clue['family'] not in families else 1
            # Prefer gentle reductions and new clue families; this makes the
            # case feel like a chain rather than one near-answer plus filler.
            options.append((family_bonus,-len(new),rng.random(),clue,new))
        options.sort(key=lambda item:(item[0],item[1],item[2]))
        for _,__,___,clue,new in options:
            result=search(new,picked+[clue],families|{clue['family']})
            if result:return result
        return None

    evidence=search(all_ids,[],set())
    if not evidence:
        # The attribute construction above normally produces a chain. Retry
        # cleanly rather than shipping an ambiguous case if a rare shuffle does not.
        return detective_new(rng)
    for clue in evidence:clue.pop('_mask',None)
    title,incident=rng.choice(scenarios)
    return {
        'title':title,'incident':incident,'suspects':suspects,'evidence':evidence,
        'answer':c['name'],'answer_index':culprit,'attempts_left':3,
        'notice':'Eight suspects. Six clues. Every clue still fits several people on its own — combine all of them.'
    }


def detective_case_text(g):
    if g.get('kind')!='detective' or g.get('status')!='playing':raise ValueError('The case file is only available during an active case.')
    d=g['data'];rows=[]
    for s in d['suspects']:
        rows.append(
            f'**{s["name"]}** — arrival {s["time"]} · carried {s["tool"]} · shoe {s["shoe"]} · '
            f'assigned to {s["zone"]} · badge {s.get("badge","unknown")}'
        )
    clues='\n'.join(f'{i+1}. {x.get("text",x) if isinstance(x,dict) else x}' for i,x in enumerate(d['evidence']))
    return (
        f'**{d["title"]}**\n{d["incident"].capitalize()}.\n\n**Suspect profiles**\n'+'\n'.join(rows)+
        '\n\n**Evidence**\n'+clues+
        '\n\nNo single clue is enough. Cross-reference all six clues and accuse the **one suspect left after the full intersection**.'
    )


def detective_answer_matches(data,value):
    typed=' '.join(str(value or '').strip().casefold().split())
    target=str(data['answer']).casefold()
    if typed==target:
        return True
    # First names are accepted only when they identify exactly one suspect.
    # Cases may intentionally contain two people named Harper, Alex, etc.
    first=target.split()[0]
    same_first=[s for s in data.get('suspects',[]) if str(s.get('name','')).casefold().split()[:1]==[first]]
    return typed==first and len(same_first)==1


def retire_legacy_racer_games(state, wallets, now):
    """Close/refund old Minigames Racer sessions after Racer moved to ChessBot."""
    changed=0
    for g in state.get('games',{}).values():
        if g.get('kind')!='racer' or g.get('status')=='finished':
            continue
        finish(state,wallets,g,[],now,'Puzzle Racer moved to ChessBot — old Minigames race closed.',refund=True)
        g['rev']=int(g.get('rev',0))+1
        changed+=1
    return changed


def retire_legacy_escape_games(state, wallets, now):
    """Finish/refund unfinished Escape runs created by an older engine build.

    Minigames state survives process restarts. Without this migration, an old
    Escape adventure can reappear after new code deploys and look like the new
    generator is still producing legacy rooms.
    """
    changed=0
    for g in state.get('games',{}).values():
        if g.get('kind')!='escape' or g.get('status')=='finished':
            continue
        data=g.get('data') or {}
        if data.get('escape_build')==ESCAPE_BUILD or data.get('generation')==ESCAPE_BUILD:
            continue
        finish(state,wallets,g,[],now,'Escape Room engine upgraded — start a fresh run.',refund=True)
        g['rev']=int(g.get('rev',0))+1
        changed+=1
    return changed

def create(state,wallets,gid,uid,name,kind,stake,now,mode=None):
    if kind not in KINDS:raise ValueError('Unknown game.')
    if any(g['status']!='finished' and uid not in g.get('left',[]) and any(p['id']==uid for p in g['players']) for g in state['games'].values()):
        raise ValueError('Finish or leave your current minigame first.')
    if stake<0 or stake>MAX_STAKE or int(stake)!=stake:raise ValueError(f'Stake must be a whole number from 0 to {MAX_STAKE}.')
    if stake and kind not in ('connect','ships','rps','blackjack'):raise ValueError('This game does not accept coin stakes.')
    e=member(wallets,uid,name)
    if e.get('coins',0)<stake:raise ValueError('You do not have enough coins for that stake.')
    g={'id':gid,'kind':kind,'players':[{'id':uid,'name':name,'badge':e.get('active_badge','')}],
       'status':'lobby','stake':int(stake),'reserved':False,'created':now,'deadline':now+600,
       'rev':0,'message_id':None,'result':'','paid':{}}
    if kind=='chessle':g['mode']=chessle.normalize_mode(mode)
    elif kind=='codebreaker':g['mode']=codebreaker_normalize_mode(mode)
    elif kind=='math':
        duration=int(mode or 60)
        if duration not in MATH_DURATION_MODES:raise ValueError('Math Rush must be 1 or 2 minutes.')
        g['mode']=duration
    state['games'][gid]=g
    # Retain active games plus a bounded recent history. Audit events remain permanent.
    done=sorted([x for x in state['games'].values() if x['status']=='finished'],key=lambda x:x.get('ended',0))
    for old in done[:-60]:state['games'].pop(old['id'],None)
    return g



def escape_clue_text(g, uid):
    """Return the clue packet visible to one Escape Room player.

    Solo players (or the last remaining player after teammates leave) receive
    every clue for the current room so a run can never require a second person.
    Multiplayer teams keep the normal private clue split.
    """
    if g.get('kind') != 'escape' or g.get('status') != 'playing':
        raise ValueError('Escape clues are only available during an active Escape Room.')
    players=[p['id'] for p in g.get('players',[]) if p['id'] not in g.get('left',[])]
    if uid not in players:
        raise ValueError('This information is only for current players.')
    d=g.get('data',{})
    rooms=d.get('rooms',[])
    if not rooms:
        raise ValueError('This Escape Room has no active room.')
    room=rooms[min(int(d.get('room',0)),len(rooms)-1)]
    clues=[str(x) for x in room.get('clues',[]) if str(x).strip()]
    if not clues:
        return '**Room information**\nNo separate private clues are required for this room.'
    if len(players) == 1:
        packet='\n\n'.join(f'**Clue {i+1}/{len(clues)}**\n{clue}' for i,clue in enumerate(clues))
        return '**Solo mode — all clues are yours:**\n\n'+packet
    seat=players.index(uid)
    return '**Your clue — share it with your team:**\n'+clues[seat%len(clues)]

def start(state,wallets,g,now,rng,bank):
    if len(g['players'])<MINIMUM[g['kind']]:raise ValueError('Not enough players yet.')
    if g['stake'] and g['kind'] not in ('connect','ships','rps','blackjack'):
        raise ValueError('This game does not accept coin stakes.')
    if g['stake']:
        for p in g['players']:move_coins(wallets,p,-g['stake'])
        g['reserved']=True
        if g['kind']=='blackjack':g['blackjack_reserved']=float(g['stake'])
    g['status']='playing';g['started']=now;g['deadline']=now+TURN_SECONDS
    g['expires']=now+(5400 if g['kind']=='poker' else 2400)
    kind=g['kind'];n=len(g['players'])
    if kind=='connect':g['data']={'board':[0]*42,'turn':0}
    elif kind=='ships':g['data']={'fleets':[fleet(rng) for _ in range(n)],'ready':[], 'shots':[[] for _ in range(n)],'turn':0,'notice':'Privately review your fleet and press Ready.'};g['deadline']=now+300
    elif kind=='rps':g['data']={'choices':{}}
    elif kind=='mines':
        g['data']={
            'mines':[], 'open':[], 'flags':[],
            'width':MINEFIELD_WIDTH,'height':MINEFIELD_HEIGHT,'count':MINEFIELD_MINES,
            # `mode` remains as a legacy/solo fallback. Multiplayer uses one
            # private mode per player so one teammate cannot accidentally flip
            # everyone else's next click from Reveal to Flag (or vice versa).
            'mode':'reveal',
            'modes':{p['id']:'reveal' for p in g['players']},
            'contributors':[],
            'last_move':None,
            'layout_rev':None,
        }
        g['deadline']=g['expires']
    elif kind=='poker':g['data']=poker.new(n,rng)
    elif kind=='blackjack':
        g['data']=blackjack.new(rng);g['data']['phase_rev']=g['rev']+1
        # Resolve natural Blackjacks immediately after the opening deal.
        player_natural=blackjack.is_blackjack(g['data']['player']);dealer_natural=blackjack.is_blackjack(g['data']['dealer'])
        if player_natural or dealer_natural:
            if player_natural and dealer_natural:
                g['data']['notice']='Both player and dealer have Blackjack.'
                _finish_blackjack(state,wallets,g,now,'push')
            elif player_natural:
                g['data']['notice']='Natural Blackjack!'
                _finish_blackjack(state,wallets,g,now,'blackjack')
            else:
                g['data']['notice']='Dealer has a natural Blackjack.'
                _finish_blackjack(state,wallets,g,now,'loss')
    elif kind=='escape':g['data']=build_escape(None,rng);g['deadline']=g['expires']
    elif kind=='chessle':g['data']=chessle.new(g.get('mode','normal'),rng);g['deadline']=g['expires']
    elif kind=='lingo':
        g['data']=lingo_new(state,rng);g['expires']=now+900;g['deadline']=g['expires']
    elif kind=='bomb':
        g['data']=bomb_new(rng);g['expires']=now+360;g['deadline']=g['expires']
    elif kind=='math':
        g['data']=math_new(g.get('mode',60),rng);g['data']['started_at']=now;g['data']['ends_at']=now+int(g['mode']);g['expires']=g['data']['ends_at']+30;g['deadline']=g['data']['ends_at']
    elif kind=='detective':
        g['data']=detective_new(rng);g['expires']=now+900;g['deadline']=g['expires']
    elif kind=='codebreaker':
        g['data']=codebreaker_new(rng,g.get('mode','easy'));g['expires']=now+1200;g['deadline']=g['expires']
    elif kind=='cluest':
        g['data']=cluest_new(rng);g['expires']=now+1800;g['deadline']=g['expires']
    elif kind=='trivia':
        seen=set()
        for p in g['players']:seen.update(state.get('seen',{}).get(p['id'],[]))
        qs=select_ladder(bank,seen,rng)
        g['data']={'questions':qs,'round':0,'answers':{},'correct':{p['id']:0 for p in g['players']},'blocks':{p['id']:[0,0,0] for p in g['players']},'last':'','reveal':False}
        for p in g['players']:
            state.setdefault('seen',{})[p['id']]=(state.get('seen',{}).get(p['id'],[])+[q['id'] for q in qs])[-600:]
        g['deadline']=now+35
    g['data']['phase_rev']=g['rev']+1


def fleet(rng):
    result=[];used=set()
    for length in [5,4,3,3,2]:
        while True:
            row,col=rng.randrange(10),rng.randrange(10);dr,dc=rng.choice([(1,0),(0,1)])
            cells=[(row+k*dr)*10+col+k*dc for k in range(length)]
            if row+(length-1)*dr<10 and col+(length-1)*dc<10 and not used.intersection(cells):
                result.append(cells);used.update(cells);break
    return result


def coordinate(value,size):
    value=str(value).strip().upper().replace(' ','')
    try:col=ord(value[0])-65;row=int(value[1:])-1
    except (ValueError,IndexError):raise ValueError('Use a coordinate such as B7.')
    if not (0<=row<size and 0<=col<size):raise ValueError('That square is outside the board.')
    return row*size+col


def coordinate_rect(value,width,height):
    value=str(value).strip().upper().replace(' ','')
    try:col=ord(value[0])-65;row=int(value[1:])-1
    except (ValueError,IndexError):raise ValueError('Use a square such as B7.')
    if not (0<=row<height and 0<=col<width):raise ValueError('That square is outside the board.')
    return row*width+col


def neighbors(cell,width=MINEFIELD_WIDTH,height=MINEFIELD_HEIGHT):
    row,col=divmod(cell,width)
    return {r*width+c for r in range(max(0,row-1),min(height,row+2)) for c in range(max(0,col-1),min(width,col+2))}-{cell}


def flood(opened,cell,mines,width=MINEFIELD_WIDTH,height=MINEFIELD_HEIGHT):
    todo=[cell]
    while todo:
        x=todo.pop()
        if x in opened or x in mines:continue
        opened.add(x)
        ns=neighbors(x,width,height)
        if not ns&mines:todo.extend(ns-opened-mines)


def solvable(mines,first,width=MINEFIELD_WIDTH,height=MINEFIELD_HEIGHT):
    total=width*height
    opened=set();flood(opened,first,mines,width,height);known=set()
    while len(opened)<total-len(mines):
        constraints=[];safe=set();newmines=set()
        for x in opened:
            ns=neighbors(x,width,height);unknown=ns-opened-known;count=len(ns&mines)-len(ns&known)
            if unknown:constraints.append((unknown,count))
        constraints.append((set(range(total))-opened-known,len(mines)-len(known)))
        for cells,count in constraints:
            if count==0:safe|=cells
            if count==len(cells):newmines|=cells
        if not safe and not newmines:
            for a,na in constraints:
                for b,nb in constraints:
                    if a<b:
                        diff=b-a;n=nb-na
                        if n==0:safe|=diff
                        elif n==len(diff):newmines|=diff
        if not safe and not (newmines-known):return False
        known|=newmines
        for x in safe:flood(opened,x,mines,width,height)
    return True


def mine_layout(first,rng,width=MINEFIELD_WIDTH,height=MINEFIELD_HEIGHT,count=MINEFIELD_MINES):
    total=width*height
    choices=list(set(range(total))-neighbors(first,width,height)-{first})
    for _ in range(800):
        mines=set(rng.sample(choices,count))
        if solvable(mines,first,width,height):return sorted(mines)
    raise ValueError('Could not create a fair board. Please choose the square again; no move was saved.')


def migrate_minefield_button_board(state,wallets,now):
    """Reset unfinished older Minefields once when moving to the current hard button board."""
    for g in state.get('games',{}).values():
        if g.get('kind')!='mines' or g.get('status')!='playing':continue
        d=g.get('data') or {}
        if int(d.get('width',d.get('size',8)) or 8)==MINEFIELD_WIDTH and int(d.get('height',8) or 8)==MINEFIELD_HEIGHT:
            continue
        g['data']={'mines':[], 'open':[], 'flags':[], 'width':MINEFIELD_WIDTH,'height':MINEFIELD_HEIGHT,'count':MINEFIELD_MINES,'mode':'reveal',
                   'modes':{p['id']:'reveal' for p in g.get('players',[])},'contributors':[],'last_move':None,'layout_rev':None,
                   'notice':'Minefield upgraded to the new hard 5×16 button board. The field restarted safely.'}
        g['data']['phase_rev']=g.get('rev',0)+1
        g['deadline']=g.get('expires',now+2400)
        g['rev']=g.get('rev',0)+1


def migrate_minefield_multiplayer(state,wallets,now):
    """Add multiplayer-safe metadata to current Minefields without resetting boards."""
    changed=0
    for g in state.get('games',{}).values():
        if g.get('kind')!='mines' or g.get('status')=='finished':
            continue
        d=g.setdefault('data',{})
        modes=d.setdefault('modes',{})
        fallback=d.get('mode','reveal')
        for p in g.get('players',[]):
            modes.setdefault(p['id'],fallback if fallback in ('reveal','flag') else 'reveal')
        d.setdefault('contributors',[])
        d.setdefault('last_move',None)
        d.setdefault('layout_rev',g.get('rev',0) if d.get('mines') else None)
        changed+=1
    return changed


def trivia_coins(blocks):
    easy,medium,hard=blocks
    return (1 if easy==5 else 0)+(3 if medium==5 else 1 if medium>=3 else 0)+(6 if hard==5 else 3 if hard>=3 else 0)


def retire_mine_stakes(state,wallets,now):
    for g in state['games'].values():
        if g['kind']=='mines' and g['status']!='finished' and g.get('stake',0):
            finish(state,wallets,g,[],now,'Minefield stakes removed. Reserved coins refunded.',refund=True)
            g['rev']+=1


def trivia_end_round(state,wallets,g,now):
    d=g['data'];q=d['questions'][d['round']]
    for uid,choice in d['answers'].items():
        if q['options'][choice]==q['answer']:
            d['correct'][uid]+=1
            if 'blocks' in d:d['blocks'][uid][min(d['round']//5,2)]+=1
    d['last']='Correct answer: '+q['answer'];d['reveal']=True
    g['deadline']=now+7
    if d['round']==14:
        # Participation earns no points; rewards are coins only, up to 10.
        rewards={uid:(trivia_coins(d['blocks'][uid]) if 'blocks' in d else (10 if n==15 else n*10//15)) for uid,n in d['correct'].items() if uid not in g.get('left',[])}
        winners=[uid for uid,n in rewards.items() if n>0]
        finish(state,wallets,g,winners,now,'Knowledge ladder complete.',rewards)


def action(state,wallets,gid,uid,name,command,value,now,rng,bank,expected=None):
    if gid not in state['games']:raise ValueError('This game is no longer available.')
    g=state['games'][gid]
    if expected is not None and g['rev']!=expected and command!='leave':
        # Independent players may answer the SAME round from the same old embed.
        # Reject controls from a previous question/room/turn, not a peer's answer.
        concurrent_lobby = command=='join' and g['status']=='lobby' and 0<=expected<=g['rev']
        d=g.get('data',{})
        concurrent_mines = (g['status']=='playing' and g.get('kind')=='mines'
                            and command in ('mine_click','mine_mode') and 0<=expected<=g['rev'])
        concurrent_round = (g['status']=='playing' and d.get('phase_rev',g['rev']+1)<=expected<=g['rev']
            and ((g['kind']=='trivia' and command=='answer' and not d['reveal'])
                 or (g['kind']=='rps' and command=='choose')
                 or (g['kind']=='escape' and command in ('inspect','hint','solve'))
                 or (g['kind']=='bomb' and command in ('bomb_info','bomb_code'))
                 or (g['kind']=='detective' and command in ('casefile','accuse'))
                 or (g['kind']=='codebreaker' and command=='codebreaker_submit')
                 or (g['kind']=='cluest' and command in ('cluest_board','cluest_call','cluest_hint'))
                 or (g['kind']=='ships' and command in ('ready','randomize','place') and len(d['ready'])<2)))
        if not (concurrent_lobby or concurrent_mines or concurrent_round):raise ValueError('The board changed. Use the latest buttons or Refresh.')
    if command in ('replay','replay_stake'):
        stake_value=None if command=='replay' else value
        _replay_blackjack(state,wallets,g,uid,name,now,rng,stake_value)
        g['rev']+=1
        return g
    if g['status']=='finished':raise ValueError('This game has finished.')
    if now >= g['deadline'] and command!='leave':raise ValueError('The timer has expired. Press Refresh in a moment.')
    players=[p['id'] for p in g['players']]
    if command=='join':
        if g['status']!='lobby' or uid in players:raise ValueError('You cannot join this lobby.')
        if len(players)>=LIMITS[g['kind']]:raise ValueError('This lobby is full.')
        if any(x['status']!='finished' and uid not in x.get('left',[]) and any(p['id']==uid for p in x['players']) for x in state['games'].values()):raise ValueError('You are already in a minigame.')
        e=member(wallets,uid,name)
        if e.get('coins',0)<g['stake']:raise ValueError('You do not have enough coins for this stake.')
        g['players'].append({'id':uid,'name':name,'badge':e.get('active_badge','')})
    else:
        if uid not in players or uid in g.get('left',[]):raise ValueError('Only current players in this game can use this action.')
        i=players.index(uid)
        if command=='start':
            if i!=0 or g['status']!='lobby':raise ValueError('Only the host can start the lobby.')
            start(state,wallets,g,now,rng,bank)
        elif command=='leave':
            if g['status']=='lobby':
                if i==0:finish(state,wallets,g,[],now,'Lobby closed.',refund=True)
                else:g['players'].pop(i)
            elif g['kind'] in ('connect','ships','rps'):
                finish(state,wallets,g,[players[1-i]],now,name+' forfeited.',amount=0)
            elif g['kind']=='mines':
                g.setdefault('left',[]).append(uid)
                remaining=[p for p in players if p not in g['left']]
                if not remaining:
                    finish(state,wallets,g,[],now,'Minefield closed after all players left. No coin reward.',refund=True)
                else:
                    g['data'].setdefault('last_move',None)
                    g['data']['notice']=name+' left the Minefield. The remaining team can continue.'
            elif g['kind']=='blackjack':
                g['data']['notice']=name+' left the Blackjack table.'
                _finish_blackjack(state,wallets,g,now,'loss')
            elif g['kind']=='bomb':
                g.setdefault('left',[]).append(uid)
                remaining=[p for p in players if p not in g['left']]
                if len(remaining)<2:finish(state,wallets,g,[],now,'Defusal cancelled — fewer than two defusers remain.',refund=True)
            else:
                g.setdefault('left',[]).append(uid)
                remaining=[p for p in players if p not in g['left']]
                if not remaining or (g['kind']=='poker' and len(remaining)<2):
                    finish(state,wallets,g,[],now,'Table closed after players left. No coin reward.',refund=True)
                elif g['kind']=='poker':
                    g['data']['departed']=[players.index(p) for p in g['left']]
                    poker.skip_departed(g['data'])
                    g['deadline']=now+60
                elif g['kind']=='trivia' and not g['data']['reveal']:
                    if all(p in g['data']['answers'] for p in remaining):trivia_end_round(state,wallets,g,now)
        else:
            if g['status']!='playing':raise ValueError('The host must start the game first.')
            d=g['data'];kind=g['kind']
            if kind=='connect':
                if i!=d['turn']:raise ValueError('It is not your turn.')
                col=int(value)
                if not 0<=col<7:raise ValueError('Choose column 1 to 7.')
                rows=[r for r in range(6) if d['board'][r*7+col]==0]
                if not rows:raise ValueError('That column is full.')
                r=max(rows);d['board'][r*7+col]=i+1;d['last_drop']=r*7+col;won=[]
                for dr,dc in [(0,1),(1,0),(1,1),(1,-1)]:
                    for offset in range(4):
                        cells=[(r+(k-offset)*dr,col+(k-offset)*dc) for k in range(4)]
                        if all(0<=rr<6 and 0<=cc<7 and d['board'][rr*7+cc]==i+1 for rr,cc in cells):won=[rr*7+cc for rr,cc in cells]
                if won:d['winning']=won;finish(state,wallets,g,[uid],now,name+' wins!',amount=5)
                elif all(d['board']):finish(state,wallets,g,[],now,'Draw. The board is full.')
                else:d['turn']=1-i;g['deadline']=now+TURN_SECONDS
            elif kind=='rps':
                if value not in ['rock','paper','scissors']:raise ValueError('Choose Rock, Paper or Scissors.')
                if uid in d['choices']:raise ValueError('Your choice is already locked.')
                d['choices'][uid]=value
                if len(d['choices'])==2:
                    a,b=[d['choices'][u] for u in players]
                    w=[] if a==b else [players[0 if (a,b) in [('rock','scissors'),('paper','rock'),('scissors','paper')] else 1]]
                    finish(state,wallets,g,w,now,f'{a.title()} vs {b.title()}. '+('Draw.' if not w else 'Round complete.'),amount=2)
            elif kind=='ships':
                if command=='randomize':
                    if i in d['ready']:raise ValueError('Your fleet is locked.')
                    d['fleets'][i]=fleet(rng)
                elif command=='place':
                    if i in d['ready']:raise ValueError('Your fleet is locked.')
                    parts=str(value).upper().replace(',',' ').split()
                    if len(parts)!=3:raise ValueError('Use: ship number, start square, H or V. Example: 1 A1 H.')
                    ship=int(parts[0])-1;cell=coordinate(parts[1],10);direction=parts[2]
                    if ship not in range(5) or direction not in ('H','V'):raise ValueError('Ship must be 1–5; direction H or V.')
                    length=[5,4,3,3,2][ship];row,col=divmod(cell,10);dr,dc=(0,1) if direction=='H' else (1,0)
                    if row+(length-1)*dr>=10 or col+(length-1)*dc>=10:raise ValueError('The ship would extend outside the board.')
                    cells=[(row+k*dr)*10+col+k*dc for k in range(length)]
                    other={c for j,ss in enumerate(d['fleets'][i]) if j!=ship for c in ss}
                    if set(cells)&other:raise ValueError('The ship overlaps another ship. Choose another position.')
                    d['fleets'][i][ship]=cells
                elif command=='ready':
                    if i in d['ready']:raise ValueError('Your fleet is already locked.')
                    d['ready'].append(i)
                    if len(d['ready'])==2:g['deadline']=now+TURN_SECONDS;d['phase_rev']=g['rev']+1
                elif command=='fire':
                    if len(d['ready'])<2:raise ValueError('Both players must lock their fleet first.')
                    if i!=d['turn']:raise ValueError('It is not your turn.')
                    cell=coordinate(value,10)
                    if cell in d['shots'][i]:raise ValueError('You already fired there.')
                    d['shots'][i].append(cell);enemy=d['fleets'][1-i];allcells={c for ship in enemy for c in ship}
                    sunk=any(cell in ship and set(ship)<=set(d['shots'][i]) for ship in enemy)
                    d['notice']=name+(' sank a ship!' if sunk else ' hit a ship!' if cell in allcells else ' missed.')
                    if allcells<=set(d['shots'][i]):finish(state,wallets,g,[uid],now,name+' sank the entire enemy fleet!',amount=5)
                    else:d['turn']=1-i;g['deadline']=now+TURN_SECONDS
                else:raise ValueError('Unknown fleet action.')
            elif kind=='mines':
                modes=d.setdefault('modes',{})
                fallback=d.get('mode','reveal')
                current_mode=modes.get(uid,fallback if fallback in ('reveal','flag') else 'reveal')
                if command=='mine_mode':
                    requested=str(value or '').strip().casefold()
                    if requested in ('reveal','flag'):
                        current_mode=requested
                    else:
                        # Backwards compatibility for already-rendered old
                        # Toggle buttons after a deploy.
                        current_mode='flag' if current_mode=='reveal' else 'reveal'
                    modes[uid]=current_mode
                    # Preserve old single-player behavior/state shape for
                    # backwards compatibility; multiplayer reads `modes`.
                    if len([p for p in players if p not in g.get('left',[])])==1:
                        d['mode']=current_mode
                    d['notice']=f'{name} changed personal mode to {current_mode.title()}.'
                else:
                    width=int(d.get('width',MINEFIELD_WIDTH));height=int(d.get('height',MINEFIELD_HEIGHT));total=width*height
                    if command=='mine_click':
                        try:cell=int(value)
                        except (TypeError,ValueError):raise ValueError('That Minefield square is invalid.')
                        if not 0<=cell<total:raise ValueError('That Minefield square is invalid.')
                        command='flag' if current_mode=='flag' else 'reveal'
                    else:
                        cell=coordinate_rect(value,width,height)
                    contributors=d.setdefault('contributors',[])
                    label=chr(65+(cell%width))+str(cell//width+1)
                    if command=='flag':
                        if cell in d['open']:raise ValueError('That square is already open.')
                        if cell in d['flags']:
                            d['flags'].remove(cell)
                            action_text='removed a flag from'
                        else:
                            d['flags'].append(cell)
                            action_text='flagged'
                        if uid not in contributors:contributors.append(uid)
                        d['last_move']={'uid':uid,'name':name,'action':action_text,'cell':cell,'label':label}
                        d['notice']=f'{name} {action_text} {label}.'
                    elif command=='reveal':
                        if cell in d['flags']:raise ValueError('That square is flagged. Switch your personal mode to Flag and remove it before revealing.')
                        if cell in d['open']:raise ValueError('That square is already open.')
                        # If two teammates click the untouched field at almost
                        # the same time, only the first click creates the mine
                        # layout. Reject a still-pending pre-layout click rather
                        # than letting it instantly hit a newly-created mine.
                        layout_rev=d.get('layout_rev')
                        if d['mines'] and expected is not None and layout_rev is not None and expected < int(layout_rev):
                            raise ValueError('A teammate just opened the first square. Use the updated Minefield before revealing another square.')
                        if uid not in contributors:contributors.append(uid)
                        if not d['mines']:
                            d['mines']=mine_layout(cell,rng,width,height,int(d.get('count',MINEFIELD_MINES)))
                            d['layout_rev']=g['rev']+1
                        d['last_move']={'uid':uid,'name':name,'action':'revealed','cell':cell,'label':label}
                        if cell in d['mines']:
                            lose_mines(state,wallets,g,now,name)
                        else:
                            opened=set(d['open']);flood(opened,cell,set(d['mines']),width,height);d['open']=sorted(opened)
                            d['flags']=[c for c in d['flags'] if c not in opened]
                            d['notice']=f'{name} revealed {label}.'
                            if len(opened)==total-len(d['mines']):
                                active=[p for p in players if p not in g.get('left',[])]
                                winners=[p for p in active if p in contributors]
                                if not winners:winners=[uid]
                                finish(state,wallets,g,winners,now,'All safe squares revealed — team victory!',amount=5)
                    else:raise ValueError('Unknown minefield action.')
            elif kind=='trivia':
                if command!='answer' or d['reveal']:raise ValueError('Wait for the next question.')
                if uid in d['answers']:raise ValueError('Your answer is locked.')
                choice=int(value)
                if not 0<=choice<4:raise ValueError('Choose A, B, C or D.')
                d['answers'][uid]=choice
                if all(p in d['answers'] for p in players if p not in g.get('left',[])):trivia_end_round(state,wallets,g,now)
            elif kind=='escape':
                if uid not in d['active']:d['active'].append(uid)
                room=d['rooms'][d['room']]
                if command=='hint':
                    if d['room'] not in d['hints']:d['hints'].append(d['room'])
                    d['notice']='Hint: '+room['hint']
                elif command=='solve':
                    if now-d['attempts'].get(uid,0)<5:raise ValueError('Wait five seconds between attempts.')
                    d['attempts'][uid]=now
                    if not answer_matches(room, value):
                        d['notice']=name+' tried an answer. The lock remains closed.'
                    else:
                        d['inventory'].append(room['item']);d['journal'].append(f"Chapter {d['room']+1} — {room['title']}: {room['answer']}");d['room']+=1;d['phase_rev']=g['rev']+1
                        d['notice']='Unlocked! Found: '+room['item']
                        if d['room']==len(d['rooms']):finish(state,wallets,g,[p for p in d['active'] if p not in g.get('left',[])],now,'Your team escaped!',10)
                elif command=='inspect':pass
                else:raise ValueError('Unknown exploration action.')
            elif kind=='lingo':
                if command!='lingo_guess':raise ValueError('Unknown Lingo action.')
                outcome=lingo_submit(d,value)
                if outcome=='solved':finish(state,wallets,g,[uid],now,f'Lingo solved: {d["target"]}.',amount=1)
                elif outcome=='failed':finish(state,wallets,g,[],now,f'No guesses left. The word was {d["target"]}.')
                else:g['deadline']=g['expires']
            elif kind=='bomb':
                if command!='bomb_code':raise ValueError('Unknown Bomb Defusal action.')
                current=[p for p in players if p not in g.get('left',[])]
                operator=current[d['module']%len(current)]
                if uid!=operator:raise ValueError('Only the current device operator can enter the module code. Share the answer with them.')
                if now-d.get('attempts',{}).get(uid,0)<3:raise ValueError('Wait three seconds between code attempts.')
                d.setdefault('attempts',{})[uid]=now;module=d['modules'][d['module']]
                if not bomb_answer_matches(module,value):
                    d['strikes']+=1;d['notice']=f'Wrong code — strike {d["strikes"]}/3.'
                    if d['strikes']>=3:finish(state,wallets,g,[],now,'The bomb detonated after three strikes. No coin reward.')
                else:
                    d['module']+=1;d['phase_rev']=g['rev']+1
                    if d['module']>=len(d['modules']):
                        active=[p for p in current if p not in g.get('left',[])]
                        finish(state,wallets,g,active,now,'Bomb defused! All three modules cleared.',amount=5)
                    else:d['notice']=f'Module cleared! Module {d["module"]+1}/3 is now active. Roles rotated.'
            elif kind=='math':
                if command!='math_answer':raise ValueError('Unknown Math Rush action.')
                d['attempts']+=1;correct=_numeric_equal(value,d['current']['a'])
                if correct:
                    d['score']+=1
                    d['last']=f'✅ Correct — score {d["score"]}.'
                    d['current']=_math_problem(rng,d['score'])
                else:
                    d['misses']+=1
                    d['last']='❌ Incorrect — try the same equation again.'
                d['notice']=d['last']
                g['deadline']=d['ends_at'];d['phase_rev']=g['rev']+1
            elif kind=='detective':
                if command!='accuse':raise ValueError('Unknown Detective action.')
                if detective_answer_matches(d,value):
                    active=[p for p in players if p not in g.get('left',[])]
                    finish(state,wallets,g,active,now,f'Case solved — {d["answer"]} was responsible.',amount=3)
                else:
                    d['attempts_left']-=1
                    if d['attempts_left']<=0:finish(state,wallets,g,[],now,f'Case closed unsolved. The culprit was {d["answer"]}.')
                    else:d['notice']=f'That accusation does not fit all the evidence. {d["attempts_left"]} accusations remain.'
            elif kind=='codebreaker':
                if command!='codebreaker_submit':raise ValueError('Unknown Codebreaker action.')
                d['attempts']=int(d.get('attempts',0))+1
                if codebreaker_answer_matches(d,value):
                    finish(state,wallets,g,[uid],now,f'Codebreaker cracked — the code was {d["answer"]}.',amount=2)
                else:
                    d['notice']='That code does not fit every transcript row. Keep comparing the exact/wrong-position counts.'
            elif kind=='cluest':
                if command=='cluest_call':
                    outcome=cluest_call(d,value)
                    if outcome=='solved':finish(state,wallets,g,[uid],now,'Cluest solved — every suspect was logically identified.',amount=5)
                elif command=='cluest_hint':
                    d['notice']=cluest_hint(d)
                else:raise ValueError('Unknown Cluest action.')
            elif kind=='chessle':
                if command!='guess':raise ValueError('Unknown Chessle action.')
                outcome=chessle.submit(d,value)
                if outcome=='solved':
                    amount=chessle.REWARDS[chessle.normalize_mode(d.get('mode'))]
                    finish(state,wallets,g,[uid],now,f'Opening solved: {d["opening"]}.',amount=amount)
                elif outcome=='failed':
                    finish(state,wallets,g,[],now,f'No guesses left. The opening was {d["opening"]}.')
                else:
                    g['deadline']=g['expires']
            elif kind=='blackjack':
                if command=='hit':
                    outcome=blackjack.hit(d)
                elif command=='stand':
                    outcome=blackjack.stand(d)
                elif command=='double':
                    if not blackjack.can_double(d):raise ValueError('Double is only available on the first two cards of this hand.')
                    if g['stake']:
                        move_coins(wallets,g['players'][0],-g['stake'])
                        g['blackjack_reserved']=float(g.get('blackjack_reserved',g['stake']))+float(g['stake'])
                    outcome=blackjack.double(d)
                elif command=='split':
                    if not blackjack.can_split(d):raise ValueError('Split requires two cards with the same Blackjack value and a maximum of four hands.')
                    if g['stake']:
                        move_coins(wallets,g['players'][0],-g['stake'])
                        g['blackjack_reserved']=float(g.get('blackjack_reserved',g['stake']))+float(g['stake'])
                    outcome=blackjack.split(d)
                else:raise ValueError('Unknown Blackjack action.')
                if outcome=='resolved':_finish_blackjack_round(state,wallets,g,now)
                if g['status']!='finished':g['deadline']=now+TURN_SECONDS
            elif kind=='poker':
                if command=='next':
                    if d['phase']!='between':raise ValueError('This hand is not finished.')
                    poker.start(d,rng)
                else:poker.act(d,i,command,value)
                poker.skip_departed(d)
                alive=[j for j,s in enumerate(d['stack']) if s>0 and j not in d.get('departed',[])]
                if d['phase']=='between' and len(alive)<=1:finish(state,wallets,g,[players[alive[0]]] if alive else [],now,'Poker tournament complete.',15 if len(players)>=3 and not g.get('left') else 0)
                g['deadline']=now+(12 if d['phase']=='between' else 60)
    g['rev']+=1
    return g


def tick(state,wallets,gid,now,rng):
    g=state['games'][gid]
    if g['status']=='finished' or now<g['deadline']:return
    if g['status']=='lobby':finish(state,wallets,g,[],now,'Lobby expired.',refund=True)
    elif now>=g['expires']:
        if g['kind']=='poker':
            # Do not award a tournament for merely waiting. Cancel without reward.
            finish(state,wallets,g,[],now,'Tournament time limit reached. No coin reward.',refund=True)
        elif g['kind']=='bomb':finish(state,wallets,g,[],now,'Time expired — the bomb detonated. No coin reward.')
        elif g['kind']=='lingo':finish(state,wallets,g,[],now,f'Time expired. The word was {g.get("data",{}).get("target","unknown")}.' )
        elif g['kind']=='detective':finish(state,wallets,g,[],now,f'Case timed out. The culprit was {g.get("data",{}).get("answer","unknown")}.' )
        elif g['kind']=='codebreaker':finish(state,wallets,g,[],now,f'Codebreaker timed out. The code was {g.get("data",{}).get("answer","unknown")}.' )
        elif g['kind']=='cluest':finish(state,wallets,g,[],now,'Cluest session timed out. No coin reward.')
        else:finish(state,wallets,g,[],now,'Session expired.',refund=True)
    elif g['kind']=='math':
        d=g['data'];uid=g['players'][0]['id']
        finish(state,wallets,g,[uid],now,f'Math Rush complete — {d["score"]} correct, {d["misses"]} incorrect.',amount=1)
    elif g['kind']=='trivia':
        d=g['data']
        if d['reveal']:d['round']+=1;d['answers']={};d['reveal']=False;g['deadline']=now+35;d['phase_rev']=g['rev']+1
        else:trivia_end_round(state,wallets,g,now)
    elif g['kind']=='poker':
        d=g['data']
        if d['phase']=='between':poker.start(d,rng)
        elif d['pending']:
            i=d['pending'][0];poker.act(d,i,'check' if d['bet'][i]>=d['target'] else 'fold')
        poker.skip_departed(d)
        alive=[j for j,s in enumerate(d['stack']) if s>0 and j not in d.get('departed',[])]
        if d['phase']=='between' and len(alive)<=1:finish(state,wallets,g,[g['players'][alive[0]]['id']] if alive else [],now,'Poker tournament complete.',15 if len(g['players'])>=3 and not g.get('left') else 0)
        g['deadline']=now+(12 if d['phase']=='between' else 60)
    elif g['kind']=='blackjack':
        g['data']['notice']='Blackjack turn timed out.'
        _finish_blackjack(state,wallets,g,now,'loss')
    elif g['kind']=='ships' and len(g['data']['ready'])<2:finish(state,wallets,g,[],now,'Fleet setup timed out.',refund=True)
    elif g['kind'] in ('connect','ships'):
        winner=g['players'][1-g['data']['turn']]['id'];finish(state,wallets,g,[winner],now,'Opponent ran out of time.',amount=0)
    elif g['kind']=='rps':finish(state,wallets,g,[],now,'A player did not choose in time.',refund=True)
    g['rev']+=1
