"""Descriptive streak warning: Chess.com-reported Accuracy and rated outcomes.

An Accuracy streak is a DISCOVERY clue, not a cheating verdict. Chess.com
Accuracy may be absent, review-selected, and opponent-skill dependent. Thus
the detector compares each *predeclared* contiguous ten-game stretch inside
the latest fifty to STRICTLY EARLIER same-time-class games and opponent
strength, then requires separate Stockfish/Maia evidence for HIGH.

This audit never changes a priority or selects games for engine review.
"""
from __future__ import annotations

import math
import statistics

SCHEMA = 'sharkbot-accuracy-streak-audit-v1'
WINDOW_GAMES = 10
LATEST_SCOPE = 50
BASELINE_GAMES = 40
ACCURACY_THRESHOLD = 95.0


def _accuracy(game):
    value=getattr(game,'accuracy',None)
    if (isinstance(value,(int,float)) and not isinstance(value,bool)
            and math.isfinite(value) and 0<=value<=100):
        return float(value)
    return None


def _expected(game):
    own=getattr(game,'rating',None)
    other=getattr(game,'opponent_rating',None)
    if (isinstance(own,int) and not isinstance(own,bool)
            and isinstance(other,int) and not isinstance(other,bool)
            and 100<=own<=4000 and 100<=other<=4000):
        return 1/(1+10**((other-own)/400))
    return None


def accuracy_streak_audit(games):
    """Return anonymized aggregate observations; no FENs, IDs or user names."""
    by_id={}
    for game in games:
        ended=getattr(game,'ended',None)
        if (getattr(game,'rated',None) is True
                and not getattr(game,'probe_only',False)
                and isinstance(ended,(int,float)) and math.isfinite(ended)
                and isinstance(getattr(game,'identity',None),str)):
            by_id[game.identity]=game
    ordered=sorted(by_id.values(),key=lambda g:(g.ended,g.identity))
    scope=ordered[-LATEST_SCOPE:]
    windows=[]
    flagged=[]
    observed=0
    for offset in range(max(0,len(scope)-WINDOW_GAMES+1)):
        subset=scope[offset:offset+WINDOW_GAMES]
        # Avoid comparing bullet Accuracy to rapid/blitz Accuracy. Exact
        # clock-control differences inside one class are still a caveat.
        kinds={g.time_class for g in subset}
        if len(kinds)!=1 or next(iter(kinds)) not in ('rapid','blitz','bullet'):
            continue
        kind=subset[0].time_class
        seen=[_accuracy(g) for g in subset]
        available=[n for n in seen if n is not None]
        high=sum(n is not None and n>=ACCURACY_THRESHOLD for n in seen)
        wins=sum(g.result=='Win' for g in subset)
        if wins<9 or high<8:
            continue
        observed+=1
        baseline=[g for g in ordered if g.ended<subset[0].ended
                  and g.time_class==kind][-BASELINE_GAMES:]
        prior_accuracies=[_accuracy(g) for g in baseline]
        prior_accuracies=[a for a in prior_accuracies if a is not None]
        baseline_win_rate=(sum(g.result=='Win' for g in baseline)/len(baseline)
                           if baseline else None)
        baseline_accuracy=(statistics.mean(prior_accuracies)
                           if prior_accuracies else None)
        current_accuracy=statistics.mean(available)
        expected=[_expected(g) for g in subset]
        valid=[p for p in expected if p is not None]
        predicted_points=sum(valid) if len(valid)==WINDOW_GAMES else None
        rating_known=len(valid)
        weaker=sum((p is not None and p>=.90) for p in expected)
        # Require both a well-observed older player baseline and comparable
        # opposition. A run of automatic wins against dramatically weaker
        # players is not independently anomalous.
        baseline_supported=(len(baseline)>=20 and len(prior_accuracies)>=10)
        contrast=(current_accuracy-baseline_accuracy
                  if baseline_accuracy is not None else None)
        comparable_opposition=(rating_known==WINDOW_GAMES
                               and predicted_points<=7.5 and weaker<=2)
        increased=(baseline_supported and contrast is not None
                   and contrast>=8
                   and baseline_win_rate is not None
                   and wins/WINDOW_GAMES-baseline_win_rate>=.15)
        alert=bool(increased and comparable_opposition)
        deep=sum(bool(getattr(g,'deep',False)) for g in subset)
        hard=sum(int((getattr(g,'metrics',None) or {}).get('human',{})
                     .get('opportunities',0) or 0)
                 for g in subset if getattr(g,'deep',False))
        hits=sum(int((getattr(g,'metrics',None) or {}).get('human',{})
                     .get('hits',0) or 0)
                 for g in subset if getattr(g,'deep',False))
        row={
            'time_class':kind,
            'window_games':WINDOW_GAMES,
            'win_games':wins,
            'accuracy_reported_games':len(available),
            'accuracy_95_plus_games':high,
            'accuracy_mean':round(current_accuracy,2),
            'older_same_class_games':len(baseline),
            'older_accuracy_reported_games':len(prior_accuracies),
            'older_accuracy_mean':round(baseline_accuracy,2)
                if baseline_accuracy is not None else None,
            'accuracy_change_points':round(contrast,2) if contrast is not None else None,
            'older_win_rate':round(baseline_win_rate,3)
                if baseline_win_rate is not None else None,
            'opponent_ratings_known':rating_known,
            'elo_expected_points':round(predicted_points,2)
                if predicted_points is not None else None,
            'very_weak_opponent_games':weaker,
            'baseline_sufficient':baseline_supported,
            'opponent_context_sufficient':comparable_opposition,
            'deep_reviewed_games':deep,
            'deep_hard_opportunities':hard,
            'deep_human_anomaly_hits':hits,
            'followup_alert':alert,
            'end_time':int(subset[-1].ended),
        }
        windows.append(row)
        if alert:
            flagged.append(row)
    best=max(flagged or windows,
             key=lambda r:(r['followup_alert'],r['accuracy_95_plus_games'],
                           r['win_games'],r['end_time']),default=None)
    return {
        'schema':SCHEMA,
        'latest_rated_scope':len(scope),
        'last_50_accuracy_reported':sum(_accuracy(g) is not None for g in scope),
        'last_10_accuracy_reported':sum(_accuracy(g) is not None for g in scope[-10:]),
        'window_games':WINDOW_GAMES,
        'high_win_accuracy_windows_observed':observed,
        'supported_followup_windows':len(flagged),
        'followup_alert':bool(flagged),
        'best':best,
        'scoring_influence':False,
        'note':(
            'A reported Accuracy streak is only a prompt to inspect already '
            'deep-reviewed competitive positions. The rating-adjusted '
            'outcome model and historical Accuracy reporting are incomplete '
            'and selection-biased. This does NOT establish cheating, and '
            'never directly changes LOW, MODERATE, HIGH or VERY HIGH.'),
    }
