"""Fast, descriptive matched-period context for Method 3 behavior charts.

NO engine, positional quality assessment, external service, classifier update,
or misconduct likelihood. Each game contributes at most once per comparison.
"""
from collections import Counter, defaultdict
from math import isfinite
from statistics import median

from fairplay_method3_opening_shift import opening_choice

MIN_GAMES = 5
MIN_CLOCK_COVERAGE = 0.60
MIN_OPENING_GAMES = 8
FLANK_FLOOR = 86400.0


def _finite(value):
    return type(value) in (int, float) and isfinite(value)


def _window(result):
    data = getattr(result, 'diagnostics', {}).get('method3', {})
    chosen = data.get('selected') or {}
    start, end = chosen.get('first_ended'), chosen.get('last_ended')
    if not _finite(start) or not _finite(end) or start > end:
        return None
    return start, end, max(FLANK_FLOOR, end-start), chosen.get('time_class')


def _era(ended, start, end, flank):
    if not _finite(ended):return None
    if start-flank <= ended < start:return 'before'
    if start <= ended <= end:return 'during'
    if end < ended <= end+flank:return 'after'
    return None


def matched_clock_controls(result, clock_values, *, minimum=MIN_GAMES):
    """Compare medians across classes with same exact controls and dates.

    Unlike a full-history modal control, find a control supported in BOTH
    flanks for EACH time class. A different control in either flank does not
    substitute for missing observations. Select the most balanced eligible
    exact control; tie-break deterministically. Use at least two classes.
    """
    window = _window(result)
    if window is None:return None
    start, end, flank, _ = window
    groups = defaultdict(lambda: {'before':[], 'during':[], 'after':[]})
    totals = defaultdict(lambda: {'before':0, 'during':0, 'after':0})
    for game in result.games:
        kind = getattr(game, 'time_class', None)
        control = getattr(game, 'time_control', None)
        if kind not in ('bullet','blitz','rapid') or not isinstance(control,str):
            continue
        era = _era(getattr(game,'ended',None),start,end,flank)
        if era is None:continue
        key = (kind,control)
        totals[key][era]+=1
        value = clock_values.get(game.identity)
        if _finite(value) and value >= 0:
            groups[key][era].append(value)

    kinds = {}
    for (kind,control),group in sorted(groups.items()):
        before,during = len(group['before']),len(group['during'])
        if min(before,during)<minimum:continue
        counts=totals[(kind,control)]
        # Selectively missing clock comments can create a false convergence.
        # Without adequate per-era coverage, the gap must stay unavailable.
        if (before < MIN_CLOCK_COVERAGE*counts['before']
                or during < MIN_CLOCK_COVERAGE*counts['during']):continue
        # Same-control coverage is explicit, not silently imputed.
        quality = (min(before,during),before+during,control)
        previous = kinds.get(kind)
        if previous is None or quality[:2]>previous[0][:2] or (
                quality[:2]==previous[0][:2] and control<previous[1]):
            kinds[kind]=(quality,control,group,totals[(kind,control)])

    selected = {kind:kinds[kind] for kind in ('bullet','blitz','rapid') if kind in kinds}
    def summarize(era):
        if len(selected)<2:return None
        classes={kind:{'games':len(group[era]),'total_games':counts[era],
                       'median_seconds':median(group[era])}
                 for kind,(_,control,group,counts) in selected.items()
                 if len(group[era])>=minimum}
        if len(classes)!=len(selected):return None
        values=[row['median_seconds'] for row in classes.values()]
        return {'classes':classes,'gap_seconds':max(values)-min(values)}
    return {'comparable':tuple(selected),'flank_seconds':flank,
            'controls':{kind:control for kind,(_,control,_,__) in selected.items()},
            'before':summarize('before'),'during':summarize('during'),
            'after':summarize('after'),
            'counts':{kind:{era:len(group[era]) for era in ('before','during','after')}
                      for kind,(_,_,group,_) in selected.items()}}


