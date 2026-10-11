"""Conservative HIGH manual-review route for a recent *engine-confirmed* shift.

Unlike published Chess.com Accuracy, this evaluates difficult competitive
decisions with paired fast/deep Stockfish searches, and compares the ten most
recent rated games with an earlier same-control period. Outcomes, Accuracy,
account names and Maia confidence do not score. No automatic sanctions.
"""
from __future__ import annotations

import math
import statistics

from fairplay_burst import _verified_entry
from fairplay_confirmation import paired_quality_confirmation
from fairplay_human import period_summary

WINDOW = 10
BASELINE = 20
LATEST = 50


def _counts(game, fast=False):
    metrics=(game.fast_metrics if fast else game.metrics) or {}
    row=metrics.get('human') or {}
    names=('opportunities', 'hits', 'quiet_hits', 'quality_stable_hits',
           'quality_stable_opportunities', 'paired_evaluated_opportunities')
    if any(not isinstance(row.get(k),int) or isinstance(row[k],bool)
           or row[k]<0 for k in names):
        return None
    if not (row['hits']<=row['opportunities']
            and row['quiet_hits']<=row['hits']
            and row['quality_stable_hits']<=row['quality_stable_opportunities']
            <=row['paired_evaluated_opportunities']<=row['opportunities']):
        return None
    return row


def _opponent_context(games):
    expected=[]
    for game in games:
        own,opponent=game.rating,game.opponent_rating
        if (not isinstance(own,int) or isinstance(own,bool)
                or not isinstance(opponent,int) or isinstance(opponent,bool)
                or not 100<=own<=4000 or not 100<=opponent<=4000):
            return None
        expected.append(1/(1+10**((opponent-own)/400)))
    return sum(expected)/len(expected)


