"""Descriptive high-Accuracy frequency by time control and opponent Elo context.

No population calibration is available. These counts must never set priority.
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
