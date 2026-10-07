"""Real chronological gameplay periods and bounded change/rescue diagnostics.

Gameplay may combine controls within a time class. Clock evidence never does.
Ranked best-game sets cannot establish a period or independent recurrence.
"""
import statistics
from dataclasses import replace
from fairplay_config import CONFIG
from fairplay_human import period_summary, absolute_qualified


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
        # Bound search to non-overlapping blocks for each size, plus actual
        # sessions and the complete class. Overlap is not independent replication.
        options=[('class',group)]+[('session',v) for v in sessions]
        for width in config.human_period_windows:
            if width>=len(group):continue
            for start in range(0,len(group)-width+1,width):options.append(('block',group[start:start+width]))
        identities=set()
        for mode,part in options:
            ids=tuple(g.identity for g in part)
            if len(part)<config.min_games or ids in identities:continue
            identities.add(ids);s=period_summary(part,config,fast=True)
            opponents=[g.opponent_rating for g in part if g.opponent_rating is not None]
            opponent_reference=statistics.median(opponents) if opponents else None
            baseline=[g for g in group if g.identity not in ids and g.rating is not None and
                s['rating_reference'] is not None and abs(g.rating-s['rating_reference'])<=config.baseline_player_rating_tolerance
                and g.opponent_rating is not None and opponent_reference is not None
                and abs(g.opponent_rating-opponent_reference)<=config.baseline_opponent_rating_tolerance]
            b=period_summary(baseline,config,fast=True)
            personal=('personal' not in config.disabled_features and kind!='bullet' and len(baseline)>=config.baseline_reference_games and b['hard_opportunities']>=40
                and s['hard_opportunities']>=40 and s['hard_contributors']>=8 and s['hard_lower']>=.65
                and s['hard_hits']/max(1,s['hard_opportunities'])-b['hard_hits']/max(1,b['hard_opportunities'])>=.25
                and s['information']-b['information']>=.10)
            periods.append({'ids':list(ids),'class':kind,'kind':mode,'start':part[0].ended,'end':part[-1].ended,
                'controls':sorted(set(g.time_control for g in part)), 'summary':s,
                'absolute':kind!='bullet' and absolute_qualified(s,config),
                'personal':bool(personal),'baseline_ids':[g.identity for g in baseline],
                'qualified':(kind!='bullet' and absolute_qualified(s,config)) or bool(personal)})
    return sorted(periods,key=lambda p:(p['qualified'],p['summary']['hit_lower'],p['summary']['contributors']),reverse=True)


def deep_confirmation(period,games,config=CONFIG):
    members=[g for g in games if g.identity in period['ids'] and g.deep]
    paired=period_summary(members,config,fast=True);deep=period_summary(members,config)
    n=deep['opportunities'];stable=deep['stable_opportunities']
    retention=(deep['hits']/paired['hits']) if paired['hits'] else 0
    qualified=(len(members)>=config.human_deep_games and deep['contributors']>=config.human_deep_games
        and n>=config.human_deep_opportunities and deep['hit_lower']>=config.human_deep_hit_lower
        and stable/max(1,n)>=config.human_stability_fraction and retention>=config.human_retention)
    # Stable opportunities must include successes, not merely stable misses.
    qualified=qualified and deep['stable_hits']>=config.human_deep_opportunities*.6
    absolute=bool(qualified and period.get('absolute',True))
    controls=[g for g in games if g.identity in period.get('baseline_ids',[]) and g.deep]
    baseline=period_summary(controls,config)
    hard_n=deep['hard_opportunities'];hard_retention=deep['hard_hits']/max(1,paired['hard_hits'])
    personal=(period.get('personal',False) and len(controls)>=config.baseline_deep_games
        and len(members)>=config.human_deep_games and deep['hard_contributors']>=5
        and hard_n>=config.human_deep_opportunities and baseline['hard_opportunities']>=12
        and deep['hard_lower']>=config.human_deep_hit_lower
        and deep['hard_hits']/max(1,hard_n)-baseline['hard_hits']/max(1,baseline['hard_opportunities'])>=.25
        and deep['information']-baseline['information']>=.10
        and hard_retention>=config.human_retention and deep['hard_stable']/max(1,hard_n)>=config.human_stability_fraction)
    return {'qualified':bool(absolute or personal),'absolute':absolute,'personal':bool(personal),
            'games':len(members),'summary':deep,'paired_fast':paired,'retention':retention,
            'stability_fraction':stable/max(1,n)}