def analyze(games, config):
    """Predeclared latest-ten vs previous-twenty same-control change point.

    Require the most recent ten *overall* rated games to belong to one exact
    time control. We do not scan many possible endings until one passes.
    An earlier baseline must be sufficiently observed; absent data cannot be
    treated as a low personal reference.
    """
    eligible={g.identity:g for g in games
              if getattr(g,'rated',None) is True
              and not getattr(g,'probe_only',False)
              and isinstance(getattr(g,'identity',None),str)
              and isinstance(getattr(g,'ended',None),(int,float))
              and math.isfinite(g.ended)}
    ordered=sorted(eligible.values(),key=lambda g:(g.ended,g.identity))
    latest=ordered[-LATEST:]
    recent=latest[-WINDOW:]
    checks={}
    if len(recent)!=WINDOW:
        return {'schema':'fairplay-recent-engine-shift-v1','qualified':False,
                'blockers':['Fewer than ten rated games in reviewed scope.'],
                'recent_games':len(recent),'scoring_source':'paired Stockfish gameplay only'}
    kinds={(g.time_class,g.time_control) for g in recent}
    same_control=len(kinds)==1 and recent[0].time_class in ('blitz','rapid')
    consecutive=(same_control and all(
        b.control_index==a.control_index+1
        for a,b in zip(recent,recent[1:])))
    checks['ten consecutive rated games at one blitz/rapid control']=bool(consecutive)
    controls=[g for g in ordered if g.ended<recent[0].ended
              and (g.time_class,g.time_control)==
                  (recent[0].time_class,recent[0].time_control)]
    baseline=controls[-BASELINE:]
    checks['at least twelve earlier same-control comparison games']=len(baseline)>=12
    checks['ten complete recent deep searches']=all(
        _verified_entry(g) and _counts(g) is not None
        and _counts(g,fast=True) is not None for g in recent)
    # Never omit missing older games just to make the baseline look weaker.
    # Every game used as a personal control must have authentic paired data.
    checks['every older control has complete paired deep searches']=bool(
        len(baseline)>=12 and all(
            _verified_entry(g) and _counts(g) is not None
            and _counts(g,fast=True) is not None for g in baseline))
    if not (checks['ten complete recent deep searches']
            and checks['every older control has complete paired deep searches']):
        return {'schema':'fairplay-recent-engine-shift-v1','qualified':False,
                'blockers':[key for key,val in checks.items() if not val],
                'recent_games':len(recent),'baseline_games':len(baseline),
                'scoring_source':'paired Stockfish gameplay only'}
    deep=period_summary(recent,config)
    fast=period_summary(recent,config,fast=True)
    older=period_summary(baseline,config)
    confirmation=paired_quality_confirmation(deep,0.85,coverage_floor=0.95)
    recent_rate=deep['hits']/deep['opportunities'] if deep['opportunities'] else 0
    baseline_rate=older['hits']/older['opportunities'] if older['opportunities'] else 1
    halves=[period_summary(recent[i:i+5],config) for i in (0,5)]
    halves_ok=all(
        s['opportunities']>=8 and s['hits']>=5 and s['hit_games']>=3
        for s in halves)
    fast_rate=fast['hits']/fast['opportunities'] if fast['opportunities'] else 0
    rating_expected=_opponent_context(recent)
    checks.update({
        'at least twenty recent competitive choices':deep['opportunities']>=20,
        'recent engine anomalies across seven games':
            deep['hits']>=14 and deep['hit_games']>=7,
        'both nonoverlapping five-game halves contribute':halves_ok,
        'deep anomalous choices survive fast and deep comparison':
            confirmation['coverage_passed'] and confirmation['fraction_passed']
            and confirmation['hits']>=12,
        'fast search independently detects recent anomaly':
            fast['opportunities']>=20 and fast_rate>=0.60
            and deep['hits']>=0.8*fast['hits'],
        'at least four quiet difficult anomalies':deep['quiet_hits']>=4,
        'recent engine anomaly rate at least sixty percent':recent_rate>=0.60,
        'at least thirty older competitive decisions':older['opportunities']>=30,
        'older anomaly rate at most thirty percent':baseline_rate<=0.30,
        'large recent-vs-older change in engine decisions':
            recent_rate-baseline_rate>=0.35,
        'recent opponents not overwhelmingly mismatched':
            rating_expected is not None and rating_expected<=0.80,
        'paired deep engine work complete on recent and comparison games':
            all(g.deep for g in baseline),
    })
    report={
        'schema':'fairplay-recent-engine-shift-v1',
        'qualified':all(checks.values()),
        'blockers':[name for name,passed in checks.items() if not passed],
        'checks':checks,
        'recent_games':len(recent),
        'baseline_games':len(baseline),
        'recent_opportunities':deep['opportunities'],
        'recent_anomalies':deep['hits'],
        'recent_hit_games':deep['hit_games'],
        'older_opportunities':older['opportunities'],
        'older_anomalies':older['hits'],
        'recent_hit_rate':round(recent_rate,4),
        'older_hit_rate':round(baseline_rate,4),
        'paired_coverage':round(confirmation['coverage'],4),
        'paired_quality_agreement':round(confirmation['fraction'],4),
        'opponent_expected_score':round(rating_expected,4) if rating_expected is not None else None,
        'scoring_source':'paired Stockfish gameplay only; Accuracy/wins not scored',
        'calibration_note':(
            'This is a conservative manual-review rule, not an empirically '
            'calibrated probability of misconduct. Verify on independently '
            'labelled fair-play holdouts before relying on false-positive rates.'),
    }
    return report


def integrate(result, games, config):
    report=analyze(games,config)
    coverage_ok=(not result.partial
                 and result.coverage.get('primary_engine_complete',False)
                 and result.coverage.get('required_deep_complete',False))
    feature_ok=not {'human','difficulty','recent_shift_high'} & set(config.disabled_features)
    report['coverage_gate_passed']=bool(coverage_ok)
    report['feature_gate_passed']=bool(feature_ok)
    passed=bool(report['qualified'] and coverage_ok and feature_ok)
    if not coverage_ok:report['blockers'].append('Primary/deep engine coverage incomplete')
    if not feature_ok:report['blockers'].append('Recent shift feature disabled')
    result.diagnostics['recent_engine_shift']=report
    result.diagnostics.setdefault('high_paths',{})['Recent confirmed engine shift HIGH']={
        'passed':passed,'blockers':report['blockers']}
    if passed and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
        result.priority='HIGH'
        result.deep_confirmed=True
        result.diagnostics['high_path']='Recent paired-Stockfish gameplay regime shift'
        result.diagnostics['high_blocked']=[]
        result.reasons=[
            'The latest ten rated games show a large sustained rise in difficult '
            'engine-confirmed decisions against an earlier same-control baseline.',
            'Independent five-game halves and paired full-depth searches support '
            'manual review. Published Accuracy and game results did not set HIGH.']
    return result
