"""Persistent multi-family evidence without requiring a personal regime change.

A steady pattern can be worth human review even when the entire observed
history follows that pattern. This route considers COMPLETE contiguous runs,
not a ranked collection of good games. It needs engine opportunities throughout
both chronological halves, comparable delays on easy and hard decisions in
both halves, and unusually strong results after the existing underrating margin.
Deep confirmation is mandatory. The route can establish HIGH, never VERY HIGH.

Wilson bounds and the outcome bound are conservative heuristics here: moves,
games and their outcomes are not independent trials, and skill expectations are
not a fitted human population model. Neither is a misconduct probability.
"""
import copy
import math
import statistics

from fairplay_config import CONFIG
from fairplay_calibration import lower_bound, strength_expectations
from fairplay_results import result_support
from fairplay_timing import decision_trivial_kind
from fairplay_local_timing import clock_delay_evidence


def _clock_copy(game, *, fast):
    """Classify timing opportunities at ONE engine budget, including misses."""
    clone=copy.copy(game)
    clone.decisions=[]
    for decision in game.decisions:
        item=copy.copy(decision)
        item.metrics=(decision.fast_engine or decision.metrics) if fast else decision.metrics
        clone.decisions.append(item)
    return clone


def clock_replication(games, config=CONFIG, *, fast=True):
    """Easy/normal/critical delays must coexist inside the same games.

    Pooling unrelated games can otherwise manufacture category overlap. Keep
    near-instant moves and misses; only invalid clocks, openings and severe
    time trouble are removed by the existing parsing rules.
    """
    copies=[_clock_copy(game,fast=fast) for game in games]
    shared=[]
    for game in copies:
        categories=set()
        for decision in game.decisions:
            if (decision.phase=='opening' or not decision.clock_valid
                    or decision.think is None or not math.isfinite(decision.think)
                    or decision.think<0):continue
            categories.add('trivial' if decision_trivial_kind(decision)
                           else 'critical' if decision.metrics.get('critical') else 'normal')
        if len(categories)==3:shared.append(game.identity)
    profile=clock_delay_evidence(copies,config)
    required=max(config.convergence_shared_games,math.ceil(len(games)*config.pooled_opportunity_fraction))
    absolute=bool(profile['raw_elevated'] and len(shared)>=required)
    local=profile['normalized']['elevated']
    return {'supported':absolute or local,
            'methods':(['absolute'] if absolute else [])+(['within-game'] if local else []),
            'shared_games':len(shared),'required_shared_games':required,
            'profile':profile}


def opportunity_evidence(games, config=CONFIG, *, fast=True, half=False):
    """Count observed opportunities, not an arbitrary minimum in every game.

    A game with no critical choice is not a missed choice; an actual miss always
    stays in the denominator. A large, distributed pool is still required.
    """
    from fairplay_clusters import summary, rows_for
    metrics=summary(games,fast)
    rows=rows_for(games,fast)
    contributors=sum(row.get('critical',0)>0 for row in rows)
    ratings=[g.rating for g in games if g.rating is not None]
    rating=statistics.median(ratings) if ratings else None
    expectation=strength_expectations(rating,config)[2]
    bound=lower_bound(metrics['critical_top1'],metrics['critical'],config.rate_lower_bound_z)
    required_lower=max(config.convergence_half_lower if half else config.convergence_critical_lower,
                       expectation+config.convergence_strength_excess)
    required_positions=config.convergence_half_critical if half else config.convergence_critical
    required_games=max(config.convergence_half_contributors if half else config.convergence_contributors,
                       math.ceil(len(games)*config.pooled_opportunity_fraction))
    enough=(metrics['critical']>=required_positions and contributors>=required_games)
    return {'supported':bool(enough and bound>=required_lower),
            'metrics':metrics,'contributors':contributors,'required_contributors':required_games,
            'lower_bound':bound,'required_lower':required_lower,'strength_expectation':expectation,
            'enough_opportunities':enough}


