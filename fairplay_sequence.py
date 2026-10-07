"""Real chronological gameplay periods and bounded change/rescue diagnostics.

Gameplay may combine controls within a time class. Clock evidence never does.
Ranked best-game sets cannot establish a period or independent recurrence.
"""
import statistics
from dataclasses import replace
from fairplay_config import CONFIG
from fairplay_human import period_summary, absolute_qualified, absolute_blockers
from fairplay_acute import acute_blockers, acute_deep_confirmation


def class_absolute(summary,kind,config=CONFIG):
    if not absolute_qualified(summary,config):return False
    if kind!='bullet':return True
    # Bullet precision is less reliable, not categorically unusable. Require
    # substantially broader, more extreme gameplay evidence before deep review.
    return bool(summary['games']>=config.bullet_human_games
        and summary['opportunities']>=config.bullet_human_opportunities
        and summary['contributors']>=config.bullet_human_contributors
        and summary['hit_lower']>=config.bullet_human_hit_lower
        and summary['information']>=config.bullet_human_information)


def class_periods(games, config=CONFIG):
    groups={kind:[] for kind in ('rapid','blitz','bullet')}
    seen=set()
    for game in sorted(games,key=lambda g:(g.ended,g.identity)):
        if game.identity in seen or game.rated is not True or game.probe_only:continue
        seen.add(game.identity)
        if 'human' in (game.fast_metrics or game.metrics):groups[game.time_class].append(game)
    periods=[]
    for kind,group in groups.items():
        sessions=[];session=[]
        for game in group:
            if session and game.ended-session[-1].ended>config.session_gap_seconds:
                sessions.append(session);session=[]
            session.append(game)
        if session:sessions.append(session)
        # Bounded chronological rolling windows prevent arbitrary block edges
        # from hiding a ten-game cluster. Overlap is never independent replication.
        options=[('class',group)]+[('session',v) for v in sessions]
        for width in config.human_period_windows:
            if width>=len(group):continue
            stride=1 if width==config.min_games else max(1,config.human_period_stride)
            starts=set(range(0,len(group)-width+1,stride));starts.add(len(group)-width)
            for start in sorted(starts):options.append(('window',group[start:start+width]))
        # Acute discovery spans the complete engine-reviewed class sample.
        # The windows are contiguous and overlapping windows never count as
        # independent recurrence. Strict per-game/deep gates control the
        # look-elsewhere risk; no ranked noncontiguous strongest-game set exists.
        for width in config.acute_windows:
            if width>len(group):continue
            for start in range(0,len(group)-width+1):
                options.append(('acute_candidate',group[start:start+width]))
        identities=set()
        for mode,part in options:
            ids=tuple(g.identity for g in part)
            acute = mode=='acute_candidate'
            if (not acute and len(part)<config.min_games) or ids in identities:continue
            identities.add(ids);s=period_summary(part,config,fast=True)
            opponents=[g.opponent_rating for g in part if g.opponent_rating is not None]
            opponent_reference=statistics.median(opponents) if opponents else None
            baseline=[g for g in group if g.identity not in ids and g.rating is not None and
                s['rating_reference'] is not None and abs(g.rating-s['rating_reference'])<=config.baseline_player_rating_tolerance
                and g.opponent_rating is not None and opponent_reference is not None
                and abs(g.opponent_rating-opponent_reference)<=config.baseline_opponent_rating_tolerance]
            b=period_summary(baseline,config,fast=True)
            personal=('personal' not in config.disabled_features and kind!='bullet' and len(baseline)>=config.baseline_reference_games and b['hard_opportunities']>=40
                and not acute and s['hard_opportunities']>=40 and s['hard_contributors']>=config.human_min_contributors and s['hard_lower']>=.65
                and s['hard_hits']/max(1,s['hard_opportunities'])-b['hard_hits']/max(1,b['hard_opportunities'])>=.25
                and (s.get('observed_quality') or 0)-(b.get('observed_quality') or 0)>=.18)
            periods.append({'ids':list(ids),'class':kind,'kind':mode,'start':part[0].ended,'end':part[-1].ended,
                'controls':sorted(set(g.time_control for g in part)), 'summary':s,
                'absolute':not acute and class_absolute(s,kind,config),
                'acute':acute,
                'blockers':acute_blockers(part,config) if acute else absolute_blockers(s,config),
                'personal':bool(personal),'baseline_ids':[g.identity for g in baseline],
                'qualified':not acute_blockers(part,config) if acute else (class_absolute(s,kind,config) or bool(personal))})
    return sorted(periods,key=lambda p:(p['qualified'],p['summary']['information']*p['summary']['hit_lower'],p['summary']['contributors']),reverse=True)


