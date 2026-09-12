"""Texas Hold'em tournament engine. Integer chips, full side pots, split pots."""
from collections import Counter
from itertools import combinations

RANKS = '23456789TJQKA'
SUITS = 'cdhs'


def rank5(cards):
    values = sorted([RANKS.index(c[0]) + 2 for c in cards], reverse=True)
    groups = sorted(((n, r) for r, n in Counter(values).items()), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    unique = sorted(set(values), reverse=True)
    straight = unique[0] if len(unique) == 5 and unique[0] - unique[-1] == 4 else 0
    if unique == [14, 5, 4, 3, 2]:
        straight = 5
    if flush and straight: return (8, straight)
    if groups[0][0] == 4: return (7, groups[0][1], groups[1][1])
    if [x[0] for x in groups] == [3, 2]: return (6, groups[0][1], groups[1][1])
    if flush: return (5, *values)
    if straight: return (4, straight)
    if groups[0][0] == 3: return (3, groups[0][1], *sorted([r for n,r in groups[1:]], reverse=True))
    if [x[0] for x in groups[:2]] == [2, 2]: return (2, *sorted([groups[0][1],groups[1][1]], reverse=True), groups[2][1])
    if groups[0][0] == 2: return (1, groups[0][1], *sorted([r for n,r in groups[1:]], reverse=True))
    return (0, *values)


def rank(cards):
    return max(rank5(c) for c in combinations(cards, 5))


def clockwise(p, after, seats):
    return sorted(seats, key=lambda i: (i-after-1) % len(p['stack']))


def pay(p, i, amount):
    amount = min(int(amount), p['stack'][i])
    p['stack'][i] -= amount
    p['bet'][i] += amount
    p['total'][i] += amount


def new(n, rng):
    p = {'stack': [500]*n, 'dealer': n-1, 'hand': 0, 'phase': 'between', 'log': ''}
    start(p, rng)
    return p


def start(p, rng):
    alive = [i for i,s in enumerate(p['stack']) if s > 0 and i not in p.get('departed',[])]
    if len(alive) < 2:
        p['phase'] = 'finished'; return
    n = len(p['stack'])
    p['dealer'] = clockwise(p, p['dealer'], alive)[0]
    p['hand'] += 1
    p['bb'] = min(1000, 20 * 2**((p['hand']-1)//5))
    p.update(deck=[r+s for r in RANKS for s in SUITS], board=[], holes=[[] for _ in range(n)],
             bet=[0]*n, total=[0]*n, folded=[i not in alive for i in range(n)], phase='preflop', log='', reveal=False)
    rng.shuffle(p['deck'])
    for _ in range(2):
        for i in alive: p['holes'][i].append(p['deck'].pop())
    sb = p['dealer'] if len(alive) == 2 else clockwise(p,p['dealer'],alive)[0]
    bb = clockwise(p,sb,alive)[0]
    pay(p,sb,p['bb']//2); pay(p,bb,p['bb'])
    p['target'] = p['bb']; p['min_raise'] = p['bb']
    p['pending'] = clockwise(p,bb,[i for i in alive if p['stack'][i]>0])
    p['acted_at'] = {}
    advance(p)


def can_raise(p, i):
    old = p['acted_at'].get(str(i))
    return old is None or p['target']-old >= p['min_raise']


def act(p, i, action, value=0):
    if not p.get('pending') or p['pending'][0] != i or p['phase'] in ('between','finished'):
        raise ValueError('It is not your turn.')
    owed = max(0,p['target']-p['bet'][i])
    if action == 'fold':
        p['folded'][i] = True
    elif action in ('call','check'):
        if action == 'check' and owed: raise ValueError('You must call or fold.')
        pay(p,i,owed)
    elif action in ('raise','allin'):
        total = p['bet'][i]+p['stack'][i] if action == 'allin' else int(value)
        if total <= p['target']:
            if action != 'allin': raise ValueError('Raise must exceed the current bet.')
            pay(p,i,p['stack'][i])
        else:
            if not can_raise(p,i): raise ValueError('A short all-in has not reopened raising for you.')
            if total > p['bet'][i]+p['stack'][i]: raise ValueError('Not enough chips.')
            raise_size = total-p['target']
            full = raise_size >= p['min_raise']
            if not full and total != p['bet'][i]+p['stack'][i]:
                raise ValueError(f'Minimum raise-to is {p["target"]+p["min_raise"]} chips.')
            pay(p,i,total-p['bet'][i])
            p['target'] = total
            if full: p['min_raise'] = raise_size
            p['pending'] = clockwise(p,i,[j for j in range(len(p['stack'])) if j!=i and not p['folded'][j] and p['stack'][j]>0 and (full or p['bet'][j]<total)])
            p['acted_at'][str(i)] = p['target']
            advance(p); return
    else: raise ValueError('Unknown poker action.')
    p['acted_at'][str(i)] = p['target']
    p['pending'] = [j for j in p['pending'] if j != i]
    advance(p)


def advance(p):
    while True:
        live = [i for i in range(len(p['stack'])) if not p['folded'][i]]
        if len(live) == 1:
            i = live[0]; prize = sum(p['total']);p['stack'][i] += prize
            p['log'] = f'Seat {i+1} wins {prize} chips. Everyone else folded.'
            p['total'] = [0]*len(p['stack']);p['pending']=[];p['phase']='between';return
        p['pending'] = [i for i in p['pending'] if not p['folded'][i] and p['stack'][i]>0]
        able = [i for i in live if p['stack'][i]>0]
        if len(able) <= 1 and all(p['bet'][i]>=p['target'] for i in able):
            p['pending'] = []
        if p['pending']: return
        if p['phase'] == 'river':
            showdown(p);return
        p['deck'].pop()  # Burn card.
        count = 3 if p['phase']=='preflop' else 1
        p['board'].extend(p['deck'].pop() for _ in range(count))
        p['phase'] = {'preflop':'flop','flop':'turn','turn':'river'}[p['phase']]
        p['bet']=[0]*len(p['stack']);p['target']=0;p['min_raise']=p['bb'];p['acted_at']={}
        p['pending'] = clockwise(p,p['dealer'],able)
        # If only one player can act, run out the board without pointless bets.
        if len(able)<=1:p['pending']=[]


def showdown(p):
    previous=0; lines=[]
    for level in sorted(set(p['total'])-{0}):
        contributors=[i for i,t in enumerate(p['total']) if t>=level]
        amount=(level-previous)*len(contributors);previous=level
        eligible=[i for i in contributors if not p['folded'][i]]
        if not eligible: raise RuntimeError('Invalid side pot: no eligible player.')
        best=max(rank(p['holes'][i]+p['board']) for i in eligible)
        winners=[i for i in eligible if rank(p['holes'][i]+p['board'])==best]
        winners=clockwise(p,p['dealer'],winners)
        for k,i in enumerate(winners):p['stack'][i]+=amount//len(winners)+(k<amount%len(winners))
        lines.append(f'{amount} chips to '+', '.join('seat '+str(i+1) for i in winners))
    p['log']='; '.join(lines);p['total']=[0]*len(p['stack']);p['pending']=[]
    p['phase']='between';p['reveal']=True


def skip_departed(p):
    """A player who left auto-folds when next required to act; all-ins settle."""
    while p.get('pending') and p['pending'][0] in p.get('departed',[]):
        act(p,p['pending'][0],'fold')
