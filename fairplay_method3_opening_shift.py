"""Temporary opening-switch context around an already selected Method 3 window.

Uses existing validated UCI moves; no PGN refetch, engine, ECO database or API.
A change in repertoire is not evidence of cheating.
"""
from collections import Counter

MIN_OPPORTUNITIES = 8
MAX_NEIGHBORS = 50

def opening_choice(game):
    """Comparable opening decisions only, never mix color/opponent first move."""
    moves = getattr(game, 'moves', ()) or ()
    if not moves:
        return None
    kind = getattr(game, 'time_class', None)
    control = getattr(game, 'time_control', None)
    if game.color:  # Player is White: their first move is the choice.
        first = moves[0]
        if first not in ('e2e4', 'd2d4', 'c2c4', 'g1f3'):
            first = 'other'
        return ((kind, control, 'white', 'first move'), first)
    if len(moves) < 2:
        return None
    first, reply = moves[:2]
    if first == 'e2e4':
        family = {'c7c6':'Caro-Kann', 'c7c5':'Sicilian', 'e7e5':'Open Game',
                  'e7e6':'French', 'd7d5':'Scandinavian', 'g7g6':'Modern'}.get(reply,'Other vs 1.e4')
        return ((kind,control,'black','vs 1.e4'), family)
    if first == 'd2d4':
        family = {'d7d5':'1...d5', 'g8f6':'Indian setup', 'f7f5':'Dutch'}.get(reply,'Other vs 1.d4')
        return ((kind,control,'black','vs 1.d4'), family)
    return None

def temporary_switch(games, selected):
    """Require a dominant DURING choice absent in BOTH matched flanks.

    Selection boundaries use exact class-local game indices, not 200-game blocks.
    Counts use all played matching games, including games without Accuracy.
    """
    if not selected or not selected.get('first_game') or not selected.get('last_game'):
        return None
    kind=selected['time_class']
    ordered=sorted((g for g in games if g.time_class==kind),
                   key=lambda g:(g.ended,g.identity))
    first,last=selected['first_game']-1,selected['last_game']
    if not (0<=first<last<=len(ordered)):
        return None
    during=ordered[first:last]
    options=Counter(k for g in during if (choice:=opening_choice(g)) for k,v in (choice,))
    for key,_ in options.most_common():
        def matched(rows):
            return [value for g in rows if (choice:=opening_choice(g))
                    for group,value in (choice,) if group==key]
        middle=matched(during)
        before=matched(ordered[:first])[-MAX_NEIGHBORS:]
        after=matched(ordered[last:])[:MAX_NEIGHBORS]
        if min(len(before),len(middle),len(after))<MIN_OPPORTUNITIES:
            continue
        mid_choice,mid_count=Counter(middle).most_common(1)[0]
        pre_choice,pre_count=Counter(before).most_common(1)[0]
        post_choice,post_count=Counter(after).most_common(1)[0]
        if pre_choice!=post_choice or mid_choice==pre_choice:
            continue
        if (mid_count/len(middle)<.75 or pre_count/len(before)<.70
                or post_count/len(after)<.70):
            continue
        if (before.count(mid_choice)/len(before)>.20
                or after.count(mid_choice)/len(after)>.20):
            continue
        return {'time_class':kind,'time_control':key[1],
                'color':key[2],'opponent_opening':key[3],
                'before':pre_choice,'during':mid_choice,'after':post_choice,
                'counts':{'before':len(before),'during':len(middle),'after':len(after)},
                'fractions':{'before':round(pre_count/len(before),3),
                             'during':round(mid_count/len(middle),3),
                             'after':round(post_count/len(after),3)},
                'note':'Temporary opening change; context only, not proof of misconduct.'}
    return None
