"""Descriptive high-Accuracy frequency by time control and opponent Elo context.

No population calibration is available. A conservative within-player historical\nfrequency check can support MODERATE, never HIGH.
"""
from statistics import median


def _valid_accuracy(g):
    from math import isfinite
    value=getattr(g,'accuracy',None)
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and 0<=value<=100


def _context(g):
    from math import isfinite
    own,opp=g.rating,g.opponent_rating
    if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not isfinite(v) or v<=0
           for v in (own,opp)):
        return 'unknown'
    delta=own-opp
    return 'near' if abs(delta)<=200 else 'weaker' if delta>200 else 'stronger'


def summarize(games):
    """One-pass grouping avoids rescanning the history for each Elo bucket."""
    buckets={kind:{'rows':0,'observed':[],'groups':{name:[] for name in
             ('near','weaker','stronger','unknown')}} for kind in ('rapid','blitz','bullet')}
    for g in games:
        bucket=buckets.get(g.time_class)
        if bucket is None:continue
        bucket['rows']+=1
        if _valid_accuracy(g):
            bucket['observed'].append(g)
            bucket['groups'][_context(g)].append(g)
    output={}
    for kind,bucket in buckets.items():
        observed=bucket['observed']
        groups={}
        for name,subset in bucket['groups'].items():
            differences=[g.rating-g.opponent_rating for g in subset if name!='unknown']
            lengths=[len(g.decisions) for g in subset if getattr(g,'decisions',None) is not None]
            groups[name]={'games':len(subset),'at_least_90':sum(g.accuracy>=90 for g in subset),
                          'at_least_95':sum(g.accuracy>=95 for g in subset),
                          'median_elo_difference':median(differences) if differences else None,
                          'median_recorded_moves':median(lengths) if lengths else None}
        output[kind]={'games':bucket['rows'],'accuracy_coverage':len(observed),
                      'at_least_90':sum(g.accuracy>=90 for g in observed),
                      'at_least_95':sum(g.accuracy>=95 for g in observed),
                      'opponent_context':groups}
    return {'by_time_class':output,'calibrated':False,'affects_priority':False,
            'note':'Descriptive only; absolute Accuracy rates are not population-calibrated.'}


def absolute_frequency_signal(games):
    """Uncalibrated manual-review flag, not a misconduct probability.

    Require broad published coverage, comparable Elo, game length and opponent
    diversity. Missing Accuracy is never interpreted as a low score.
    """
    buckets={}
    for g in games:
        kind=getattr(g,'time_class',None)
        control=getattr(g,'time_control',None)
        if kind in ('rapid','blitz','bullet') and isinstance(control,str) and control:
            buckets.setdefault((kind,control),[]).append(g)
    signals=[]
    for (kind,control),rows in sorted(buckets.items()):
        if len(rows)<40:continue
        covered=[g for g in rows if _valid_accuracy(g)]
        if len(covered)<30 or len(covered)/len(rows)<0.60:continue
        near=[g for g in covered if _context(g)=='near' and len(getattr(g,'decisions',()) or ())>=20]
        if len(near)<25:continue
        opponents={g.opponent_username.strip().casefold() for g in near
                   if isinstance(getattr(g,'opponent_username',None),str) and g.opponent_username.strip()}
        if len(opponents)<15:continue
        high90=sum(g.accuracy>=90 for g in near)
        high95=sum(g.accuracy>=95 for g in near)
        if high90/len(near)<0.40 or high95/len(near)<0.15:continue
        signals.append({'time_class':kind,'time_control':control,'eligible_games':len(near),
                        'at_least_90':high90,'at_least_95':high95,
                        'accuracy_covered':len(covered),'total_games':len(rows),
                        'distinct_opponents':len(opponents),
                        'note':'Uncalibrated absolute-frequency review flag; no population reference or cheating probability.'})
    return signals


