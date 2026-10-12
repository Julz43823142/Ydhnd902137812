"""Descriptive streak warning: Chess.com-reported Accuracy and rated outcomes.

An Accuracy streak is a DISCOVERY clue, not a cheating verdict. Chess.com
Accuracy may be absent, review-selected, and opponent-skill dependent. The
descriptive audit compares each predeclared contiguous ten-game stretch
inside the latest fifty to strictly earlier same-time-class games and opponent
strength. A separate owner-defined triage policy can mark a large repeated
Accuracy jump HIGH for manual inspection without engine confirmation; its
report must make that distinction explicit.

The descriptive audit itself does not change priority or engine selection.
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


def low_rating_accuracy_pairs(scope, history=None):
    """Read-only 90+ Accuracy *consecutive* game pairs around 500 Elo.

    The pair itself is merely observed. Calling it supported review context
    also requires real move opportunities, an earlier quality reference, and
    opposition within a comparable rating range. No probabilistic cheating
    interpretation and no independent contribution to HIGH.
    """
    observations=[]
    supported=[]
    for first,second in zip(scope,scope[1:]):
        if (first.time_class != second.time_class
                or first.time_class not in ('rapid','blitz','bullet')):
            continue
        if not all(_accuracy(g) is not None and _accuracy(g)>=90
                   for g in (first,second)):
            continue
        if not all(isinstance(g.rating,int) and not isinstance(g.rating,bool)
                   and 300 <= g.rating <= 600 for g in (first,second)):
            continue
        def informative(g):
            metrics=(getattr(g,'fast_metrics',None)
                     or getattr(g,'metrics',None) or {})
            number=metrics.get('decisions',0)
            return isinstance(number,int) and not isinstance(number,bool) and number>=8
        enough_decisions=all(informative(g) for g in (first,second))
        comparable_opposition=all(
            _expected(g) is not None
            and g.opponent_rating >= g.rating-300
            for g in (first,second))
        preceding=[g for g in (history if history is not None else scope)
                   if g.ended<first.ended and g.time_class==first.time_class][-20:]
        prior=[_accuracy(g) for g in preceding]
        prior=[x for x in prior if x is not None]
        older_mean=statistics.mean(prior) if len(prior)>=8 else None
        row={
            'time_class':first.time_class,
            'older_reported_accuracy_games':len(prior),
            'older_accuracy_mean':round(older_mean,2)
                if older_mean is not None else None,
            'games':2,'accuracy_90_plus_games':2,
            'mean_accuracy':round((_accuracy(first)+_accuracy(second))/2,2),
            'deep_reviewed_games':sum(bool(getattr(g,'deep',False))
                                      for g in (first,second)),
            'both_games_have_eight_useful_decisions':enough_decisions,
            'opponent_strength_comparable':comparable_opposition,
            'end_time':int(second.ended),
        }
        observations.append(row)
        # A pair may be flagged for *manual inspection* without a historical
        # baseline; that uncertainty is shown, never interpreted as proof.
        if enough_decisions and comparable_opposition and row['deep_reviewed_games']==2:
            supported.append(row)
    return {
        'observed_pairs':len(observations),
        'review_context_pairs':len(supported),
        'review_context_flag':bool(supported),
        'best':max(supported or observations,
                   key=lambda r:(r['deep_reviewed_games'],
                                 r['mean_accuracy'],r['end_time']),default=None),
        'scoring_influence':False,
        'interpretation':(
            'Two official 90+ Accuracy reports near 500 Elo are unusual enough '
            'to inspect, but their base rate is unknown and position difficulty '
            'varies. This cannot by itself elevate priority.'),
    }


def accuracy_opponent_context(games):
    """Describe recent published Accuracy with same-class, earlier opposition.

    Published scores are sparse and selected by Chess.com; compare opponent
    ratings only for games with a published score so missing reports do not
    distort either median. A separate policy can use an extreme, sustained
    change to prioritize manual inspection, never to infer misconduct.
    """
    rows=[]
    for kind in ('rapid','blitz'):
        group=[g for g in games if g.time_class==kind]
        recent=group[-20:]
        older=group[:-20][-40:]
        def reported(sample):
            return [g for g in sample if _accuracy(g) is not None]
        a,b=reported(recent),reported(older)
        if len(a)<5 or len(b)<3:
            continue
        def opponent_median(sample):
            ratings=[g.opponent_rating for g in sample
                     if isinstance(getattr(g,'opponent_rating',None),int)
                     and not isinstance(g.opponent_rating,bool)
                     and 100<=g.opponent_rating<=4000]
            return round(statistics.median(ratings)) if len(ratings)>=3 else None
        current_opponent=opponent_median(a)
        earlier_opponent=opponent_median(b)
        rows.append({
            'time_class':kind,
            'recent_games':len(recent),
            'earlier_games':len(older),
            'recent_reported':len(a),
            'earlier_reported':len(b),
            'recent_accuracy_median':round(statistics.median(_accuracy(g) for g in a),1),
            'earlier_accuracy_median':round(statistics.median(_accuracy(g) for g in b),1),
            'recent_90_plus':sum(_accuracy(g)>=90 for g in a),
            'recent_opponent_median':current_opponent,
            'earlier_opponent_median':earlier_opponent,
            'opponent_shift_large':(current_opponent is not None
                                    and earlier_opponent is not None
                                    and abs(current_opponent-earlier_opponent)>=250),
        })
    return rows


def prioritize_accuracy_surge(result, games, config=None):
    """Owner policy: reserve HIGH human review for a sustained score jump.

    This route is deliberately distinct from engine-evidence HIGH. It never
    asserts engine use, never reaches VERY HIGH, and states opponent and
    missing-score caveats. It applies identically to every account.
    """
    rated=sorted((g for g in games if getattr(g,'rated',None) is True
                  and not getattr(g,'probe_only',False)
                  and isinstance(getattr(g,'ended',None),(int,float))
                  and math.isfinite(g.ended)),
                 key=lambda g:(g.ended,g.identity))
    rows=accuracy_opponent_context(rated)
    disabled=set(getattr(config,'disabled_features',()) or ())
    qualifying=[r for r in rows if (
        r['recent_games']==20 and r['recent_reported']>=10
        and r['earlier_games']>=20 and r['earlier_reported']>=3
        and r['recent_90_plus']>=8
        and r['recent_accuracy_median']>=90
        and r['earlier_accuracy_median']<=65
        and r['recent_accuracy_median']-r['earlier_accuracy_median']>=30)]
    if {'accuracy','accuracy_surge'} & disabled:
        qualifying=[]
    result.diagnostics['accuracy_surge_priority']={
        'policy':'manual-review triage only',
        'passed':bool(qualifying),
        'time_classes':[r['time_class'] for r in qualifying],
        'opponent_change_present':any(r['opponent_shift_large'] for r in qualifying),
        'engine_confirmation_required':False,
        'misconduct_inference':False,
    }
    result.diagnostics.setdefault('high_paths',{})['Published Accuracy surge — manual review']={
        'passed':bool(qualifying),
        'blockers':([] if qualifying else
                    ['Disabled in this research run.'] if {'accuracy','accuracy_surge'} & disabled
                    else ['No sustained same-class published Accuracy surge.']),
    }
    if qualifying and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
        row=qualifying[0]
        result.priority='HIGH'
        result.diagnostics['high_path']='Published Accuracy surge — manual review'
        caveat=(' Opponent Elo changed substantially.'
                if row['opponent_shift_large'] else '')
        if row['earlier_reported']<5:
            caveat+=' Earlier scores are sparse.'
        result.reasons.insert(0,
            f"Published {row['time_class'].title()} Accuracy: "
            f"{row['earlier_accuracy_median']:.1f}% ({row['earlier_reported']} earlier) "
            f"→ {row['recent_accuracy_median']:.1f}% "
            f"({row['recent_reported']} recent). HIGH requests manual review, "
            f"not proof. Stockfish/Maia did not confirm this path.{caveat}")
    return result


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
        'same_class_accuracy_context':accuracy_opponent_context(ordered),
        'low_rating_90_plus_pairs':low_rating_accuracy_pairs(scope, ordered),
        'latest_rated_scope':len(scope),
        'last_50_accuracy_reported':sum(_accuracy(g) is not None for g in scope),
        'last_10_accuracy_reported':sum(_accuracy(g) is not None for g in scope[-10:]),
        'last_10_accuracy_mean':round(statistics.mean(recent_known),2)
            if (recent_known:=[_accuracy(g) for g in scope[-10:]
                              if _accuracy(g) is not None]) else None,
        'last_10_accuracy_95_plus':sum(
            _accuracy(g) is not None and _accuracy(g)>=95 for g in scope[-10:]),
        'last_20_accuracy_reported':sum(
            _accuracy(g) is not None for g in scope[-20:]),
        'last_20_accuracy_95_plus':sum(
            _accuracy(g) is not None and _accuracy(g)>=95 for g in scope[-20:]),
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
            'does not directly change priority. A separate published-score '
            'surge policy may prioritize manual review without engine proof.'),
    }


def unrated_accuracy_audit(games):
    """Descriptive last-ten *unrated* nonbullet Accuracy visibility.

    An unrated 95% game must not disappear from the report simply because it
    cannot be combined with rated outcomes. Scores are public observations,
    never an automatic HIGH path; the paired ten-window scorer is separate.
    """
    unique={}
    for game in games:
        if (getattr(game,'rated',None) is False
                and not getattr(game,'probe_only',False)
                and getattr(game,'time_class',None) in ('rapid','blitz')
                and isinstance(getattr(game,'identity',None),str)
                and isinstance(getattr(game,'ended',None),(int,float))
                and math.isfinite(game.ended)):
            unique[game.identity]=game
    ordered=sorted(unique.values(),key=lambda g:(g.ended,g.identity))
    recent=ordered[-10:]
    known=[_accuracy(g) for g in recent]
    known=[n for n in known if n is not None]
    return {
        'schema':'sharkbot-unrated-accuracy-observation-v1',
        'recent_unrated_blitz_rapid_games':len(recent),
        'reported_accuracy_games':len(known),
        'accuracy_95_plus_games':sum(n>=95 for n in known),
        'reported_accuracy_mean':round(statistics.mean(known),2) if known else None,
        'recent_unrated_win_games':sum(g.result=='Win' for g in recent),
        'scoring_influence':False,
        'warning':('Unrated Chess.com Accuracy is an observation, not proof. '
                   'Missing Accuracy is not treated as zero; Stockfish and Maia '
                   'must independently confirm a suspicious move pattern.'),
    }
