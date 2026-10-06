"""Explicit uncertainty and leave-period-out comparisons for moderator screening.

These are transparent heuristic effect/coverage gates, not p-values calibrated
for the many overlapping searched windows. A best streak needs a substantial
change relative to the player's own dispersion, not merely high raw accuracy.
No titles, account status, external labels or Chess.com accuracy enter here.
"""
import math
import statistics as stats

from fairplay_config import CONFIG


def strength_expectations(rating, config=CONFIG):
    band = 0 if rating is None else min(4, max(0, (rating-1000)//400+1))
    return (config.engine_top1_floor+config.strength_top1_step*band,
            config.engine_top3_floor+config.strength_top3_step*band,
            config.critical_hit_floor+config.strength_critical_step*band)


def posterior_rate(rate, count, expectation, prior_count):
    """Shrink a hit rate toward a strength prior, not the evidence toward zero."""
    if rate is None or count <= 0:return None
    return (rate*count+expectation*prior_count)/(count+prior_count)


def lower_bound(rate, count, z=CONFIG.rate_lower_bound_z):
    """One-sided Wilson bound; weighted counts are approximate effective samples.

    Displayed rates stay raw. This denominator protection is not a claim of an
    independently calibrated confidence interval for correlated chess decisions.
    """
    if rate is None or count <= 0:return 0.0
    p=max(0,min(1,rate));z2=z*z
    return (p+z2/(2*count)-z*math.sqrt(p*(1-p)/count+z2/(4*count*count)))/(1+z2/count)


def comparable_baseline(cluster, games, config=CONFIG, *, fast=True):
    from fairplay_clusters import comparison_control, rows_for
    from fairplay_positions import opponent_context
    if not cluster:return []
    members=[g for g in games if g.identity in set(cluster['ids'])]
    if not members:return []
    key=comparison_control(members[0]);ids=set(cluster['ids'])
    candidates=sorted([g for g in games if g.rated is True and g.identity not in ids
                   and g.time_class==cluster['time_class'] and comparison_control(g)==key
                   and rows_for([g],fast)[0].get('decisions',0)>=config.min_game_decisions],
                  key=lambda g:(g.ended,g.identity))
    context=opponent_context(members)
    if context['player_rating'] is None:return candidates
    # Never fall back silently to mismatched opposition just to fill a baseline.
    # Missing ratings cannot establish an opponent-matched personal anomaly.
    return [g for g in candidates if g.rating is not None and g.opponent_rating is not None
            and abs(g.rating-context['player_rating'])<=config.baseline_player_rating_tolerance
            and abs(g.opponent_rating-context['opponent_rating'])<=config.baseline_opponent_rating_tolerance
            and abs(g.rating-g.opponent_rating-context['elo_difference'])<=config.baseline_rating_gap_tolerance]


def representative_controls(baseline, count=3):
    """Stratify by robust CPL at quartiles; never select just the worst games."""
    rows=sorted(baseline,key=lambda g:((g.fast_metrics or g.metrics).get('robust_cpl') or 0,g.ended,g.identity))
    if not rows or count<=0:return []
    if len(rows)<=count:return rows
    positions=[round((len(rows)-1)*(i+1)/(count+1)) for i in range(count)]
    return [rows[i] for i in dict.fromkeys(positions)]


def baseline_comparison(cluster, games, config=CONFIG, *, fast=True, controls=None):
    from fairplay_clusters import summary, rows_for, med
    from fairplay_positions import opponent_context
    members=[g for g in games if cluster and g.identity in set(cluster['ids']) and g.rated is True]
    baseline=comparable_baseline(cluster,games,config,fast=fast) if controls is None else controls
    a,b=summary(baseline,fast),summary(members,fast)
    def difference(key):
        return b[key]-a[key] if a.get(key) is not None and b.get(key) is not None else None
    deltas={key:difference(key) for key in ('weighted_top1','top3','median_cpl','robust_cpl','critical_top1')}
    for key in ('mistakes','blunders'):
        deltas[key+'_rate']=b[key]/b['decisions']-a[key]/a['decisions'] if min(a['decisions'],b['decisions']) else None
    cpls=[m['robust_cpl'] for m in rows_for(baseline,fast) if m.get('robust_cpl') is not None]
    center=med(cpls);mad=med([abs(v-center) for v in cpls],0) if cpls else 0
    drop=-(deltas['robust_cpl'] or 0)
    effect=drop/max(8,1.4826*mad)
    enough=len(baseline)>=config.baseline_reference_games and a['decisions']>=config.baseline_reference_decisions
    if controls is not None:
        # Three representative deep controls validate a baseline already covered
        # by many equal-budget fast games; they do not create a new population.
        enough=len(baseline)>=config.baseline_deep_games and a['decisions']>=config.min_deep_decisions
    top_gain=deltas['weighted_top1'] or 0
    critical_gain=deltas['critical_top1'] or 0
    # A natural best window must beat both effect size and personal dispersion.
    engine_delta=enough and top_gain>=config.baseline_top1_delta and drop>=config.baseline_cpl_delta and effect>=config.baseline_effect_mad
    critical_delta=enough and min(a['critical'],b['critical'])>=config.min_critical and critical_gain>=config.baseline_critical_delta and drop>=config.baseline_cpl_delta and effect>=config.baseline_effect_mad
    rate_delta_bounds={}
    if controls is not None:
        # A representative three-game deep control sample cannot be expected to
        # contain thirty critical positions. Compare conservative interval ends;
        # the full fast baseline must already establish the personal anomaly.
        def conservative_delta(key,count):
            upper=1-lower_bound(None if a[key] is None else 1-a[key],a[count],config.rate_lower_bound_z)
            return lower_bound(b[key],b[count],config.rate_lower_bound_z)-upper
        rate_delta_bounds={'weighted_top1':conservative_delta('weighted_top1','effective_decisions'),
                           'critical_top1':conservative_delta('critical_top1','critical')}
        engine_delta=engine_delta and rate_delta_bounds['weighted_top1']>=config.baseline_top1_delta
        critical_delta=(enough and a['critical']>=config.baseline_deep_critical
                        and b['critical']>=config.min_deep_critical
                        and rate_delta_bounds['critical_top1']>=config.baseline_critical_delta
                        and drop>=config.baseline_cpl_delta and effect>=config.baseline_effect_mad)

    state='Strong' if engine_delta or critical_delta else 'Normal' if enough else 'Insufficient Data'
    if state=='Strong' and top_gain>=.30 and drop>=40 and (critical_gain>=.30 or b['critical']<config.min_critical):state='Very Strong'
    stable=enough and abs(top_gain)<.08 and abs(critical_gain)<.12 and abs(deltas['robust_cpl'] or 0)<15
    return {'sufficient':enough,'state':state,'established':bool(engine_delta or critical_delta),
            'engine':bool(engine_delta),'critical':bool(critical_delta),'stable':stable,
            'baseline':a,'cluster':b,'deltas':deltas,'cpl_mad':mad,'effect_mad':effect,
            'before_games':sum(g.ended<cluster['start'] for g in baseline) if cluster else 0,
            'after_games':sum(g.ended>cluster['end'] for g in baseline) if cluster else 0,
            'rate_delta_bounds':rate_delta_bounds,'baseline_ids':[g.identity for g in baseline],
            'opponent_context':{'baseline':opponent_context(baseline),'cluster':opponent_context(members)},
            'opponent_matched':opponent_context(members)['player_rating'] is not None}


def high_cluster_qualification(cluster, games, config=CONFIG):
    if not cluster:return False
    m=cluster['metrics']
    eligible=m.get('eligible_games',m['games'])
    normal=(eligible>=config.high_cluster_games and m['decisions']>=config.high_cluster_decisions)
    exceptional=(eligible>=config.cluster_min_games and m['decisions']>=config.small_high_decisions
                 and m['critical']>=config.small_high_critical
                 and min(cluster['engine_score'],cluster['critical_score'])>=config.exceptional_evidence
                 and cluster['sustained_games']>=math.ceil(m['games']*.9))
    return bool(cluster['persistent'] and (normal or exceptional))


def stable_history(games, config=CONFIG):
    """Stable excellent play is a general history property, independent of title."""
    from fairplay_clusters import rows_for
    rows=rows_for(games,True)
    if len(rows)<config.normal_high_sample_games:return False
    tops=sorted(m.get('weighted_top1',m.get('top1')) for m in rows if m.get('weighted_top1',m.get('top1')) is not None)
    cpls=sorted(m['robust_cpl'] for m in rows if m.get('robust_cpl') is not None)
    if min(len(tops),len(cpls))<config.normal_high_sample_games:return False
    def spread(values):return values[round((len(values)-1)*.9)]-values[round((len(values)-1)*.1)]
    return stats.median(tops)>=.65 and spread(tops)<.08 and spread(cpls)<15
