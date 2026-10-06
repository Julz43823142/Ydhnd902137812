"""Conservative multi-family priority gates, separate from HTTP/engine execution."""
from fairplay_config import CONFIG
from fairplay_clusters import clamp, evidence, find_clusters, regime_changes, confirm_cluster, summary, buckets
from fairplay_baseline import personal_timing
from fairplay_timing import trivial_delay_summary

def priority_model(scores, *, games, decisions, critical, confidence, deep_confirmed, partial,
                   config=CONFIG, persistent=True, recurrence=False, cluster_games=None, deep_cluster_games=None):
    if games<config.min_games or decisions<150:return 'INSUFFICIENT DATA'
    engine, difficult, timing, shift, context = scores
    primary = engine>=config.high_engine_score or (difficult>=config.high_critical_score and critical>=config.min_deep_critical)
    supporting = max(timing,shift,context)>=.5 or recurrence
    strong = max(engine,difficult)>=config.very_high_score and min(engine,difficult)>=.65
    if (strong and max(timing,shift,context)>=.75 and persistent and deep_confirmed
            and confidence=='HIGH' and not partial and games>=config.very_high_games
            and decisions>=config.high_decisions and critical>=config.very_high_critical
            and (cluster_games is None or cluster_games>=10) and (deep_cluster_games is None or deep_cluster_games>=8)):
        return 'VERY HIGH'
    if primary and supporting and persistent and deep_confirmed and confidence!='LOW':return 'HIGH'
    return 'MODERATE' if max(engine,difficult)>=.5 or sum(v*w for v,w in zip(scores,config.weights))>=.30 else 'LOW'