def complete_runs(games, config=CONFIG):
    """No rated/control mixing, probes, unknown clocks or unscanned gaps."""
    from fairplay_clusters import buckets, contiguous
    available=[g for g in games if g.rated is True and not g.probe_only
               and g.time_class in ('rapid','blitz') and g.time_control
               and g.control_index is not None
               and 'decisions' in (g.fast_metrics or g.metrics)]
    # Repeated input references cannot multiply opportunities or outcome evidence.
    unique={}
    for game in sorted(available,key=lambda g:(g.ended,g.identity)):
        unique.setdefault(game.identity,game)
    available=list(unique.values())
    runs=[]
    for group in buckets(available).values():
        run=[]
        for game in group:
            if run and not contiguous([run[-1],game]):
                if len(run)>=config.convergence_games:runs.append(run)
                run=[]
            run.append(game)
        if len(run)>=config.convergence_games:runs.append(run)
    return runs


def discover_convergence(games, config=CONFIG):
    """Deterministic bounded discovery from equal-budget fast measurements.

    The result-tail cutoff is adjusted by ALL eligible complete runs examined,
    including rejected ones. Overlapping windows are deliberately not searched
    here. Replication halves are chronological, not chosen by move quality.
    """
    from fairplay_clusters import group_record
    runs=complete_runs(games,config)
    examined=len(runs)
    candidates=[]
    for group in runs:
        whole=opportunity_evidence(group,config)
        m=whole['metrics']
        if m['decisions']<config.convergence_decisions or not whole['enough_opportunities']:continue
        cut=len(group)//2
        halves=[group[:cut],group[cut:]]
        engine_halves=[opportunity_evidence(part,config,half=True) for part in halves]
        clocks=clock_replication(group,config)
        clock_halves=[clock_replication(part,config) for part in halves]
        outcomes=result_support(group,config)
        bound=outcomes.get('bound')
        adjusted=min(1.0,bound*max(1,examined)) if bound is not None else None
        result_ok=adjusted is not None and adjusted<=config.convergence_result_bound
        coverage_ok=len(group)>=config.convergence_games and m['decisions']>=config.convergence_decisions
        engine_ok=whole['supported'] and all(row['supported'] for row in engine_halves)
        common_methods=set(clocks['methods']).intersection(*(set(row['methods']) for row in clock_halves))
        timing_method='absolute' if 'absolute' in common_methods else 'within-game' if 'within-game' in common_methods else None
        timing_ok=timing_method is not None
        blockers=[]
        if not coverage_ok:blockers.append('Insufficient complete-period game/decision coverage.')
        if not engine_ok:blockers.append('Critical precision is not adequately repeated in both chronological halves.')
        if not timing_ok:blockers.append('Comparable delayed easy/hard decisions are not repeated in both halves and the same games.')
        if not result_ok:blockers.append('Results do not clear the conservative opponent-strength and searched-period checks.')
        record=group_record(group,'complete period',config,fast=True)
        record['convergence']={'candidate':not blockers,'examined_periods':examined,
            'opportunities':whole,'engine_halves':engine_halves,
            'clocks':clocks,'clock_halves':clock_halves,'timing_method':timing_method,'results':outcomes,
            'adjusted_result_bound':adjusted,'blockers':blockers}
        candidates.append(record)
    candidates.sort(key=lambda row:(row['convergence']['candidate'],
        -len(row['convergence']['blockers']),row['metrics']['critical'],row['end']),reverse=True)
    return {'examined_periods':examined,'candidate':next((r for r in candidates if r['convergence']['candidate']),None),
            'closest':candidates[0] if candidates else None}