def opening_repetition(result, *, min_games=MIN_OPENING_GAMES):
    """Probability that two DIFFERENT games repeat the same opening choice.

    The finite-sample n(n-1) denominator matters: using sum(p*p) unfairly
    inflates apparent repetition for groups with fewer games. Compare only
    identical (class, exact control, color, opponent first-move context)
    before and during a selected period. Context only.
    """
    window = _window(result)
    if window is None:return None
    start,end,_,kind=window
    if kind not in ('bullet','blitz','rapid'):return None
    strata=defaultdict(lambda:{'before':[],'during':[],'after':[]})
    for game in result.games:
        if getattr(game,'time_class',None)!=kind or type(getattr(game,'color',None)) is not bool:
            continue
        era=('before' if game.ended<start else
             'during' if game.ended<=end else 'after')
        choice=opening_choice(game)
        if choice is None:continue
        key,value=choice
        strata[key][era].append((game.ended,game.identity,value))
    options=[]
    for key,eras in strata.items():
        before=sorted(eras['before'])[-50:]
        during=sorted(eras['during'])
        after=sorted(eras['after'])[:50]
        if min(len(before),len(during))<min_games:continue
        options.append((min(len(before),len(during)),len(during),key,
                        (before,during,after)))
    if not options:return None
    _,_,key,eras=sorted(options,key=lambda row:(-row[0],-row[1],row[2]))[0]
    def summarize(rows):
        n=len(rows)
        if n<min_games:return None
        repeats=sum(count*(count-1) for count in Counter(v for _,__,v in rows).values())
        return {'games':n,'repeat_fraction':repeats/(n*(n-1)),
                'distinct_choices':len(set(v for _,__,v in rows))}
    before,during,after=eras
    return {'time_class':key[0], 'time_control':key[1],
            'color':key[2], 'opponent_context':key[3],
            'before':summarize(before),'during':summarize(during),
            'after':summarize(after)}


def time_pressure_response(result, pressure_ratios, *, minimum=MIN_GAMES):
    """Compare *paired* in-game under-10 / 30+-second think-time ratios.

    Ratios use each game's own positive >=30s reference, not other people's
    pacing. Restrict to the selected period time class AND exact time control.
    The same 24h-minimum calendar flank is used for before/during comparison.
    Return unavailable instead of interpreting missing pressure as a zero.
    """
    window=_window(result)
    if window is None:return None
    start,end,flank,kind=window
    if kind not in ('bullet','blitz','rapid'):return None
    by_control=defaultdict(lambda:{'before':[],'during':[],'after':[]})
    total_by_control=defaultdict(lambda:Counter())
    for game in result.games:
        if getattr(game,'time_class',None)!=kind:continue
        era=_era(getattr(game,'ended',None),start,end,flank)
        if era is None:continue
        total_by_control[game.time_control][era]+=1
        ratio=pressure_ratios.get(game.identity)
        if not _finite(ratio) or ratio<0:continue
        by_control[game.time_control][era].append(ratio)
    eligible=[(min(len(x['before']),len(x['during'])),
               len(x['before'])+len(x['during']),control,x)
              for control,x in by_control.items()
              if min(len(x['before']),len(x['during']))>=minimum]
    if not eligible:return None
    _,_,control,eras=sorted(eligible,key=lambda x:(-x[0],-x[1],x[2]))[0]
    def summarize(era):
        values=eras[era]
        return {'games':len(values),
                'total_games':total_by_control[control][era],
                'median_ratio':median(values)} if len(values)>=minimum else None
    return {'time_class':kind,'time_control':control,
            'before':summarize('before'),'during':summarize('during'),
            'after':summarize('after'),'flank_seconds':flank,
            'note':'Ratio <1 means faster under 10s than with at least 30s in the SAME game. '
                   'Requires one valid move in each state, at least five different games per era.'}
