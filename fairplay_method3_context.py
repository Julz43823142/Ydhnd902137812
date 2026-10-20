"""Retrospective, strictly descriptive context for the selected Method 3 period.

Same-date cross-time-control results and before/during/after timing are NOT
independent cheating signals. Coverage and exact time controls are always
included; sparse classes never become evidence merely through comparison.
"""
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from fairplay_outcome_study import expected_points


def _date(timestamp):
    return datetime.fromtimestamp(timestamp,timezone.utc).strftime('%d %b %Y')


def _think(game):
    clocks=[d.think for d in game.decisions
            if getattr(d,'clock_valid',False)
            and isinstance(d.think,(int,float)) and not isinstance(d.think,bool)
            and 0 <= d.think < 120]
    # A one-move game cannot reliably characterize decision timing.
    return median(clocks) if len(clocks)>=5 else None


def _summary(games):
    eligible=[]
    accuracies=[]
    timings=[]
    for g in games:
        p=expected_points(g.rating,g.opponent_rating)
        if p is not None and g.score in (0,.5,1):
            eligible.append((g.score,p))
        if isinstance(g.accuracy,(int,float)) and not isinstance(g.accuracy,bool) and 0 <= g.accuracy <= 100:
            accuracies.append(g.accuracy)
        clock=_think(g)
        if clock is not None:
            timings.append(clock)
    return {'games':len(games),'result_games':len(eligible),
            'expected_points':sum(p for _,p in eligible),
            'scored_points':sum(v for v,_ in eligible),
            'score_excess':(sum(v-p for v,p in eligible)/len(eligible)) if eligible else None,
            'accuracy_games':len(accuracies),
            'median_accuracy':median(accuracies) if accuracies else None,
            'clock_games':len(timings),
            'median_think_seconds':median(timings) if timings else None,
            'first_date':_date(games[0].ended) if games else None,
            'last_date':_date(games[-1].ended) if games else None}


def context(games,selected):
    """All collected classes on the exact selected timestamps; matched clock baselines.

    Separate classes can have tiny samples, so they are descriptive and never
    scored together with the selected class. The timing comparison uses the
    MOST COMMON exact time control of selected games, avoiding the 1+0 vs 3+2
    clock-speed confound. Nearest up to 50 games in both directions are kept
    nonoverlapping with the chosen period.
    """
    if not selected:
        return None
    rows=sorted(games,key=lambda g:(g.ended,g.identity))
    begin,end=selected['first_ended'],selected['last_ended']
    primary=selected['time_class']
    selected_rows=[g for g in rows if g.time_class==primary and begin<=g.ended<=end]
    if not selected_rows:
        return None
    controls=Counter(g.time_control for g in selected_rows)
    primary_control=controls.most_common(1)[0][0]
    kind_rows={kind:[g for g in rows if g.time_class==kind and begin<=g.ended<=end]
               for kind in ('rapid','blitz','bullet')}
    across={}
    for kind,members in kind_rows.items():
        summary=_summary(members)
        summary['insufficient_sample']=len(members)<10
        summary['time_controls']=dict(Counter(g.time_control for g in members))
        across[kind]=summary
    # Window membership by global timestamp is adequate for cross-controls.
    # For the selected control, exclude only the EXACT selected identities:
    # same-second games outside this control are still not included in before/after.
    same=[g for g in rows if g.time_class==primary and g.time_control==primary_control]
    before=[g for g in same if g.ended<begin][-50:]
    during=[g for g in same if begin<=g.ended<=end]
    after=[g for g in same if g.ended>end][:50]
    # Also show nearby calendar-week activity, not a fixed 50-game period
    # that might span a year in a seldom-played control.
    week=7*24*3600
    before_week=[g for g in same if begin-week<=g.ended<begin]
    after_week=[g for g in same if end<g.ended<=end+week]
    timing={'time_class':primary,'time_control':primary_control,
        'during_control_games':len(during),
        'before50':_summary(before),'during':_summary(during),'after50':_summary(after),
        'before_week_games':len(before_week),'after_week_games':len(after_week),
        'interpretation':'Descriptive game-level median thinking time and official Chess.com '
                         'Accuracy for identical time controls. Unknown data stays missing; '
                         'no timing or Accuracy score is inferred.'}
    return {'date_start':_date(begin),'date_end':_date(end),
            'start_timestamp':begin,'end_timestamp':end,
            'selected_control':primary_control,'by_class':across,
            'timing':timing,
            'classification_role':'Context only, not an independent review-priority vote'}
