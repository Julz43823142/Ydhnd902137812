"""Predeclared ten-game Chess.com Accuracy change-point *discovery*.

Never set a Fair Play priority from a platform Accuracy number, rating or win.
Assess up to 250 most recent rated non-bullet games in fixed non-overlapping
ten-game blocks per exact time control, plus the latest rolling ten. Compare to
strictly earlier, same-control, peer-opponent, similarly rated personal games.
Missing Accuracy is missing data, never an assumed zero or perfect value.
Only a separate full-window Stockfish + Maia route may issue HIGH.
"""
from __future__ import annotations

import math
import statistics

SCHEMA='fairplay-accuracy-blocks-v1'
SCOPE=250
BLOCK=10
BASELINE_LOOKBACK=80
MIN_RECENT_ACCURACY=8
MIN_BASELINE_ACCURACY=15
MIN_PEER_GAMES=8
PLAYER_RATING_TOLERANCE=200
OPPONENT_RATING_TOLERANCE=250
MIN_RISE=15.0
MIN_RECENT_MEAN=88.0


def _accuracy(game):
    v=getattr(game,'accuracy',None)
    return float(v) if (isinstance(v,(float,int)) and not isinstance(v,bool)
                        and math.isfinite(v) and 0<=v<=100) else None


def _rating(game, attr):
    v=getattr(game,attr,None)
    return v if isinstance(v,int) and not isinstance(v,bool) and 100<=v<=4000 else None


def _matched(game, target_rating=None):
    own,other=_rating(game,'rating'),_rating(game,'opponent_rating')
    return (own is not None and other is not None
            and abs(own-other)<=OPPONENT_RATING_TOLERANCE
            and (target_rating is None or abs(own-target_rating)<=PLAYER_RATING_TOLERANCE))


def inspect(games, *, scope=SCOPE):
    """Return (identity-free public audit, internal full-window candidates).

    Fixed blocks avoid searching every possible 10-game subsequence and
    reporting only its maximum. The latest rolling ten is an explicitly
    predeclared exception, so recent cheating isn't lost at a block boundary.
    """
    by_id={}
    for g in games:
        ended=getattr(g,'ended',None)
        if (getattr(g,'rated',None) is True
                and not getattr(g,'probe_only',False)
                and getattr(g,'time_class',None) in ('rapid','blitz')
                and isinstance(getattr(g,'time_control',None),str)
                and bool(g.time_control)
                and isinstance(getattr(g,'identity',None),str)
                and isinstance(ended,(int,float)) and math.isfinite(ended)):
            by_id[g.identity]=g
    ordered=sorted(by_id.values(),key=lambda g:(g.ended,g.identity))[-scope:]
    groups={}
    for g in ordered:
        groups.setdefault((g.time_class,g.time_control),[]).append(g)
    inspected=[]
    candidates=[]
    for (kind,control),rows in sorted(groups.items()):
        if len(rows)<BLOCK:continue
        starts=list(range(0,len(rows)-BLOCK+1,BLOCK))
        if len(rows)-BLOCK not in starts:starts.append(len(rows)-BLOCK)
        for start in starts:
            current=rows[start:start+BLOCK]
            own=[_rating(g,'rating') for g in current]
            if any(x is None for x in own):
                rating_mid=None
            else:rating_mid=statistics.median(own)
            peer=sum(_matched(g,rating_mid) for g in current)
            recent_values=[v for g in current if (v:=_accuracy(g)) is not None]
            earlier=rows[max(0,start-BASELINE_LOOKBACK):start]
            baseline=[g for g in earlier if _matched(g,rating_mid)]
            prior=[v for g in baseline if (v:=_accuracy(g)) is not None]
            recent_mean=(statistics.mean(recent_values) if recent_values else None)
            older_mean=(statistics.mean(prior) if prior else None)
            difference=(recent_mean-older_mean if recent_mean is not None
                        and older_mean is not None else None)
            supported=(rating_mid is not None and peer>=MIN_PEER_GAMES
                       and len(recent_values)>=MIN_RECENT_ACCURACY
                       and len(prior)>=MIN_BASELINE_ACCURACY)
            flagged=bool(supported and recent_mean>=MIN_RECENT_MEAN
                         and difference>=MIN_RISE)
            row={
                'time_class':kind,'time_control':control,
                'window_games':BLOCK,'window_end_time':int(current[-1].ended),
                'latest_control_window':start==len(rows)-BLOCK,
                'accuracy_reported':len(recent_values),
                'mean_accuracy':round(recent_mean,2) if recent_mean is not None else None,
                'older_accuracy_reported':len(prior),
                'older_mean_accuracy':round(older_mean,2) if older_mean is not None else None,
                'accuracy_rise':round(difference,2) if difference is not None else None,
                'similar_rating_opponents':peer,
                'baseline_similar_rating_games':len(baseline),
                'comparison_supported':supported,'investigate':flagged,
                'deep_reviewed_games':sum(bool(getattr(g,'deep',False)) for g in current),
            }
            inspected.append(row)
            if flagged:
                candidates.append({'ids':tuple(g.identity for g in current),
                                   'time_class':kind,'time_control':control,
                                   'window_end_time':row['window_end_time'],
                                   'accuracy_rise':row['accuracy_rise'],
                                   'mean_accuracy':row['mean_accuracy']})
    # One historical candidate with maximal rise might otherwise crowd out
    # the present incident. Prioritize latest events, then effect magnitude.
    candidates.sort(key=lambda c:(-c['window_end_time'],-c['accuracy_rise']))
    public=sorted(inspected,key=lambda x:(
        x['investigate'],x['comparison_supported'],
        x['accuracy_rise'] if x['accuracy_rise'] is not None else -1,
        x['window_end_time']),reverse=True)
    return ({
        'schema':SCHEMA,'rated_blitz_rapid_games':len(ordered),
        'scope_max':scope,'bullet_excluded':True,
        'windows_evaluated':len(inspected),
        'supported_comparison_windows':sum(x['comparison_supported'] for x in inspected),
        'discovery_windows':sum(x['investigate'] for x in inspected),
        'best':public[0] if public else None,
        'top_windows':public[:5],
        'scoring_influence':False,
        'note':(
            'Chess.com-reported Accuracy is descriptive and sometimes missing. '
            'A fifteen-point jump over a same-control personal baseline with '
            'similar-rated opponents selects games for deeper manual-review '
            'evidence; Accuracy alone never determines HIGH.'),
    },candidates)
