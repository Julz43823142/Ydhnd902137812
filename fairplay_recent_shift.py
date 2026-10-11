"""Manual HIGH review for a recent Accuracy-discovered ten-game episode.

The discovery window must have a strictly older, comparable-rated personal
baseline, but Chess.com Accuracy and outcomes DO NOT score as gameplay
evidence. Every game in the window (good and bad moves) needs depth-confirmed
paired Stockfish evidence and separately evaluated Maia-3 counterfactuals.
The sources share Stockfish alternatives and are not independent misconduct
probabilities. Uncalibrated: NEVER use for automatic sanctions.
"""
from __future__ import annotations

from fairplay_burst import _verified_entry
from fairplay_confirmation import paired_quality_confirmation
from fairplay_human import period_summary
from fairplay_policy import summarize as maia_summary

SCHEMA='fairplay-recent-engine-shift-v2'
MAX_WINDOWS=2


def _valid_human_rows(games):
    for g in games:
        if not _verified_entry(g):
            return False
        for source in (g.fast_metrics,g.metrics):
            h=(source or {}).get('human') or {}
            for key in ('opportunities','hits','quiet_hits','quality_stable_hits',
                        'quality_stable_opportunities','paired_evaluated_opportunities'):
                if not isinstance(h.get(key),int) or isinstance(h[key],bool) or h[key]<0:
                    return False
            if not (h['hits']<=h['opportunities']
                    and h['quiet_hits']<=h['hits']
                    and h['quality_stable_hits']<=h['quality_stable_opportunities']
                    <=h['paired_evaluated_opportunities']<=h['opportunities']):
                return False
    return True


def evidence(games,config):
    """Full window scoring. All gates must pass; no partial/unknown positives."""
    checks={}
    if len(games)!=10 or not _valid_human_rows(games):
        return {'passed':False,'blockers':['Ten complete paired deep Stockfish games required.']}
    games=sorted(games,key=lambda g:(g.ended,g.identity))
    checks['same exact rapid/blitz control and consecutive rated games']=(
        len({(g.time_class,g.time_control) for g in games})==1
        and games[0].time_class in ('rapid','blitz')
        and all(b.control_index==a.control_index+1 for a,b in zip(games,games[1:])))
    deep=period_summary(games,config)
    fast=period_summary(games,config,fast=True)
    paired=paired_quality_confirmation(deep,.85,coverage_floor=.95)
    halves=[period_summary(games[i:i+5],config) for i in (0,5)]
    checks.update({
        'twenty competitive deep engine opportunities':deep['opportunities']>=20,
        'fourteen confirmed anomalous moves across seven games':(
            deep['hits']>=14 and deep['hit_games']>=7),
        'both five-game halves contain anomalous play':all(
            half['opportunities']>=8 and half['hits']>=6
            and half['hit_games']>=3 for half in halves),
        'twelve fast engine anomalies corroborate deep choices':(
            fast['opportunities']>=20 and fast['hits']>=12
            and deep['hits']>=.8*fast['hits']),
        'paired fast/deep searches confirm objective move quality':(
            paired['coverage_passed'] and paired['fraction_passed']
            and paired['hits']>=12),
        'quiet difficult choices are spread across games':(
            deep['quiet_hits']>=5
            and sum((g.metrics['human']['quiet_hits']>=1) for g in games)>=4),
        'sizeable raw quality excess, not just top-move matching':(
            deep['quality_excess']>=.12 and deep['information']>=.16
            and deep['anomaly_strength']>=.27),
    })
    # A missing Maia probability, counterfactual root search, or deep/fast
    # stability observation makes game_summary noncontributing. It cannot be
    # replaced by high Accuracy, wins, or a guessed model response.
    learned=maia_summary(games)
    learned_halves=[maia_summary(games[i:i+5]) for i in (0,5)]
    checks.update({
        'Maia-3 identifies six distributed anomalous contributor games':(
            learned['contributors']>=6 and learned['contributor_weight']>=5),
        'Maia-3 compares twenty game-capped difficult decisions':(
            learned['effective_positions']>=20),
        'Maia-3 signed human-policy quality discrepancy retained':(
            learned['signed_excess']>=.16 and learned['information']>=.10),
        'Maia-3 comparison stable under paired deep Stockfish searches':(
            learned['positions']>=20
            and learned['stable']>=.70*learned['positions']),
        'both halves independently contain Maia-3 evidence':all(
            half['contributors']>=2
            and half['effective_positions']>=7
            and half['signed_excess']>=.10
            and half['information']>=.07
            for half in learned_halves),
    })
    return {
        'passed':all(checks.values()),
        'blockers':[name for name,ok in checks.items() if not ok],
        'checks':checks,
        'stockfish_opportunities':deep['opportunities'],
        'stockfish_anomalous_moves':deep['hits'],
        'stockfish_hit_games':deep['hit_games'],
        'stockfish_quality_stable_hits':paired['hits'],
        'maia_contributor_games':learned['contributors'],
        'maia_effective_positions':round(learned['effective_positions'],2),
        'maia_signed_excess':round(learned['signed_excess'],4),
    }