def deep_confirmation(period,games,config=CONFIG):
    if period.get('acute'):return acute_deep_confirmation(period,games,config)
    members=[g for g in games if g.identity in period['ids'] and g.deep]
    paired=period_summary(members,config,fast=True);deep=period_summary(members,config)
    n=deep['opportunities'];stable=deep['stable_opportunities']
    retention=(deep['hits']/paired['hits']) if paired['hits'] else 0
    qualified=(len(members)>=config.human_deep_games and deep['contributors']>=config.human_deep_games
        and n>=config.human_deep_opportunities and deep['hit_lower']>=config.human_deep_hit_lower
        and stable/max(1,n)>=config.human_stability_fraction and retention>=config.human_retention)
    # Stable opportunities must include successes, not merely stable misses.
    qualified=qualified and deep['stable_hits']>=config.human_deep_opportunities*.6
    absolute=bool(qualified and period.get('absolute',True)
        and deep['information']>=config.human_absolute_information_floor
        and deep.get('anomaly_strength',0)>=config.human_period_excess)
    if period['class']=='bullet':
        absolute=bool(absolute and len(members)>=config.bullet_human_deep_games
            and deep['contributors']>=config.bullet_human_deep_games
            and n>=config.bullet_human_deep_opportunities
            and deep['hit_lower']>=config.bullet_human_deep_hit_lower
            and deep['information']>=config.bullet_human_information
            and stable/max(1,n)>=config.bullet_human_stability)
    controls=[g for g in games if g.identity in period.get('baseline_ids',[]) and g.deep]
    baseline=period_summary(controls,config)
    hard_n=deep['hard_opportunities'];hard_retention=deep['hard_hits']/max(1,paired['hard_hits'])
    personal=(period.get('personal',False) and len(controls)>=config.baseline_deep_games
        and len(members)>=config.human_deep_games and deep['hard_contributors']>=5
        and hard_n>=config.human_deep_opportunities and baseline['hard_opportunities']>=12
        and deep['hard_lower']>=config.human_deep_hit_lower
        and deep['hard_hits']/max(1,hard_n)-baseline['hard_hits']/max(1,baseline['hard_opportunities'])>=.25
        and (deep.get('observed_quality') or 0)-(baseline.get('observed_quality') or 0)>=.18
        and hard_retention>=config.human_retention and deep['hard_stable']/max(1,hard_n)>=config.human_stability_fraction)
    return {'qualified':bool(absolute or personal),'absolute':absolute,'personal':bool(personal),
            'games':len(members),'summary':deep,'paired_fast':paired,'retention':retention,
            'stability_fraction':stable/max(1,n),
            'blockers':[label for label,ok in {
                'deep contributor games':len(members)>=config.human_deep_games and deep['contributors']>=config.human_deep_games,
                'deep opportunity coverage':n>=config.human_deep_opportunities,
                'deep anomaly hit lower bound':deep['hit_lower']>=config.human_deep_hit_lower,
                'deep semantic-quality stability':stable/max(1,n)>=config.human_stability_fraction,
                'fast to deep retention':retention>=config.human_retention,
                'deep rating-adjusted information':deep['information']>=config.human_absolute_information_floor,
                'deep headroom-aware anomaly':deep.get('anomaly_strength',0)>=config.human_period_excess}.items() if not ok] if not (absolute or personal) else []}


