"""Search-size protection for bounded Elo-points periods.

The Chernoff bound assumes independent games and correct expected points.
Neither assumption is calibrated for this bot. This is a model-based guard
against searching many windows, never a misconduct probability or a verdict.
"""
from math import exp, expm1, log, log1p

from fairplay_outcome_study import expected_points


def points_bound(games):
    rows=[(g.get('score'),expected_points(g.get('player_elo'),g.get('opponent_elo'))) for g in games]
    rows=[(score,p) for score,p in rows if type(score) in (int,float) and score in (0,.5,1) and p is not None]
    n=len(rows);actual=sum(score for score,p in rows);expected=sum(p for score,p in rows)
    if not n or actual<=expected:return 1.0
    if actual==n:return exp(sum(log(p) for score,p in rows))
    # Bounded [0,1] points, including draws. The Bernoulli MGF upper-bounds
    # the MGF for points with the same mean; it does not estimate winrate.
    lo,hi=0.0,64.0
    for _ in range(40):
        t=(lo+hi)/2;et=exp(t)
        tilted=sum(p*et/(1+p*(et-1)) for score,p in rows)
        if tilted<actual:lo=t
        else:hi=t
    t=(lo+hi)/2
    return min(1.0,exp(sum(log1p(p*expm1(t)) for score,p in rows)-t*actual))


def protect_periods(periods,games):
    """Count each physical chronological window once, not each reference mode."""
    groups={kind:[g for g in games if g['time_class']==kind] for kind in ('rapid','blitz','bullet')}
    cache={}
    count=len({(p['time_class'],p['first_game'],p['last_game']) for p in periods})
    for p in periods:
        key=(p['time_class'],p['first_game'],p['last_game'])
        if key not in cache:
            rows=groups[key[0]][key[1]-1:key[2]]
            adjusted=min(1.0,points_bound(rows)*count)
            short=p['games']<10
            short_ok=not short or (p['games']>=5 and p['results']['games']==p['games']
                                  and p['results']['wins']==p['games'])
            cache[key]={'searched_windows':count,'adjusted_model_bound':adjusted,
                        'short_sample':short,'short_sample_complete':short_ok,
                        'high_pass':short_ok and adjusted<=.01,
                        'moderate_pass':short_ok and adjusted<=.10,
                        'note':'Independent-game Elo model guard, not a cheating probability; ratings may be inaccurate in rated or unrated play.'}
        p['search_guard']=cache[key]
        for checks in p['scenarios'].values():
            raw=checks['results_pass']
            checks['results_review_pass']=raw and cache[key]['moderate_pass']
            checks['results_pass']=raw and cache[key]['high_pass']
            checks['priority']=('HIGH' if checks['accuracy_pass'] and checks['results_pass'] else
                'MODERATE' if checks['accuracy_pass'] or checks['results_review_pass'] else
                'LOW' if checks['accuracy_covered'] and checks['results_covered'] else 'INSUFFICIENT DATA')