def integrate(result,games,config,accuracy_candidates=()):
    """Only the two newest predeclared baseline-supported windows can score."""
    by_id={g.identity:g for g in games}
    candidates=list(accuracy_candidates)[:MAX_WINDOWS]
    attempts=[]
    coverage_ok=(not result.partial
                 and result.coverage.get('primary_engine_complete',False)
                 and result.coverage.get('required_deep_complete',False))
    feature_ok=not {'human','neural','difficulty','recent_shift_high'} & set(config.disabled_features)
    for candidate in candidates:
        ids=tuple(candidate['ids'])
        window=[by_id[k] for k in ids if k in by_id]
        if len(window)!=10:
            proof={'passed':False,
                   'blockers':['Selected ten-game Accuracy discovery was not fully deep reviewed.']}
        else:
            proof=evidence(window,config)
        attempts.append({
            'time_class':candidate['time_class'],
            'window_end_time':candidate['window_end_time'],
            'mean_accuracy':candidate['mean_accuracy'],
            'accuracy_rise':candidate['accuracy_rise'],
            'deep_reviewed':len(window),
            **proof,
        })
    qualified=bool(coverage_ok and feature_ok
                   and any(r['passed'] for r in attempts))
    if qualified and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
        result.priority='HIGH'
        result.deep_confirmed=True
        result.diagnostics['high_path']='Accuracy-discovered, Stockfish+Maia-confirmed recent gameplay shift'
        result.diagnostics['high_blocked']=[]
        result.reasons=[
            'A ten-game rated blitz/rapid Accuracy shift against a comparable '
            'personal baseline led to a full-game investigation.',
            'Difficult competitive decisions across both halves were corroborated '
            'by paired deep Stockfish and Maia-3 human-policy comparisons. '
            'HIGH requires manual game review; this is not proof of cheating.']
    report={
        'schema':SCHEMA,'qualified':qualified,
        'windows_attempted':len(attempts),
        'windows_confirmed':sum(r['passed'] for r in attempts),
        'attempts':attempts,
        'coverage_gate_passed':bool(coverage_ok),
        'feature_gate_passed':bool(feature_ok),
        'source':'Chess.com Accuracy only selects windows; Stockfish+Maia issue review priority',
        'caveat':'Uncalibrated correlated evidence; no automated sanction or misconduct probability.',
    }
    result.diagnostics['recent_engine_shift']=report
    result.diagnostics.setdefault('high_paths',{})['Accuracy-shift + Stockfish + Maia HIGH']={
        'passed':qualified,
        'blockers':(
            ['Insufficient engine coverage'] if not coverage_ok else []
        )+(['Required feature disabled'] if not feature_ok else [])+
        (attempts[0]['blockers'] if attempts and not qualified else
         ['No fully corroborated ten-game discovery.'] if not qualified else []),
    }
    return result