def coverage_members(members,count):
    """Chronological anchors plus geometry coverage; never rank by hits.

    Opportunity-poor uniformly spaced selections can fail the deep denominator
    even when the complete period is informative. Within chronological bins,
    prefer more measurable positions, including all their misses.
    """
    members=sorted(members,key=lambda g:(g.ended,g.identity))
    if count>=len(members):return members
    if count<=2:return members[:1]+members[-1:] if count==2 else members[:count]
    selected=[members[0],members[-1]]
    interior=members[1:-1];bins=count-2
    for index in range(bins):
        start=index*len(interior)//bins;end=(index+1)*len(interior)//bins
        part=interior[start:end]
        selected.append(max(part,key=lambda g:((g.fast_metrics or g.metrics).get('human',{}).get('opportunities',0),-g.ended)))
    return sorted(selected,key=lambda g:(g.ended,g.identity))


def adaptive_deep_games(games,config=CONFIG):
    if config.deep_games==0:return []  # explicit local/fixture deep-disable override
    from fairplay_clusters import select_deep_games, representative_controls
    periods=class_periods(games,config)
    # A strict acute candidate is expensive to miss: deep-review it first.
    # Broad periods remain available as independent secondary coverage.
    acute_candidate=next((p for p in periods if p['qualified'] and p.get('acute')),None)
    candidate=acute_candidate or next((p for p in periods if p['qualified']),None)
    target=min(len(games),config.deep_normal_games)
    independent=[]
    for p in sorted([p for p in periods if p['qualified']],key=lambda p:len(p['ids'])):
        if not any(set(p['ids'])&set(q['ids']) for q in independent):independent.append(p)
    if len(independent)>1:target=min(len(games),config.deep_max_games)
    target=max(min(len(games),config.deep_min_games),target)
    if not candidate:return select_deep_games(games,replace(config,deep_games=target))
    lookup={g.identity:g for g in games};members=[lookup[i] for i in candidate['ids']]
    baseline=[g for g in games if g.identity not in candidate['ids'] and g.time_class==candidate['class']
              and (not candidate.get('personal') or g.identity in candidate.get('baseline_ids',[]))]
    # Controls are representative quality quartiles, never exclusively poor play.
    controls=representative_controls(baseline,config.baseline_deep_games)
    second=next((p for p in independent if not set(p['ids'])&set(candidate['ids'])),None)
    others=[lookup[i] for i in second['ids']] if second else []
    second_count=min(5,len(others));reserve=len(controls);count=min(len(members),target-reserve-second_count)
    if candidate.get('acute'):
        target=min(len(games),config.deep_max_games,max(target,len(members)+reserve+second_count))
        count=len(members)
    primary=coverage_members(members,count)
    coverage=sum((g.fast_metrics or g.metrics).get('human',{}).get('opportunities',0) for g in primary)
    required_coverage=config.bullet_human_deep_opportunities if candidate['class']=='bullet' else config.human_deep_opportunities
    if coverage<required_coverage and target<config.deep_max_games:
        target=min(len(games),config.deep_max_games)
        count=min(len(members),target-reserve-second_count)
        primary=coverage_members(members,count)
    selected=primary+controls
    if others:selected+=coverage_members(others,second_count)
    # Diversify confirmation: reserve remaining capacity for games that are
    # anomalous specifically under the human/difficulty layer, not only under
    # the legacy engine-cluster score. This is allocation only; final HIGH gates
    # are unchanged and every miss inside selected games is deep-reviewed.
    human_ranked=sorted(
        [g for g in games if not g.deep],
        key=lambda g:(
            (g.fast_metrics or g.metrics).get('human',{}).get('anomaly_strength',0),
            (g.fast_metrics or g.metrics).get('human',{}).get('information',0),
            (g.fast_metrics or g.metrics).get('human',{}).get('difficulty_inversion_strength',0),
            (g.fast_metrics or g.metrics).get('human',{}).get('opportunities',0)),
        reverse=True)
    for game in human_ranked:
        if len(selected)>=target:break
        if game not in selected:selected.append(game)
    for game in select_deep_games(games,replace(config,deep_games=target)):
        if len(selected)>=target:break
        if game not in selected:selected.append(game)
    return selected[:target]



