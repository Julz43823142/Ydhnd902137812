"""Conservative multi-family priority gates, separate from HTTP/engine execution."""
from fairplay_config import CONFIG
from fairplay_clusters import clamp, evidence, find_clusters, regime_changes, confirm_cluster, summary, buckets, comparison_control
from fairplay_baseline import personal_timing
from fairplay_timing import trivial_delay_summary, cadence_recurrence, delay_floor_periods, delay_floor_profile

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
    # Critical hit rate is largely a skill/opportunity measure. It does not
    # establish an anomaly by itself, even when called 'High' descriptively.
    moderate = (max(engine,difficult)>=.5 and max(timing,shift,context)>=.35) or min(engine,difficult)>=.65
    return 'MODERATE' if moderate else 'LOW'


def score_review(target, games, selected, skipped, partial, engine_name, profile, elapsed, config=CONFIG):
    from fairplay_analysis import aggregate, performance_metrics, context_metrics, median, family_label, ReviewResult
    useful = sorted([g for g in games if g.metrics.get('decisions',0)>=config.min_game_decisions],key=lambda g:(g.ended,g.identity))
    totals = aggregate(useful)
    classes = {kind:aggregate([g for g in useful if g.time_class==kind]) for kind in ('rapid','blitz','bullet')}
    performance, context = performance_metrics(useful), context_metrics(games,profile)
    changes = regime_changes(useful,config)
    performance['regime_changes'] = changes
    clusters = find_clusters(games,config,True)
    strongest = clusters['strongest']
    # A title changes expectations, not the evidence. It never multiplies away
    # a personal anomaly. Stable elite play needs an independent behavior shift.
    personal = personal_timing(useful,config)
    timing_score = 0.0
    for row in personal:
        timing_score = max(timing_score,{'Slight':.25,'Moderate':.5,'Strong':.75,'Very Strong':.9}.get(row['state'],0)*(.35 if row['time_class']=='bullet' else 1))
    recurrent, trivial_timing, cadence_groups, delay_floors = [], {}, {}, {}
    for (kind,control),group in buckets(games).items():
        weight = .35 if kind=='bullet' else 1.0
        floor = delay_floor_periods(group,config)
        delay_floors[f'{kind} · {control}'] = floor
        timing_score=max(timing_score,weight*floor['score'])
        cadence_row = cadence_recurrence(group,config)
        cadence_groups[f'{kind} · {control}'] = cadence_row
        if cadence_row['recurrent']:
            timing_score=max(timing_score,weight*(.75 if cadence_row['games']>=10 else .55))
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
        members=[g for g in games if g.identity in cluster['ids']]
        e,c = evidence(cluster['metrics'],rating=median([g.rating for g in members if g.rating is not None]),personal=nearby_change,config=config)
        weight=.35 if cluster['time_class']=='bullet' else 1
        engine_score=max(engine_score,e*weight);critical_score=max(critical_score,c*weight)
    # A ranked subset is useful for detail/selection, never a persistence gate.
    deep_confirmation = confirm_cluster(strongest,games,config)
    clusters['deep'] = deep_confirmation
    coverage = aggregate([g for g in games if g.deep])
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
    comparable_baseline=[g for g in baseline if strongest and g.time_class==strongest['time_class']
                         and g.time_control==strongest['time_control'] and g.rated==strongest['rated']]
    period_recurrence=any(strongest and row['time_class']==strongest['time_class']
                          and row['time_control']==strongest['time_control'] and row['rated']==strongest['rated']
                          and set(row['ids']) & set(strongest['ids']) for row in clusters['recurrence_groups'])
    recurrence = period_recurrence and len(comparable_baseline)>=config.cluster_min_games
    if recurrence:perf_score=max(perf_score,.55)
    clusters['recurrence'] = recurrence
    elite = bool(context['title']) or median([g.rating for g in useful if g.rating is not None],0)>=2200
    independent_timing = any(row['state'] in ('Moderate','Strong','Very Strong') and row['time_class']!='bullet' for row in personal)
    within_shift=sum(g.metrics['timing'].get('regime_shift',False) and g.time_class!='bullet' for g in useful)>=5
    if within_shift:perf_score=max(perf_score,.55)
    # Supporting evidence must overlap the engine period and comparison group.
    # A rapid clock anomaly cannot confirm unrelated blitz precision.
    scope=[g for g in games if strongest and g.identity in strongest['ids']]
    scope_ids={g.identity for g in scope}
    scope_key=comparison_control(scope[0]) if scope else None
    scope_class=scope[0].time_class if scope else None
    matching_changes=[r for r in changes if r['class']==scope_class and r['control']==scope_key
                      and len(scope_ids & set(r['ids']))>=min(config.cluster_min_games,len(scope)//2)]
    matching_personal=[]
    for row in personal:
        if row['time_class']!=scope_class or row['time_control']!=scope_key:continue
        for candidate in (row,row.get('chronological_shift')):
            if candidate and len(scope_ids & set(candidate.get('high_ids',[])))>=min(config.cluster_min_games,len(scope)//2):
                matching_personal.append(candidate)
    scope_cadence=cadence_recurrence(scope,config)
    scope_trivial=trivial_delay_summary(scope,config)
    scope_floor=delay_floor_profile(scope,config)
    gate_timing=max(({'Slight':.25,'Moderate':.5,'Strong':.75,'Very Strong':.9}.get(r['state'],0) for r in matching_personal),default=0)
    if scope_cadence['recurrent']:gate_timing=max(gate_timing,.75 if scope_cadence['games']>=10 else .55)
    if scope_trivial['recurrent']:gate_timing=max(gate_timing,.7 if scope_trivial['same_cadence_games']>=10 else .55)
    gate_timing=max(gate_timing,scope_floor['score'])
    if scope_class=='bullet':gate_timing*=.35
    gate_shift=max((min(.95,.5+.05*r['effect_mad'])*(.35 if scope_class=='bullet' else 1) for r in matching_changes),default=0)
    if recurrence:gate_shift=max(gate_shift,.55*(.35 if scope_class=='bullet' else 1))
    if sum(g.metrics['timing'].get('regime_shift',False) and g.time_class!='bullet' for g in scope)>=5:
        gate_shift=max(gate_shift,.55)
    scope_context=context_metrics(scope,profile)['classes'].get(scope_class,{})
    gate_context=clamp(((scope_context.get('excess_z') or 0)-2)/4)*.65 if scope_class!='bullet' else 0
    scope_behavior=bool(matching_changes or any(r['state'] in ('Moderate','Strong','Very Strong') for r in matching_personal)
                        or recurrence or scope_floor['elevated'] or scope_trivial['recurrent'])
    if elite and not scope_behavior:
        gate_timing=gate_context=0
    scores=(engine_score,critical_score,timing_score,perf_score,ctx_score)
    # Confirmation is about this particular period. Do not borrow higher scores
    # or clocks from an unrelated time-control/rated bucket.
    scoped_e,scoped_c=evidence(summary(scope),rating=median([g.rating for g in scope if g.rating is not None]),personal=bool(matching_changes),config=config)
    if scope_class=='bullet':scoped_e*=.35;scoped_c*=.35
    deep_e,deep_c=evidence(deep_confirmation.get('metrics',{}),rating=median([g.rating for g in scope if g.rating is not None]),personal=bool(matching_changes),config=config,shrink=False)
    if scope_class=='bullet':deep_e*=.35;deep_c*=.35
    primary_e,primary_c=(min(scoped_e,deep_e),min(scoped_c,deep_c)) if deep_confirmed else (scoped_e,scoped_c)
    if elite and not scope_behavior:
        # Stable expert precision without a behavioral discrepancy is reported
        # descriptively; it does not earn MODERATE from correlated hit rates.
        primary_e=primary_c=0
    gate_scores=(primary_e,primary_c,gate_timing,gate_shift,gate_context)
    priority=priority_model(gate_scores,games=len(useful),decisions=totals['decisions'],critical=summary(scope)['critical'],
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
    elif any(row['elevated'] for key,row in delay_floors.items() if not key.startswith('bullet')):reasons.append('Trivial and critical decisions repeatedly have similar non-instant delays and timing spread; lag or input habits remain possible.')
    elif timing_score>=.5:reasons.append('Trivial and critical decisions repeatedly arrive in the same delayed cadence; input delay or habits remain alternatives.' if any(k.startswith(('rapid · ','blitz · ')) and v['recurrent'] for k,v in trivial_timing.items()) else 'Repeated delayed move-time cadence; timing alone cannot establish high review priority.')
    if within_shift:reasons.append('Repeated within-game quality and timing transitions were found; opening and forced moves are excluded from this comparison.')
    if changes:reasons.append('A sustained same-control performance regime change was found; this is supporting evidence, not a verdict.')
    if recurrence:reasons.append('Separate high-signal periods recur against a lower-anomaly personal baseline.')
    if elite and not scope_behavior:reasons.append('Strong precision without an independent personal behavior change is compatible with stable expert play.')
    if critical_score>=.5 and engine_score<.5 and max(gate_scores[2:])<.35:reasons.append('Critical-move agreement alone is insufficient to raise review priority.')
    if not any(g.metrics['timing']['count']>=config.min_timing_moves for g in games):reasons.append('Clock coverage is limited; missing timing evidence is not a normal-behavior finding.')
    if not reasons:reasons.append('No well-supported elevated signal combination was found; this does not establish fair play.')
    names=('Engine Precision','Critical Position Precision','Move-Time Pattern','Performance Shift','Account / Results')
    families=dict(zip(names,(family_label(v) for v in scores)))
    clock_games=sum(g.metrics['timing']['count']>=config.timing_min_game_moves for g in games)
    clock_moves=sum(g.metrics['timing']['count'] for g in games)
    if clock_games<config.timing_recurrence_games or clock_moves<config.timing_recurrence_moves:
        families['Move-Time Pattern']='Insufficient clock data'
    if totals['decisions']<150:families['Engine Precision']='Insufficient meaningful decisions'
    if totals['critical']<config.min_deep_critical:families['Critical Position Precision']='Insufficient critical positions'
    if not any(len(group)>=2*config.baseline_min_games for group in buckets(useful).values()):
        families['Performance Shift']='Insufficient comparable games'
    return ReviewResult(target,games,selected,skipped,partial,engine_name,totals,classes,performance,context,
                        families,priority,confidence,reasons,
                        deep_confirmed,coverage,elapsed,timing={'trivial_delay':trivial_timing,'personal':personal,'cadence_groups':cadence_groups,'delay_floors':delay_floors},
                        clusters=clusters,
                        coverage={'collected':selected,'fast_scanned':len(games),'used':len(useful),'deep_reviewed':len([g for g in games if g.deep]),
                                  'excluded_after_fast':len(games)-len(useful)},
                        diagnostics={'scores':dict(zip(names,scores)),'weighted_review_score':sum(v*w for v,w in zip(scores,config.weights)),
                                     'independent_support':max(gate_scores[2:])>=.5 or recurrence,
                                     'engine_confidence':confidence,'timing_available':any(g.metrics['timing']['count']>=config.min_timing_moves for g in games),
                                     'gate_scores':dict(zip(names,gate_scores)), 'stable_strong_play':elite and not scope_behavior,
                                     'same_period_support':max(gate_scores[2:])>=.5 or recurrence,
                                     'timing_coverage':{'games':sum(g.metrics['timing'].get('all_valid_clocks',0)>0 for g in games),
                                                        'usable_moves':sum(g.metrics['timing']['count'] for g in games),
                                                        'engine_linked_moves':sum(g.metrics['timing'].get('engine_clock_count',0) for g in games),
                                                        'clock_comments':sum(g.metrics['timing'].get('clock_comments',0) for g in games),
                                                        'excluded_clocks':sum(g.metrics['timing'].get('excluded_clocks',0) for g in games)}})