def adaptive_deep_games(games,config=CONFIG):
    if config.deep_games==0:return []  # explicit local/fixture deep-disable override
    from fairplay_clusters import select_deep_games, representative_controls
    periods=class_periods(games,config);candidate=next((p for p in periods if p['qualified']),None)
    target=min(len(games),config.deep_normal_games)
    independent=[]
    for p in sorted([p for p in periods if p['qualified']],key=lambda p:len(p['ids'])):
        if not any(set(p['ids'])&set(q['ids']) for q in independent):independent.append(p)
    if len(independent)>1:target=min(len(games),config.deep_max_games)
    target=max(min(len(games),config.deep_min_games),target)
    if not candidate:return select_deep_games(games,replace(config,deep_games=target))
    lookup={g.identity:g for g in games};members=[lookup[i] for i in candidate['ids']]
    baseline=[g for g in games if g.identity not in candidate['ids'] and g.time_class==candidate['class']]
    # Controls are representative quality quartiles, never exclusively poor play.
    controls=representative_controls(baseline,config.baseline_deep_games)
    second=next((p for p in independent if not set(p['ids'])&set(candidate['ids'])),None)
    others=[lookup[i] for i in second['ids']] if second else []
    second_count=min(5,len(others));reserve=len(controls);count=min(len(members),target-reserve-second_count)
    selected=[members[round(i*(len(members)-1)/max(1,count-1))] for i in range(count)]+controls
    if others:selected+=[others[round(i*(len(others)-1)/max(1,second_count-1))] for i in range(second_count)]
    for game in select_deep_games(games,replace(config,deep_games=target)):
        if len(selected)>=target:break
        if game not in selected:selected.append(game)
    return selected[:target]


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
    periods=class_periods(games,config);confirmed=[]
    for p in periods:
        if p['qualified']:
            proof=deep_confirmation(p,games,config)
            p['deep']=proof
            if proof['qualified']:confirmed.append(p)
    best=confirmed[0] if confirmed else periods[0] if periods else None
    recurrent=[]
    for p in sorted(confirmed,key=lambda p:(len(p['ids']),p['start'])):
        if not any(set(p['ids'])&set(q['ids']) for q in recurrent):recurrent.append(p)
    replicated=False;replicated_ids=set();replicated_opportunities=0
    for left in recurrent:
        for right in recurrent:
            between=[g for g in games if left['end']<g.ended<right['start'] and g.time_class==left['class']==right['class']]
            if len(between)>=3 and period_summary(between,config,fast=True)['information']<min(left['summary']['information'],right['summary']['information'])*.5:
                replicated=True
                replicated_ids.update(left['summary']['contributor_ids']);replicated_ids.update(right['summary']['contributor_ids'])
    if replicated:replicated_opportunities=sum(p['summary']['opportunities'] for p in recurrent)
    sufficient=len(result.games)>=config.min_games and result.totals['decisions']>=config.min_games*config.min_game_decisions
    allowed=bool(confirmed and sufficient and not result.partial and result.confidence!='LOW'
                 and not {'human','difficulty'}&set(config.disabled_features))
    # These correlated gameplay features form ONE family. Other families retain
    # their own legacy scope. Baseline presence/stability is not a veto here.
    if allowed and result.priority in ('LOW','MODERATE'):
        result.priority='HIGH';result.deep_confirmed=True
        path='Deep-confirmed personal gameplay change' if best.get('personal') and best.get('deep',{}).get('personal') else 'Distributed rating-conditioned gameplay (timing not required)'
        result.diagnostics.update(high_path=path,high_blocked=[])
        result.reasons=[
            'Distributed difficult, competitive decisions were unusually precise for the rating reference, '
            'with paired deep-search confirmation across several games.',
            'Timing or result anomalies are not required for this gameplay route. '
            'Human expectedness is an uncalibrated heuristic; human review remains mandatory.']
    if (allowed and replicated and 'recurrence' not in config.disabled_features and len(result.games)>=config.very_high_games and result.confidence=='HIGH'
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
            'deep_confirmation':bool(confirmed),'complete_analysis':not result.partial,
            'confidence':result.confidence!='LOW',
            'timing_support_optional':result.diagnostics.get('gate_scores',{}).get('Move-Time Pattern',0)>=.5,
            'result_support_optional':result.diagnostics.get('gate_scores',{}).get('Account / Results',0)>=.5,
            'recurrence_optional':replicated},
        'research_evidence_index':round(100*max((p['summary']['information']*p['summary']['hit_lower'] for p in periods),default=0),2)}
    if not allowed and result.priority not in ('HIGH','VERY HIGH'):
        gates=result.diagnostics['gameplay']['gates']
        required=('sample','gameplay_period','deep_confirmation','complete_analysis','confidence')
        result.diagnostics['gameplay']['blocked']=[k.replace('_',' ') for k in required if not gates[k]]
    result.families['Human / Difficulty Evidence']='Elevated' if allowed else 'Limited / not established'
    return result
