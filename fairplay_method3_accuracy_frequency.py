"""Descriptive high-Accuracy frequency by time control and opponent Elo context.

No population calibration is available. A conservative within-player historical\nfrequency check can support MODERATE, never HIGH.
"""
from statistics import median


def summarize(games):
    output={}
    for kind in ('rapid','blitz','bullet'):
        rows=[g for g in games if g.time_class==kind]
        observed=[g for g in rows if isinstance(g.accuracy,(int,float))
                  and not isinstance(g.accuracy,bool) and 0<=g.accuracy<=100]
        def bucket(g):
            own,opp=g.rating,g.opponent_rating
            if any(not isinstance(v,(int,float)) or isinstance(v,bool) or v<=0
                   for v in (own,opp)):
                return 'unknown'
            delta=own-opp
            return 'near' if abs(delta)<=200 else 'weaker' if delta>200 else 'stronger'
        groups={}
        for name in ('near','weaker','stronger','unknown'):
            subset=[g for g in observed if bucket(g)==name]
            differences=[g.rating-g.opponent_rating for g in subset
                         if bucket(g)!='unknown']
            lengths=[len(g.decisions) for g in subset if getattr(g,'decisions',None) is not None]
            groups[name]={
                'games':len(subset),
                'at_least_90':sum(g.accuracy>=90 for g in subset),
                'at_least_95':sum(g.accuracy>=95 for g in subset),
                'median_elo_difference':median(differences) if differences else None,
                'median_recorded_moves':median(lengths) if lengths else None}
        output[kind]={
            'games':len(rows),'accuracy_coverage':len(observed),
            'at_least_90':sum(g.accuracy>=90 for g in observed),
            'at_least_95':sum(g.accuracy>=95 for g in observed),
            'opponent_context':groups}
    return {'by_time_class':output,
            'calibrated':False,'affects_priority':False,
            'note':'Descriptive only. Elo/opponent mix and game length are reported, not population-adjusted. '
                   'A calibrated expected frequency and multiple-testing correction are required before using these counts for priority.'}


def historical_signal(games, *, min_recent=30, min_baseline=60):
    """Conservative exploratory LOW-to-MODERATE support from prior same-control games.

    Fixed latest-window comparison avoids searching for the most extreme window.
    Requires many distinct opponents and a substantial near-Elo sample.
    This is not a population-normalized misconduct probability.
    """
    from math import comb
    signals=[]
    for kind in ('rapid','blitz','bullet'):
        rows=sorted((g for g in games if g.time_class==kind and
                     isinstance(g.accuracy,(int,float)) and
                     not isinstance(g.accuracy,bool) and 0<=g.accuracy<=100),
                    key=lambda g:(g.ended,getattr(g,'identity','')))
        if len(rows)<min_recent+min_baseline:continue
        recent=rows[-min_recent:]
        earlier=rows[:-min_recent]
        def eligible(g):
            own,opp=g.rating,g.opponent_rating
            return (isinstance(own,(int,float)) and not isinstance(own,bool) and own>0 and
                    isinstance(opp,(int,float)) and not isinstance(opp,bool) and opp>0 and
                    abs(own-opp)<=200 and len(getattr(g,'decisions',()) or ())>=20)
        recent=[g for g in recent if eligible(g)]
        earlier=[g for g in earlier if eligible(g)]
        if len(recent)<20 or len(earlier)<40:continue
        opponents={getattr(g,'opponent_username',None) for g in recent}
        opponents.discard(None)
        if len(opponents)<10:continue
        # Only compare periods with similar rating distributions.
        if abs(median(g.rating for g in recent)-median(g.rating for g in earlier))>150:continue
        if abs(median(g.opponent_rating for g in recent)-median(g.opponent_rating for g in earlier))>150:continue
        # Baseline upper Wilson bound prevents small historical frequencies
        # from becoming an unrealistically optimistic null model.
        n=len(earlier)
        k=sum(g.accuracy>=90 for g in earlier)
        z=2.576  # ~99% Wilson upper bound
        p=(k/n+z*z/(2*n)+z*((k/n*(1-k/n)+z*z/(4*n))/n)**.5)/(1+z*z/n)
        observed=sum(g.accuracy>=90 for g in recent)
        m=len(recent)
        if observed/m<=p+0.15:continue
        # Exact one-sided binomial tail, conservative Bonferroni for 3 controls.
        tail=sum(comb(m,j)*p**j*(1-p)**(m-j) for j in range(observed,m+1))
        if min(1.0,tail*3)<=0.01:
            signals.append({'time_class':kind,'recent_games':m,
                            'recent_at_least_90':observed,'baseline_games':n,
                            'baseline_at_least_90':k,'adjusted_tail_bound':min(1.0,tail*3),
                            'distinct_opponents':len(opponents)})
    return signals