def confirmation_extension(games,config=CONFIG):
    """Spend remaining deep budget on unresolved, retained gameplay evidence.

    This is allocation, not a relaxed HIGH gate. Whole games are chosen by
    chronological opportunity coverage, not by successful moves. Already
    confirmed candidates and deep quality collapses receive no extra budget.
    """
    if config.deep_games==0:return []
    budget=max(0,config.deep_max_games-sum(g.deep for g in games))
    if not budget:return []
    for period in class_periods(games,config):
        if not period['qualified'] or period.get('acute'):continue
        proof=deep_confirmation(period,games,config);summary=proof['summary']
        if proof['qualified']:continue
        if (proof['retention']<config.human_retention
            or summary['hit_lower']<config.human_deep_hit_lower
            or summary['information']<config.human_absolute_information_floor
            or summary.get('anomaly_strength',0)<config.human_period_excess):
            continue
        pending=[g for g in games if g.identity in period['ids'] and not g.deep
                 and (g.fast_metrics or g.metrics).get('human',{}).get('opportunities',0)]
        if pending:return coverage_members(pending,min(budget,len(pending)))
    return []


def game_structure(game):
    rows=[d for d in game.decisions if d.metrics.get('useful')]
    changes=[];rescues=0
    # Search each candidate split, retain only the strongest; no claim of an
    # independent p-value. This is structure inside one game, never a HIGH route.
    for cut in range(8,len(rows)-7):
        before=[d.metrics.get('human_information',0) for d in rows[:cut]]
        after=[d.metrics.get('human_information',0) for d in rows[cut:]]
        delta=statistics.median(after)-statistics.median(before)
        if delta>=.35:changes.append({'ply':rows[cut].ply,'delta':delta,'before':len(before),'after':len(after)})
    for i,d in enumerate(rows):
        tail=rows[i:i+5]
        if (d.metrics.get('before_cp',0)<=-100 and len(tail)==5 and
            sum(x.metrics.get('high_information',False) for x in tail)>=3 and
            tail[-1].metrics.get('actual_cp',-1000)>=-30):rescues+=1
    mistakes=[d for d in rows if d.metrics.get('cpl',0)>=100]
    return {'change':max(changes,key=lambda x:x['delta'],default=None),'rescue_windows':rescues,
        'mistake_difficulty':statistics.median(d.metrics.get('difficulty',0) for d in mistakes) if mistakes else None,
        'under_pressure_information':statistics.median(d.metrics.get('human_information',0) for d in rows if d.metrics.get('before_cp',0)<-100)
            if any(d.metrics.get('before_cp',0)<-100 for d in rows) else None}