def confirm_convergence(candidate, games, config=CONFIG, *, partial=False, confidence='LOW'):
    """Confirm the SAME period, with paired fast/deep critical opportunities.

    Selected games may not borrow engine data from outside the period. Their
    timing is independently reclassified at the deeper budget, and must remain
    compatible with the discovery signal. A failed deep pass can only reduce
    priority. Stable behavior alone never supplies primary engine evidence.
    """
    from fairplay_clusters import summary
    if not candidate:return {'qualified':False,'deep_confirmed':False,'blockers':[]}
    ids=set(candidate['ids'])
    selected=list({g.identity:g for g in games
                   if g.identity in ids and g.deep and not g.probe_only and g.rated is True}.values())
    deep=summary(selected)
    fast=summary(selected,True)
    lower=lower_bound(deep['critical_top1'],deep['critical'],config.rate_lower_bound_z)
    fast_lower=lower_bound(fast['critical_top1'],fast['critical'],config.rate_lower_bound_z)
    critical_contributors=sum(g.metrics.get('critical',0)>0 for g in selected)
    measured=all(d.metrics.get('nodes',0)>=config.deep_nodes
                 and d.fast_engine.get('nodes',0)==config.fast_nodes
                 for g in selected for d in g.decisions if d.metrics.get('useful'))
    middle=len(candidate['ids'])//2
    deep_halves=[]
    for half_ids in (set(candidate['ids'][:middle]),set(candidate['ids'][middle:])):
        part=[g for g in selected if g.identity in half_ids]
        metrics=summary(part)
        bound=lower_bound(metrics['critical_top1'],metrics['critical'],config.rate_lower_bound_z)
        deep_halves.append({'games':len(part),'critical':metrics['critical'],
            'lower_bound':bound,'supported':len(part)>=config.convergence_deep_half_games
                and metrics['critical']>=config.convergence_deep_half_critical
                and bound>=config.convergence_deep_half_lower})
    retention=(deep['critical_top1']/fast['critical_top1']
               if fast['critical_top1'] and deep['critical_top1'] is not None else 0)
    engine_ok=(measured and all(row['supported'] for row in deep_halves)
               and len(selected)>=config.convergence_deep_games
               and deep['decisions']>=config.min_deep_decisions
               and deep['critical']>=config.min_deep_critical
               and critical_contributors>=config.convergence_deep_contributors
               and lower>=config.convergence_deep_lower
               and fast_lower>=config.convergence_deep_lower
               and retention>=config.convergence_deep_retention)
    clocks=clock_replication(selected,config,fast=False)
    blockers=list(candidate['convergence']['blockers'])
    if not candidate['convergence']['candidate']:blockers.append('Discovery did not establish convergent evidence.')
    if not engine_ok:blockers.append('The same-period critical signal lacks adequate paired deep confirmation.')
    method=candidate['convergence'].get('timing_method','absolute')
    clock_confirmed=method in clocks['methods']
    if not clock_confirmed:blockers.append('The same delayed easy/hard comparison method did not survive deep opportunity classification.')
    if partial:blockers.append('Incomplete analysis cannot establish this convergent HIGH route.')
    if confidence=='LOW':blockers.append('Low data confidence cannot establish this convergent HIGH route.')
    return {'qualified':not blockers,'deep_confirmed':bool(engine_ok and clock_confirmed),
            'games':len(selected),'metrics':deep,'paired_fast':fast,'critical_contributors':critical_contributors,
            'lower_bound':lower,'paired_fast_lower':fast_lower,'retention':retention,
            'clocks':clocks,'deep_halves':deep_halves,'measured':measured,'blockers':blockers}


def integrate_review(result, games, config=CONFIG):
    """Add a distinct, explainable HIGH route while preserving legacy decisions."""
    discovery=result.clusters.get('convergence',{})
    candidate=discovery.get('candidate')
    confirmation=confirm_convergence(candidate,games,config,
        partial=result.partial,confidence=result.confidence)
    if candidate and result.priority=='INSUFFICIENT DATA':
        confirmation['qualified']=False
        confirmation['blockers'].append('The minimum meaningful-game sample is not available.')
    discovery['confirmation']=confirmation
    discovery['raised_priority']=False
    result.clusters['convergence']=discovery
    result.diagnostics['convergent_qualified']=confirmation['qualified']
    if confirmation['qualified'] and result.priority in ('LOW','MODERATE'):
        result.priority='HIGH'
        result.deep_confirmed=True
        discovery['raised_priority']=True
        result.diagnostics.update(high_path='Complete-period engine, clock and result corroboration',
            high_blocked=[],independent_support=True,same_period_support=True)
        # Explain the route that actually established this priority. Legacy
        # statements about another period's failed gates would be misleading.
        result.reasons=[
            f'A complete {len(candidate["ids"])}-game same-control period combines repeated critical precision, '
            'similar delayed easy/hard decisions and results beyond conservative Elo expectations; '
            'both chronological halves and the deep review support this pattern.',
            'Actual critical misses and near-instant moves remain in the comparisons; '
            'a ranked set of best games cannot establish this route.',
            'Habit, lag, input delay, underrating and legitimate improvement remain possible explanations. '
            'This combination warrants human review, not a misconduct conclusion.']
    return result