def historical_signal(games, *, min_recent=30, min_baseline=60):
    """Exploratory same-*exact-control* earlier-history LOW-to-MODERATE support.

    Inspect the latest 30 actually played games in each time control, not the
    latest 30 games *with published Accuracy*. Otherwise missing-score
    selection could generate an artificial spike. A model-tail guard does not
    imply independently calibrated misconduct probability.
    """
    from math import comb, isfinite
    buckets={}
    for g in games:
        kind=getattr(g,'time_class',None)
        control=getattr(g,'time_control',None)
        if kind in ('rapid','blitz','bullet') and isinstance(control,str) and control:
            buckets.setdefault((kind,control),[]).append(g)
    # Include all represented exact controls in the search penalty, not only
    # whichever happened to produce a striking Accuracy increase.
    comparisons=max(3,len(buckets))
    signals=[]
    for (kind,control),unsorted in sorted(buckets.items()):
        rows=sorted(unsorted,key=lambda g:(g.ended,getattr(g,'identity','')))
        if len(rows)<min_recent+min_baseline:continue
        recent_all=rows[-min_recent:]
        earlier_all=rows[:-min_recent]
        def accuracy_present(g):
            value=getattr(g,'accuracy',None)
            return (isinstance(value,(int,float)) and not isinstance(value,bool)
                    and isfinite(value) and 0<=value<=100)
        recent_covered=[g for g in recent_all if accuracy_present(g)]
        earlier_covered=[g for g in earlier_all if accuracy_present(g)]
        # Published Accuracy is not missing at random. Do not allow a
        # handful of selectively analysed games to define the recent rate.
        if len(recent_covered)<.80*len(recent_all):continue
        if len(earlier_covered)<.60*len(earlier_all):continue
        def eligible(g):
            own,opp=g.rating,g.opponent_rating
            return (isinstance(own,(int,float)) and not isinstance(own,bool)
                    and isfinite(own) and own>0
                    and isinstance(opp,(int,float)) and not isinstance(opp,bool)
                    and isfinite(opp) and opp>0
                    and abs(own-opp)<=200
                    and len(getattr(g,'decisions',()) or ())>=20)
        recent=[g for g in recent_covered if eligible(g)]
        earlier=[g for g in earlier_covered if eligible(g)]
        if len(recent)<20 or len(earlier)<40:continue
        opponents={name.strip().casefold() for g in recent
                   if isinstance((name := getattr(g,'opponent_username',None)),str)
                   and name.strip()}
        if len(opponents)<10:continue
        if abs(median(g.rating for g in recent)-median(g.rating for g in earlier))>150:continue
        if abs(median(g.opponent_rating for g in recent)-median(g.opponent_rating for g in earlier))>150:continue
        # Wilson ~99% upper bound for the historical >=90% frequency;
        # a conservative modelling aid under unverified game independence.
        n=len(earlier)
        k=sum(g.accuracy>=90 for g in earlier)
        z=2.576
        p=(k/n+z*z/(2*n)+z*((k/n*(1-k/n)+z*z/(4*n))/n)**.5)/(1+z*z/n)
        observed=sum(g.accuracy>=90 for g in recent)
        m=len(recent)
        if observed/m<=p+0.15:continue
        tail=sum(comb(m,j)*p**j*(1-p)**(m-j) for j in range(observed,m+1))
        adjusted=min(1.0,tail*comparisons)
        if adjusted<=0.01:
            signals.append({'time_class':kind,'time_control':control,
                            'recent_games':m,'recent_at_least_90':observed,
                            'recent_accuracy_covered':len(recent_covered),
                            'recent_total_games':len(recent_all),
                            'baseline_accuracy_covered':len(earlier_covered),
                            'baseline_total_games':len(earlier_all),
                            'baseline_games':n,'baseline_at_least_90':k,
                            'adjusted_tail_bound':adjusted,
                            'controls_searched':comparisons,
                            'distinct_opponents':len(opponents),
                            'recent_first_ended':recent_all[0].ended,
                            'recent_last_ended':recent_all[-1].ended,
                            'note':'Uncalibrated independent-game model; never HIGH evidence.'})
    return signals