def integrate_gameplay(result,games,config=CONFIG):
    from fairplay_opening import repertoire
    from fairplay_human import evidence_funnel
    legacy_priority=result.priority
    legacy_blockers=list(result.diagnostics.get("high_blocked",[]))
    periods=class_periods(games,config);confirmed=[]
    for p in periods:
        proof=deep_confirmation(p,games,config)
        p['deep']=proof
        if p['qualified'] and proof['qualified']:confirmed.append(p)
    best=confirmed[0] if confirmed else periods[0] if periods else None
    recurrent=[]
    for p in sorted([p for p in confirmed if not p.get('acute')],key=lambda p:(len(p['ids']),p['start'])):
        if not any(set(p['ids'])&set(q['ids']) for q in recurrent):recurrent.append(p)
    replicated=False;replicated_ids=set();replicated_opportunities=0
    for left in recurrent:
        for right in recurrent:
            between=[g for g in games if left['end']<g.ended<right['start'] and g.time_class==left['class']==right['class']]
            if len(between)>=3 and period_summary(between,config,fast=True)['information']<min(left['summary']['information'],right['summary']['information'])*.5:
                replicated=True
                replicated_ids.update(left['summary']['contributor_ids']);replicated_ids.update(right['summary']['contributor_ids'])
    if replicated:replicated_opportunities=sum(p['summary']['opportunities'] for p in recurrent)
    acute_confirmed=next((p for p in confirmed if p.get('acute')),None)
    broad_confirmed=[p for p in confirmed if not p.get('acute')]
    coverage=getattr(result,'coverage',{})
    primary_complete=coverage.get('primary_engine_complete',not result.partial)
    sufficient=len(result.games)>=config.min_games and result.totals['decisions']>=config.min_games*config.min_game_decisions
    broad_allowed=bool(broad_confirmed and sufficient and primary_complete and result.confidence!='LOW')
    acute_allowed=bool(acute_confirmed and primary_complete)
    if broad_allowed:best=broad_confirmed[0]
    elif acute_allowed:best=acute_confirmed
    allowed=bool((broad_allowed or acute_allowed) and not {'human','difficulty'}&set(config.disabled_features))
    # These correlated gameplay features form ONE family. Other families retain
    # their own legacy scope. Baseline presence/stability is not a veto here.
    if allowed and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
        result.priority='HIGH';result.deep_confirmed=True
        path='Acute exceptional gameplay — limited sample' if acute_allowed and not broad_allowed else 'Deep-confirmed personal gameplay change' if best.get('personal') and best.get('deep',{}).get('personal') else 'Distributed rating-conditioned gameplay (timing not required)'
        result.diagnostics.update(high_path=path,high_blocked=[])
        if acute_allowed and not broad_allowed:
            result.confidence='LOW' if len(acute_confirmed['ids'])<4 else 'MEDIUM'
        result.reasons=[
            'Distributed difficult, competitive decisions were unusually precise for the rating reference, '
            'with paired deep-search confirmation across several games.',
            'Timing or result anomalies are not required for this gameplay route. '
            'Human expectedness is an uncalibrated heuristic; human review remains mandatory.']
    if allowed and acute_allowed and not broad_allowed and str(result.diagnostics.get('high_path','')).startswith('Acute'):
        result.reasons=['An exceptionally concentrated gameplay anomaly was deep-confirmed across multiple recent games. The sample is small, so this result requires manual review and cannot establish misconduct.']
    if (broad_allowed and allowed and replicated and 'recurrence' not in config.disabled_features and len(result.games)>=config.very_high_games and result.confidence=='HIGH'
            and replicated_opportunities>=80 and len(replicated_ids)>=12):
        result.priority='VERY HIGH'
        result.reasons.append('Disjoint deep-confirmed periods repeat, separated by adequately sampled lower-anomaly play.')
    structural=[game_structure(g) for g in games]
    result.diagnostics['gameplay']={'model':'rating-conditioned heuristic (not probability)',
        'best':best,'qualified':allowed,'periods_examined':len(periods),
        'confirmed_periods':len(confirmed),'replicated_disjoint_periods':replicated,'funnel':evidence_funnel(games,config),
        'structure':{'change_games':sum(bool(s['change']) for s in structural),
                     'rescue_games':sum(s['rescue_windows']>0 for s in structural)},
        'repertoire':{} if 'opening' in config.disabled_features else repertoire(games),
        'color_profiles':{name:period_summary([g for g in games if g.color==color],config)
                          for name,color in [('White',True),('Black',False)]},
        'gates':{'sample':sufficient,'gameplay_period':bool(best and best['qualified']),
            'human_expectedness':bool(best and best['qualified']),
            'absolute_gameplay':bool(best and best.get('absolute')),
            'personal_gameplay':bool(best and best.get('personal')),
            'difficulty_opportunities':bool(best and best['summary']['hard_opportunities']>=40),
            'deep_confirmation':bool(confirmed),'complete_analysis':bool(primary_complete),
            'confidence':result.confidence!='LOW',
            'timing_support_optional':result.diagnostics.get('gate_scores',{}).get('Move-Time Pattern',0)>=.5,
            'result_support_optional':result.diagnostics.get('gate_scores',{}).get('Account / Results',0)>=.5,
            'recurrence_optional':replicated},
        'research_evidence_index':round(100*max((p['summary']['information']*p['summary']['hit_lower'] for p in periods),default=0),2)}
    if not allowed and result.priority not in ('HIGH','VERY HIGH'):
        gates=result.diagnostics['gameplay']['gates']
        required=('gameplay_period','deep_confirmation','complete_analysis')
        result.diagnostics['gameplay']['blocked']=[k.replace('_',' ') for k in required if not gates[k]]
    def route_row(predicate,field=None):
        options=[p for p in periods if predicate(p)]
        candidate=next((p for p in options if p['qualified'] and p['deep']['qualified'] and (not field or (p.get(field) and p['deep'].get(field)))),options[0] if options else None)
        passed=bool(candidate and candidate['qualified'] and candidate['deep']['qualified'] and primary_complete
                    and not {'human','difficulty'}&set(config.disabled_features))
        if field and candidate and (not candidate.get(field) or not candidate['deep'].get(field)):passed=False
        blockers=[]
        if field and candidate and not candidate.get(field):
            blockers.extend(candidate.get('blockers') or [field.title()+' candidate requirements are not established.'])
        if field and candidate and not candidate['deep'].get(field):
            blockers.extend(candidate['deep'].get('blockers') or [field.title()+' paired deep confirmation is not established.'])
        if not candidate:blockers.append('No eligible chronological candidate.')
        elif not candidate['qualified']:blockers.extend(candidate.get('blockers') or ['Personal/gameplay period not exceptional.'])
        if candidate and not candidate['deep']['qualified']:blockers.extend(candidate['deep'].get('blockers') or ['Required paired deep confirmation not established.'])
        if not primary_complete:blockers.append('Required primary engine coverage is incomplete.')
        if candidate and not candidate.get('acute') and (not sufficient or result.confidence=='LOW'):
            passed=False;blockers.append('Broad-sample coverage or confidence is insufficient.')
        if {'human','difficulty'}&set(config.disabled_features):blockers.append('Gameplay family disabled in local ablation.')
        return {'passed':passed,'blockers':[] if passed else list(dict.fromkeys(blockers)),
                'candidate_games':len(candidate['ids']) if candidate else 0}
    convergence=getattr(result,'clusters',{}).get('convergence',{})
    result.diagnostics['high_paths']={
        'Legacy cluster HIGH':{'passed':legacy_priority in ('HIGH','VERY HIGH') and not convergence.get('raised_priority'),
            'blockers':legacy_blockers if legacy_priority not in ('HIGH','VERY HIGH') else []},
        'Absolute gameplay HIGH':route_row(lambda p:p['kind']!='acute_candidate','absolute'),
        'Personal-change HIGH':route_row(lambda p:p.get('personal',False),'personal'),
        'Acute exceptional HIGH':route_row(lambda p:p.get('acute',False),'acute'),
        'Recurrence HIGH':{'passed':bool(broad_allowed and replicated),
            'blockers':[] if broad_allowed and replicated else ['Separated, deep-confirmed periods with intervening lower-anomaly play not established.']},
        'Gameplay+timing HIGH':{'passed':bool(
                broad_allowed and result.diagnostics.get('gate_scores',{}).get('Move-Time Pattern',0)>=.5),
            'blockers':[] if broad_allowed and result.diagnostics.get('gate_scores',{}).get('Move-Time Pattern',0)>=.5
                else ['Deep-confirmed broad gameplay plus same-period timing support not jointly established.']},
        'Convergence HIGH':{'passed':bool(convergence.get('raised_priority')),
            'blockers':[] if convergence.get('raised_priority') else (convergence.get('confirmation',{}).get('blockers') or ['Complete-period convergence not established.'])}}
    result.families['Human / Difficulty Evidence']='Elevated' if allowed else 'Limited / not established'
    return result
