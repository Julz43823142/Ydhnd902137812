"""Descriptive earlier-to-recent comparisons, never independent HIGH votes."""
from collections import defaultdict
from statistics import median
from fairplay_outcome_study import expected_points, number, CONFIG as REFERENCE_CONFIG


def clock_median(game):
    values=[d.think for d in game.decisions if d.clock_valid and number(d.think) is not None and d.think>=0]
    return median(values) if values else None


def summary(games):
    valid=[(g,expected_points(g.rating,g.opponent_rating)) for g in games]
    valid=[(g,p) for g,p in valid if p is not None and g.score in (0,.5,1)]
    scores=[g.accuracy for g in games if number(g.accuracy) is not None]
    clocks=[value for g in games if (value:=clock_median(g)) is not None]
    return {'games':len(games),'result_games':len(valid),
        'score_excess':sum(g.score-p for g,p in valid)/len(valid) if valid else None,
        'accuracy_games':len(scores),'accuracy_median':median(scores) if scores else None,
        'clock_games':len(clocks),'think_median':median(clocks) if clocks else None}


def matches(a,b):
    ratings=(a.rating,a.opponent_rating,b.rating,b.opponent_rating)
    return (all(number(v) is not None for v in ratings)
        and abs(a.rating-b.rating)<=REFERENCE_CONFIG.rating_tolerance
        and abs(a.opponent_rating-b.opponent_rating)<=REFERENCE_CONFIG.opponent_tolerance
        and abs((a.rating-a.opponent_rating)-(b.rating-b.opponent_rating))<=REFERENCE_CONFIG.gap_tolerance)


def changes(games,priority_games):
    """Disjoint prior reference and recent block; exact class/base/increment.

    Compare at most the most recent 20 games/control from the priority sample
    with up to 200 earlier rating-matched games. Exclude all recent-block games
    from the reference. Missing Accuracy/clocks never become zero or a signal.
    """
    games=sorted(games,key=lambda g:(g.ended,g.identity))
    recent_ids={g.identity for g in games[-priority_games:]}
    grouped=defaultdict(list)
    for game in games:grouped[(game.time_class,game.time_control)].append(game)
    output=[]
    for (kind,control),group in sorted(grouped.items()):
        recent=[g for g in group if g.identity in recent_ids][-20:]
        if len(recent)<10:continue
        earlier=[g for g in group if (g.ended,g.identity)<(recent[0].ended,recent[0].identity)]
        baseline=[g for g in earlier if any(matches(g,r) for r in recent)][-200:]
        before,after=summary(baseline),summary(recent)
        covered=(before['result_games']>=20 and before['result_games']>=.8*len(baseline)
                 and after['result_games']>=.8*len(recent))
        delta=(after['score_excess']-before['score_excess'] if covered else None)
        output.append({'time_class':kind,'time_control':control,'baseline':before,'recent':after,
            'earlier_range':(baseline[0].ended,baseline[-1].ended) if baseline else None,
            'recent_range':(recent[0].ended,recent[-1].ended) if recent else None,
            'results_comparable':covered,'score_excess_change':delta,
            'results_increase':bool(delta is not None and delta>=.15 and after['score_excess']>=.20),
            'support_only':True})
    return output