def score_review(target, games, selected, skipped, partial, engine_name, profile, elapsed, config=CONFIG):
    from fairplay_analysis import aggregate, performance_metrics, context_metrics, median, family_label, ReviewResult
    useful = sorted([g for g in games if g.metrics.get('decisions',0)>=config.min_game_decisions],key=lambda g:(g.ended,g.identity))
    totals = aggregate(useful)
    classes = {kind:aggregate([g for g in useful if g.time_class==kind]) for kind in ('rapid','blitz','bullet')}
    performance, context = performance_metrics(useful), context_metrics(useful,profile)
    changes = regime_changes(useful,config)
    performance['regime_changes'] = changes
    clusters = find_clusters(useful,config,True)
    strongest = clusters['strongest']
    # A title changes expectations, not the evidence. It never multiplies away
    # a personal anomaly. Stable elite play needs an independent behavior shift.
    personal = personal_timing(useful,config)
    timing_score = 0.0
    for row in personal:
        timing_score = max(timing_score,{'Slight':.25,'Moderate':.5,'Strong':.75,'Very Strong':.9}.get(row['state'],0)*(.35 if row['time_class']=='bullet' else 1))
    recurrent, trivial_timing = [], {}
    for (kind,control),group in buckets(useful).items():
        weight = .35 if kind=='bullet' else 1.0
        delay = trivial_delay_summary(group,config)
        trivial_timing[f'{kind} · {control}'] = delay
        if delay['recurrent']:timing_score = max(timing_score,weight*(.7 if delay['same_cadence_games']>=10 else .55))
        regular = [g for g in group if g.metrics['timing'].get('elevated') or g.metrics['timing'].get('regime_shift') or g.metrics['timing'].get('critical_cadence',{}).get('elevated')]
        if len(regular)>=5:
            recurrent.extend(regular);timing_score=max(timing_score,weight*min(.7,.4+len(regular)*.025))
    # Retain class-keyed detail compatibility without mixing controls in gates.
    exact_trivial = trivial_timing.copy()
    for kind in classes:
        rows = [v for key,v in exact_trivial.items() if key.startswith(kind+' · ')]
        trivial_timing[kind] = max(rows,key=lambda r:r['same_cadence_games']) if rows else trivial_delay_summary([],config)
    engine_score = critical_score = 0.0
    for (kind,_),group in buckets(useful).items():
        e,c = evidence(summary(group),median([g.rating for g in group if g.rating is not None]),config=config)
        weight = .35 if kind=='bullet' else 1
        engine_score=max(engine_score,e*weight);critical_score=max(critical_score,c*weight)
    for cluster in clusters['candidates']:
        if not cluster['persistent']:continue
        nearby_change = any(set(cluster['ids']) & set(row['ids']) and row['class']==cluster['time_class'] for row in changes)
        e,c = evidence(cluster['metrics'],personal=nearby_change,config=config)
        weight=.35 if cluster['time_class']=='bullet' else 1
        engine_score=max(engine_score,e*weight);critical_score=max(critical_score,c*weight)
    # A ranked subset is useful for detail/selection, never a persistence gate.
    deep_confirmation = confirm_cluster(strongest,useful,config)
    clusters['deep'] = deep_confirmation
    coverage = aggregate([g for g in useful if g.deep])
    deep_confirmed = deep_confirmation['confirmed']
    confidence = ('HIGH' if len(useful)>=30 and totals['decisions']>=500 and coverage['decisions']>=60 and not partial
                  else 'MEDIUM' if len(useful)>=config.min_games and totals['decisions']>=150 else 'LOW')
    perf_score = max((min(.95,.5+.05*row['effect_mad'])*(.35 if row['class']=='bullet' else 1) for row in changes),default=0)
    if performance['contrasts']:perf_score=max(perf_score,.55 if any(k!='bullet' for k in performance['contrasts']) else .2)
    ctx_score = .1 if context['age_days'] is not None and context['age_days']<30 else 0
    for kind,row in context['classes'].items():
        weight=.35 if kind=='bullet' else 1
        if row['excess_z'] is not None:ctx_score=max(ctx_score,clamp((row['excess_z']-2)/4)*.65*weight)
        if row['rating_gain'] is not None:ctx_score=max(ctx_score,clamp(row['rating_gain']/600)*.35*weight)
    # Independent recurrence needs a contrasting surrounding baseline. Hundreds
    # of equally excellent games do not become 'multiple suspicious clusters'.
    baseline = [g for g in useful if (g.metrics.get('top1') or 0)<.65 and (g.metrics.get('critical_top1') or 0)<.65]
    recurrence = clusters['recurrence'] and len(baseline)>=config.cluster_min_games
    clusters['recurrence'] = recurrence
    elite = bool(context['title']) or median([g.rating for g in useful if g.rating is not None],0)>=2200
    independent_timing = any(row['state'] in ('Moderate','Strong','Very Strong') and row['time_class']!='bullet' for row in personal)
    within_shift=sum(g.metrics['timing'].get('regime_shift',False) and g.time_class!='bullet' for g in useful)>=5
    if within_shift:perf_score=max(perf_score,.55)
    behavior_support=bool(independent_timing or changes or recurrence or within_shift)
    gate_timing = timing_score if not elite or behavior_support else 0
    gate_context = ctx_score if not elite or behavior_support else 0
    scores=(engine_score,critical_score,timing_score,perf_score,ctx_score)
    # The high/very-high gates use confirmed search strength, not a surviving
    # shallow spike or a different unreviewed period.
    deep_e,deep_c = evidence(deep_confirmation.get('metrics',{}),personal=True,config=config)
    if strongest and strongest['time_class']=='bullet':deep_e*=.35;deep_c*=.35
    gate_scores=(min(engine_score,deep_e),min(critical_score,deep_c),gate_timing,perf_score,gate_context) if deep_confirmed else (engine_score,critical_score,gate_timing,perf_score,gate_context)
    priority=priority_model(gate_scores,games=len(useful),decisions=totals['decisions'],critical=totals['critical'],
                           confidence=confidence,deep_confirmed=deep_confirmed,partial=partial,config=config,
                           persistent=bool(strongest and strongest['persistent']),recurrence=recurrence,
                           cluster_games=strongest['metrics']['games'] if strongest else 0,deep_cluster_games=deep_confirmation['games'])
    # Deep evidence can lower a shallow anomaly; selecting unaffected games is
    # not permission to claim confirmation. Every gate uses the actual cluster.
    reasons=[]
    if engine_score>=.5:reasons.append('Sustained engine precision on meaningful decisions, evaluated separately from the full-sample average.')
    if critical_score>=.65:reasons.append('Strong precision on difficult quiet or tactical choices with meaningful engine alternatives.')
    if strongest and strongest['strength']>=.5:reasons.append(f'Highest-signal {strongest["metrics"]["games"]}-game same-control period; deep confirmation: {"supported" if deep_confirmed else "not established"}.')
    if independent_timing:reasons.append('Personal timing behavior changed alongside stronger engine decisions; lag, habits and legitimate improvement remain alternatives.')
    elif timing_score>=.5:reasons.append('Trivial and critical decisions repeatedly arrive in the same delayed cadence; input delay or habits remain alternatives.' if any(k.startswith(('rapid · ','blitz · ')) and v['recurrent'] for k,v in trivial_timing.items()) else 'Repeated delayed move-time cadence; timing alone cannot establish high review priority.')
    if within_shift:reasons.append('Repeated within-game quality and timing transitions were found; opening and forced moves are excluded from this comparison.')
    if changes:reasons.append('A sustained same-control performance regime change was found; this is supporting evidence, not a verdict.')
    if recurrence:reasons.append('Separate high-signal periods recur against a lower-anomaly personal baseline.')
    if not reasons:reasons.append('No well-supported elevated signal combination was found; this does not establish fair play.')
    names=('Engine Precision','Critical Position Precision','Move-Time Pattern','Performance Shift','Account / Results')
    return ReviewResult(target,useful,selected,skipped,partial,engine_name,totals,classes,performance,context,
                        dict(zip(names,(family_label(v) for v in scores))),priority,confidence,reasons,
                        deep_confirmed,coverage,elapsed,timing={'trivial_delay':trivial_timing,'personal':personal},
                        clusters=clusters,
                        coverage={'collected':selected,'fast_scanned':len(games),'used':len(useful),'deep_reviewed':len([g for g in games if g.deep]),
                                  'excluded_after_fast':len(games)-len(useful)},
                        diagnostics={'scores':dict(zip(names,scores)),'weighted_review_score':sum(v*w for v,w in zip(scores,config.weights)),
                                     'independent_support':max(gate_scores[2:])>=.5 or recurrence,
                                     'engine_confidence':confidence,'timing_available':bool(recurrent or any(g.metrics['timing']['count']>=15 for g in useful))})
