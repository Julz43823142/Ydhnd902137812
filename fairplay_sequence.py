"""Real chronological gameplay periods and bounded change/rescue diagnostics.

Gameplay may combine controls within a time class. Clock evidence never does.
Ranked best-game sets cannot establish a period or independent recurrence.
"""
import statistics
from dataclasses import replace
from fairplay_config import CONFIG
from fairplay_human import period_summary, absolute_qualified, absolute_blockers, period_raw_excess_floor
from fairplay_acute import acute_blockers, acute_deep_confirmation
from fairplay_confirmation import paired_quality_confirmation


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


def class_periods(games, config=CONFIG, *, strict_original_sequence=False):
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
        # Fixed halves are a deliberately non-searched replication view. They
        # never qualify the ordinary absolute/personal HIGH routes themselves;
        # only both halves together may establish the replicated route below.
        if len(group)>=2*config.human_sparse_min_games:
            middle=len(group)//2
            if middle>=config.human_sparse_min_games and len(group)-middle>=config.human_sparse_min_games:
                options += [('replication_half',group[:middle]),('replication_half',group[middle:])]
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
            if strict_original_sequence and len(part)>1:
                # A v21 100+25 deep subset is deliberately discontinuous in
                # the original archive. Skipped intervening rated peer games
                # cannot magically become a contiguous HIGH evidence period.
                if any(a.time_control!=b.time_control or
                       getattr(a,'control_index',None) is None or
                       getattr(b,'control_index',None) != a.control_index+1
                       for a,b in zip(part,part[1:])):
                    continue
            ids=tuple(g.identity for g in part)
            acute = mode=='acute_candidate';replication_half = mode=='replication_half'
            identity_key=(ids,replication_half)
            if (not acute and len(part)<config.min_games) or identity_key in identities:continue
            identities.add(identity_key);s=period_summary(part,config,fast=True)
            opponents=[g.opponent_rating for g in part if g.opponent_rating is not None]
            opponent_reference=statistics.median(opponents) if opponents else None
            baseline=[g for g in group if g.identity not in ids and g.rating is not None and
                s['rating_reference'] is not None and abs(g.rating-s['rating_reference'])<=config.baseline_player_rating_tolerance
                and g.opponent_rating is not None and opponent_reference is not None
                and abs(g.opponent_rating-opponent_reference)<=config.baseline_opponent_rating_tolerance]
            personal_eligible=('personal' not in config.disabled_features and kind!='bullet'
                               and not acute and not replication_half)
            b=period_summary(baseline,config,fast=True) if personal_eligible else {}
            personal=(personal_eligible and len(baseline)>=config.baseline_reference_games and b['hard_opportunities']>=40
                and s['hard_opportunities']>=40 and s['hard_contributors']>=config.human_min_contributors and s['hard_lower']>=.65
                and s['hard_hits']/max(1,s['hard_opportunities'])-b['hard_hits']/max(1,b['hard_opportunities'])>=.25
                and (s.get('observed_quality') or 0)-(b.get('observed_quality') or 0)>=.18)
            if acute:
                blockers=acute_blockers(part,config);absolute=False;qualified=not blockers
            else:
                blockers=absolute_blockers(s,config)
                absolute=bool(not replication_half and class_absolute(s,kind,config))
                qualified=False if replication_half else bool(absolute or personal)
            periods.append({'ids':list(ids),'class':kind,'kind':mode,'start':part[0].ended,'end':part[-1].ended,
                'controls':sorted(set(g.time_control for g in part)), 'summary':s,
                'absolute':absolute,'acute':acute,'replication_half':replication_half,
                'blockers':blockers,'personal':bool(personal),'baseline_ids':[g.identity for g in baseline],
                'qualified':qualified})
    return sorted(periods,key=lambda p:(p['qualified'],p['summary']['information']*p['summary']['hit_lower'],p['summary']['contributors']),reverse=True)


def deep_confirmation(period,games,config=CONFIG):
    if period.get('acute'):return acute_deep_confirmation(period,games,config)
    members=[g for g in games if g.identity in period['ids'] and g.deep]
    paired=period_summary(members,config,fast=True);deep=period_summary(members,config)
    n=deep['opportunities'];stable=deep['stable_opportunities']
    reliability=paired_quality_confirmation(deep,config.human_stability_fraction)
    retention=(deep['hits']/paired['hits']) if paired['hits'] else 0
    qualified=(len(members)>=config.human_deep_games and deep['contributors']>=config.human_deep_games
        and n>=config.human_deep_opportunities and deep['hit_lower']>=config.human_deep_hit_lower
        and reliability['coverage_passed'] and reliability['fraction_passed']
        and retention>=config.human_retention)
    # Stable opportunities must include successes, not merely stable misses.
    qualified=qualified and reliability['hits']>=config.human_deep_opportunities*.6
    absolute=bool(qualified and period.get('absolute',True)
        and deep['information']>=config.human_absolute_information_floor
        and deep.get('quality_excess',0)>=period_raw_excess_floor(deep,config)
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
        and hard_retention>=config.human_retention
        and reliability['coverage_passed'] and reliability['fraction_passed'])
    return {'qualified':bool(absolute or personal),'absolute':absolute,'personal':bool(personal),
            'games':len(members),'summary':deep,'paired_fast':paired,'retention':retention,
            'stability_fraction':reliability['fraction'],
            'legacy_geometric_stability_fraction':stable/max(1,n),
            'paired_search_coverage_fraction':reliability['coverage'],
            'blockers':[label for label,ok in {
                'deep contributor games':len(members)>=config.human_deep_games and deep['contributors']>=config.human_deep_games,
                'deep opportunity coverage':n>=config.human_deep_opportunities,
                'deep anomaly hit lower bound':deep['hit_lower']>=config.human_deep_hit_lower,
                'paired search-quality coverage':reliability['coverage_passed'],
                'deep semantic-quality stability':reliability['fraction_passed'],
                'fast to deep retention':retention>=config.human_retention,
                'deep rating-adjusted information':deep['information']>=config.human_absolute_information_floor,
                'deep raw quality excess beyond ceiling guard':deep.get('quality_excess',0)>=period_raw_excess_floor(deep,config),
                'deep headroom-aware anomaly':deep.get('anomaly_strength',0)>=config.human_period_excess}.items() if not ok] if not (absolute or personal) else []}


def sparse_distribution(period,summary):
    """Chronological breadth of hit-bearing and multi-hit contributor games."""
    ids=list(period.get('ids') or [])
    if not ids:return {'left_hits':0,'right_hits':0,'left_contributors':0,'right_contributors':0}
    cut=max(1,len(ids)//2)
    left,right=set(ids[:cut]),set(ids[cut:])
    hits=set(summary.get('hit_game_ids') or [])
    contributors=set(summary.get('contributor_ids') or [])
    return {
        'left_hits':len(left&hits),'right_hits':len(right&hits),
        'left_contributors':len(left&contributors),'right_contributors':len(right&contributors)}


def sparse_review_blockers(period,config=CONFIG):
    """MODERATE-only screen for genuinely broad intermittent evidence."""
    s=period.get('summary',{});spread=sparse_distribution(period,s)
    hit_games=s.get('hit_games',0)
    single_fraction=s.get('single_hit_games',0)/max(1,hit_games)
    tests={
        'non-bullet gameplay':period.get('class')!='bullet',
        'broad chronological sample':s.get('games',0)>=config.human_sparse_min_games,
        'known rating coverage':s.get('rating_coverage',0)>=.8,
        'distributed opportunity coverage':s.get('opportunities',0)>=config.human_sparse_min_opportunities,
        'distributed high-information decisions':s.get('hits',0)>=config.human_sparse_min_hits,
        'high-information games':hit_games>=config.human_sparse_min_hit_games,
        'multi-hit contributor breadth':s.get('contributors',0)>=config.human_sparse_min_contributors,
        'high-information games in both period halves':min(spread['left_hits'],spread['right_hits'])>=config.human_sparse_min_half_hit_games,
        'contributors in both period halves':min(spread['left_contributors'],spread['right_contributors'])>=config.human_sparse_min_half_contributors,
        'single-hit games are not dominant':single_fraction<=config.human_sparse_max_single_hit_fraction,
        'anomaly hit lower bound':s.get('hit_lower',0)>=config.human_sparse_hit_lower,
        'rating-adjusted information':s.get('information',0)>=config.human_sparse_information_floor,
        'raw quality separation':s.get('quality_excess',0)>=config.human_sparse_min_raw_excess,
        'headroom-aware anomaly strength':s.get('anomaly_strength',0)>=config.human_sparse_anomaly_strength,
    }
    return [label for label,passed in tests.items() if not passed]


def sparse_deep_blockers(period,proof,config=CONFIG):
    """Paired deep retention for the distributed MODERATE route."""
    if not proof:return ['paired deep review unavailable']
    s=proof.get('summary',{});paired=proof.get('paired_fast',{})
    reliability=paired_quality_confirmation(s,config.human_sparse_deep_stability)
    n=s.get('opportunities',0);retention=s.get('hits',0)/max(1,paired.get('hits',0))
    hit_game_retention=s.get('hit_games',0)/max(1,paired.get('hit_games',0))
    spread=sparse_distribution(period,s)
    tests={
        'deep reviewed games':proof.get('games',0)>=config.human_sparse_deep_games,
        'deep opportunity coverage':n>=config.human_sparse_deep_opportunities,
        'deep high-information decisions':s.get('hits',0)>=config.human_sparse_deep_hits,
        'deep high-information games':s.get('hit_games',0)>=config.human_sparse_deep_hit_games,
        'deep multi-hit contributor breadth':s.get('contributors',0)>=config.human_sparse_deep_contributors,
        'deep hit-bearing games in both period halves':min(spread['left_hits'],spread['right_hits'])>=config.human_sparse_deep_min_half_hit_games,
        'deep anomaly hit lower bound':s.get('hit_lower',0)>=config.human_sparse_deep_hit_lower,
        'deep stable high-information decisions':reliability['hits']>=config.human_sparse_deep_stable_hits,
        'paired search-quality coverage':reliability['coverage_passed'],
        'deep semantic-quality stability':reliability['fraction_passed'],
        'fast to deep hit retention':retention>=config.human_sparse_deep_retention,
        'fast to deep hit-game retention':hit_game_retention>=config.human_sparse_deep_hit_game_retention,
        'deep rating-adjusted information':s.get('information',0)>=config.human_sparse_information_floor,
        'deep raw quality separation':s.get('quality_excess',0)>=config.human_sparse_min_raw_excess,
        'deep headroom-aware anomaly':s.get('anomaly_strength',0)>=config.human_sparse_anomaly_strength,
    }
    return [label for label,passed in tests.items() if not passed]


def best_sparse_period(periods,config=CONFIG):
    broad=[p for p in periods if not p.get('acute') and not p.get('replication_half')]
    if not broad:return None
    def rank(period):
        s=period.get('summary') or {};spread=sparse_distribution(period,s)
        single_fraction=s.get('single_hit_games',0)/max(1,s.get('hit_games',0))
        return (
            not sparse_review_blockers(period,config),
            min(spread['left_contributors'],spread['right_contributors']),
            s.get('contributors',0),
            min(spread['left_hits'],spread['right_hits']),
            s.get('hit_lower',0),
            s.get('information',0)*s.get('anomaly_strength',0),
            -single_fraction,
            s.get('opportunities',0))
    return max(broad,key=rank)


def fixed_replication_status(periods,config=CONFIG,*,require_deep=False):
    """Evaluate two deterministic chronological halves, never searched windows.

    The two halves reuse the conservative distributed-MODERATE fast/deep gates.
    Requiring both halves is temporal replication inside one gameplay family,
    not two independent probabilities.
    """
    groups={}
    for period in periods:
        if period.get('replication_half'):
            groups.setdefault(period.get('class'),[]).append(period)
    candidates=[]
    for kind,halves in groups.items():
        halves=sorted(halves,key=lambda p:(p.get('start',0),p.get('end',0)))
        if len(halves)!=2:continue
        blockers=[]
        for index,period in enumerate(halves,1):
            blockers.extend(f'half {index}: {value}' for value in sparse_review_blockers(period,config))
            if require_deep:
                blockers.extend(f'half {index}: {value}' for value in sparse_deep_blockers(period,period.get('deep'),config))
        score=sum((p.get('summary') or {}).get('information',0)*(p.get('summary') or {}).get('hit_lower',0) for p in halves)
        candidates.append({'class':kind,'halves':halves,'passed':not blockers,'blockers':list(dict.fromkeys(blockers)),'score':score})
    if not candidates:
        return {'class':None,'halves':[],'passed':False,
                'blockers':['two fixed chronological halves of at least thirty games'],'score':0}
    return max(candidates,key=lambda row:(row['passed'],row['score']))


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


def adaptive_deep_games(games,config=CONFIG,*,periods=None):
    if config.deep_games==0:return []  # explicit local/fixture deep-disable override
    from fairplay_clusters import select_deep_games, representative_controls
    periods=class_periods(games,config) if periods is None else periods
    # A strict acute candidate is expensive to miss: deep-review it first.
    # Broad periods remain available as independent secondary coverage.
    acute_candidate=next((p for p in periods if p['qualified'] and p.get('acute')),None)
    high_candidate=next((p for p in periods if p['qualified']),None)
    replication=fixed_replication_status(periods,config)
    # If no ordinary HIGH candidate exists, spend the bounded deep budget
    # symmetrically across two fixed chronological halves. This avoids tuning
    # confirmation to the single most flattering searched window.
    if not acute_candidate and not high_candidate and replication['passed']:
        lookup={g.identity:g for g in games}
        per_half=max(config.human_sparse_deep_games,config.deep_max_games//2)
        selected=[]
        for period in replication['halves']:
            members=[lookup[i] for i in period['ids'] if i in lookup]
            for game in coverage_members(members,min(per_half,len(members))):
                if game not in selected:selected.append(game)
        target=min(len(games),config.deep_max_games)
        for game in select_deep_games(games,replace(config,deep_games=target)):
            if len(selected)>=target:break
            if game not in selected:selected.append(game)
        return selected[:target]
    sparse_candidate=best_sparse_period(periods,config)
    if sparse_candidate and sparse_review_blockers(sparse_candidate,config):sparse_candidate=None
    candidate=acute_candidate or high_candidate or sparse_candidate
    target=min(len(games),config.deep_normal_games)
    independent=[]
    for p in sorted([p for p in periods if p['qualified']],key=lambda p:len(p['ids'])):
        if not any(set(p['ids'])&set(q['ids']) for q in independent):independent.append(p)
    if len(independent)>1:target=min(len(games),config.deep_max_games)
    target=max(min(len(games),config.deep_min_games),target)
    if not candidate:
        plan=select_deep_games(games,replace(config,deep_games=target))
        from fairplay_maia import confirmation_pair
        pair=confirmation_pair(games)
        if pair:
            # Keep the normal plan and at most two additions. Do not remove
            # representative controls or narrow confirmation to successful moves.
            plan += [g for g in pair if g not in plan]
        return plan[:config.deep_max_games]
    lookup={g.identity:g for g in games};members=[lookup[i] for i in candidate['ids']]
    sparse_allocation=not candidate.get('qualified') and not sparse_review_blockers(candidate,config)
    baseline=[g for g in games if g.identity not in candidate['ids'] and g.time_class==candidate['class']
              and (not candidate.get('personal') or g.identity in candidate.get('baseline_ids',[]))]
    # Controls are representative quality quartiles, never exclusively poor play.
    controls=representative_controls(baseline,config.baseline_deep_games)
    second=next((p for p in independent if not set(p['ids'])&set(candidate['ids'])),None)
    others=[lookup[i] for i in second['ids']] if second else []
    second_count=min(5,len(others));reserve=len(controls)
    # Reserve real confirmation capacity for a different evidence geometry.
    # Previously primary+controls(+second period) filled the target first, so
    # the later human-ranked loop was normally unreachable.
    diversify_count=0 if candidate.get('acute') or sparse_allocation else min(
        2, max(0,target-reserve-second_count-config.human_deep_games))
    count=min(len(members),target-reserve-second_count-diversify_count)
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
    outside=set(candidate['ids'])
    human_ranked=sorted(
        [g for g in games if not g.deep],
        key=lambda g:(
            g.identity not in outside,
            (g.fast_metrics or g.metrics).get('human',{}).get('anomaly_strength',0),
            (g.fast_metrics or g.metrics).get('human',{}).get('information',0),
            (g.fast_metrics or g.metrics).get('human',{}).get('difficulty_inversion_strength',0),
            (g.fast_metrics or g.metrics).get('human',{}).get('opportunities',0)),
        reverse=True)
    added=0
    for game in human_ranked:
        if added>=diversify_count:break
        if game not in selected:selected.append(game);added+=1
    for game in select_deep_games(games,replace(config,deep_games=target)):
        if len(selected)>=target:break
        if game not in selected:selected.append(game)
    return selected[:target]



def confirmation_extension(games,config=CONFIG,*,periods=None):
    """Spend remaining deep budget on unresolved, retained gameplay evidence.

    This is allocation, not a relaxed HIGH gate. Whole games are chosen by
    chronological opportunity coverage, not by successful moves. Already
    confirmed candidates and deep quality collapses receive no extra budget.
    """
    if config.deep_games==0:return []
    budget=max(0,config.deep_max_games-sum(g.deep for g in games))
    if not budget:return []
    periods=class_periods(games,config) if periods is None else periods
    for period in periods:
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


def integrate_gameplay(result,games,config=CONFIG,*,periods=None):
    from fairplay_opening import repertoire
    from fairplay_human import evidence_funnel
    legacy_priority=result.priority
    legacy_blockers=list(result.diagnostics.get("high_blocked",[]))
    periods=class_periods(games,config) if periods is None else periods;confirmed=[]
    for p in periods:
        proof=deep_confirmation(p,games,config)
        p['deep']=proof
        if p['qualified'] and proof['qualified']:confirmed.append(p)
    best=confirmed[0] if confirmed else next((p for p in periods if not p.get('replication_half')),periods[0] if periods else None)
    best_broad=next((p for p in periods if not p.get('acute') and not p.get('replication_half')),None)
    sparse_candidate=best_sparse_period(periods,config)
    sparse_confirmed=[p for p in periods if not p.get('acute')
        and not sparse_review_blockers(p,config) and not sparse_deep_blockers(p,p.get('deep'),config)]
    fixed_replication=fixed_replication_status(periods,config,require_deep=True)
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
    feature_enabled=not {'human','difficulty'}&set(config.disabled_features)
    allowed=bool((broad_allowed or acute_allowed) and feature_enabled)
    fixed_replication_allowed=bool(fixed_replication['passed'] and sufficient and primary_complete
        and result.confidence=='HIGH' and feature_enabled)
    sparse_allowed=bool(sparse_confirmed and sufficient and primary_complete and result.confidence!='LOW'
        and feature_enabled)
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
    if not allowed and fixed_replication_allowed and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
        result.priority='HIGH';result.deep_confirmed=True
        result.diagnostics.update(high_path='Replicated fixed-half gameplay (timing not required)',high_blocked=[])
        result.reasons=[
            'Two pre-defined chronological halves independently showed broad high-information gameplay evidence '
            'and both retained it under paired deep review.',
            'The halves are temporal replication inside one correlated gameplay family, not independent probabilities. '
            'Timing or result anomalies are not required; human review remains mandatory.']
    if not allowed and not fixed_replication_allowed and sparse_allowed and result.priority in ('LOW','INSUFFICIENT DATA'):
        result.priority='MODERATE'
        result.diagnostics['moderate_path']='Distributed intermittent gameplay evidence with paired deep retention'
        result.reasons=[
            'High-information decisions were distributed across many games and retained under paired deep review.',
            'This is a MODERATE review-priority signal only; it is not an independent evidence family and does not establish misconduct.']
    if allowed and acute_allowed and not broad_allowed and str(result.diagnostics.get('high_path','')).startswith('Acute'):
        result.reasons=['An exceptionally concentrated gameplay anomaly was deep-confirmed across multiple recent games. The sample is small, so this result requires manual review and cannot establish misconduct.']
    if (broad_allowed and allowed and replicated and 'recurrence' not in config.disabled_features and len(result.games)>=config.very_high_games and result.confidence=='HIGH'
            and replicated_opportunities>=80 and len(replicated_ids)>=12):
        result.priority='VERY HIGH'
        result.reasons.append('Disjoint deep-confirmed periods repeat, separated by adequately sampled lower-anomaly play.')
    structural=[game_structure(g) for g in games]
    display_best=(best if allowed else fixed_replication['halves'][0] if fixed_replication_allowed
                  else sparse_confirmed[0] if sparse_allowed else best_broad or best)
    sparse_review=sparse_candidate
    sparse_blockers=(sparse_review_blockers(sparse_review,config) if sparse_review else ['No eligible broad chronological candidate.'])
    if sparse_review and not sparse_blockers:
        sparse_blockers=sparse_deep_blockers(sparse_review,sparse_review.get('deep'),config)
    replication_blockers=list(fixed_replication['blockers'])
    if not sufficient:replication_blockers.append('minimum broad sample coverage')
    if not primary_complete:replication_blockers.append('required primary engine coverage is incomplete')
    if result.confidence!='HIGH':replication_blockers.append('HIGH data confidence')
    if not feature_enabled:replication_blockers.append('gameplay family enabled')
    result.diagnostics['gameplay']={'model':'rating-conditioned heuristic (not probability)',
        'best':display_best,'best_high':best,'best_broad':best_broad,'qualified':bool(allowed or fixed_replication_allowed),'periods_examined':len(periods),
        'confirmed_periods':len(confirmed),'replicated_disjoint_periods':replicated,'funnel':evidence_funnel(games,config),
        'fixed_half_replication':{'passed':fixed_replication_allowed,
            'class':fixed_replication.get('class'),'half_games':[len(p.get('ids',[])) for p in fixed_replication.get('halves',[])],
            'blockers':[] if fixed_replication_allowed else list(dict.fromkeys(replication_blockers))},
        'distributed_moderate':{'passed':sparse_allowed,'blockers':[] if sparse_allowed else list(dict.fromkeys(sparse_blockers)),
            'candidate_games':len(sparse_review['ids']) if sparse_review else 0,
            'deep_games':sparse_review.get('deep',{}).get('games',0) if sparse_review else 0,
            'fast':({key:sparse_review.get('summary',{}).get(key,0) for key in
                ('opportunities','hits','hit_games','contributors','single_hit_games','hit_lower','quality_excess','anomaly_strength')}
                if sparse_review else {}),
            'deep':({key:sparse_review.get('deep',{}).get('summary',{}).get(key,0) for key in
                ('opportunities','hits','hit_games','contributors','stable_hits','hit_lower','quality_excess','anomaly_strength')}
                if sparse_review else {})},
        'structure':{'change_games':sum(bool(s['change']) for s in structural),
                     'rescue_games':sum(s['rescue_windows']>0 for s in structural)},
        'repertoire':{} if 'opening' in config.disabled_features else repertoire(games),
        'color_profiles':{name:period_summary([g for g in games if g.color==color],config)
                          for name,color in [('White',True),('Black',False)]},
        'gates':{'sample':sufficient,'gameplay_period':bool(fixed_replication_allowed or ((best if allowed else best_broad) and (best if allowed else best_broad)['qualified'])),
            'human_expectedness':bool(fixed_replication_allowed or ((best if allowed else best_broad) and (best if allowed else best_broad)['qualified'])),
            'absolute_gameplay':bool((best if allowed else best_broad) and (best if allowed else best_broad).get('absolute')),
            'personal_gameplay':bool((best if allowed else best_broad) and (best if allowed else best_broad).get('personal')),
            'difficulty_opportunities':bool(fixed_replication_allowed or ((best if allowed else best_broad) and (best if allowed else best_broad)['summary']['hard_opportunities']>=40)),
            'deep_confirmation':bool(confirmed or fixed_replication_allowed),'complete_analysis':bool(primary_complete),
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
        # Score EVERY pre-existing route candidate first. Choosing one to show
        # cannot screen out stronger Rapid/Blitz candidates behind Bullet.
        from fairplay_evidence_audit import CandidateAudit, Check, State, Reason, PASSED, select_route_diagnostics
        options=[p for p in periods if predicate(p)]
        outcomes=[]
        descriptors=[]
        for p in options:
            proof=p.get('deep',{})
            qualifying=bool(p.get('qualified') and proof.get('qualified'))
            route_specific=bool(not field or (p.get(field) and proof.get(field)))
            broad_ok=bool(p.get('acute') or (sufficient and result.confidence!='LOW'))
            eligible_kind=not (field in ('acute','personal') and p['class']=='bullet')
            feature_ok=not {'human','difficulty'}&set(config.disabled_features)
            passed=bool(qualifying and route_specific and broad_ok and
                        eligible_kind and primary_complete and feature_ok)
            blockers=[]
            if not eligible_kind:blockers.append('Time class excluded by existing route rules.')
            if field and not p.get(field):
                blockers.extend(p.get('blockers') or [field.title()+' candidate requirements are not established.'])
            if field and not proof.get(field):
                blockers.extend(proof.get('blockers') or [field.title()+' paired deep confirmation is not established.'])
            if not p['qualified']:blockers.extend(p.get('blockers') or ['Personal/gameplay period not exceptional.'])
            if not proof.get('qualified'):blockers.extend(proof.get('blockers') or ['Required paired deep confirmation not established.'])
            if not primary_complete:blockers.append('Required primary engine coverage is incomplete.')
            if not broad_ok:blockers.append('Broad-sample coverage or confidence is insufficient.')
            if not feature_ok:blockers.append('Gameplay family disabled in local ablation.')
            blockers=list(dict.fromkeys(blockers))
            outcomes.append((p,passed,blockers))
            descriptors.append(CandidateAudit(
                route=field or 'gameplay',time_class=p['class'],
                games=len(p['ids']),opportunities=p.get('summary',{}).get('opportunities',0),
                structurally_eligible=eligible_kind and broad_ok,
                checks={'qualifying':PASSED if qualifying else Check(State.FAIL,Reason.EXISTING_GATE),
                        'deep':PASSED if proof.get('qualified') else Check(State.FAIL,Reason.EXISTING_GATE),
                        'route':PASSED if route_specific else Check(State.FAIL,Reason.EXISTING_GATE),
                        'primary':PASSED if primary_complete else Check(State.FAIL,Reason.EXISTING_GATE)}))
        # Presentation-only representative; route status is OR over ALL actual
        # candidate outcomes and is never derived from the display choice.
        chosen=(select_route_diagnostics(descriptors).get(field or 'gameplay')
                if descriptors else None)
        winner=next(((p,passed,blockers) for p,passed,blockers in outcomes
                     if p['class']==chosen.time_class and len(p['ids'])==chosen.games
                     and p.get('summary',{}).get('opportunities',0)==chosen.opportunities),
                    outcomes[0] if outcomes else None) if chosen else None
        accepted=next((row for row in outcomes if row[1]),None)
        display=accepted or winner
        passed=bool(accepted)
        return {'passed':passed,
                'blockers':[] if passed else (display[2] if display else ['No eligible chronological candidate.']),
                'candidate_games':len(display[0]['ids']) if display else 0,
                'representative_time_class':display[0]['class'] if display else 'none',
                'candidates_evaluated':len(outcomes)}

    convergence=getattr(result,'clusters',{}).get('convergence',{})
    result.diagnostics['high_paths']={
        'Legacy cluster HIGH':{'passed':legacy_priority in ('HIGH','VERY HIGH') and not convergence.get('raised_priority'),
            'blockers':legacy_blockers if legacy_priority not in ('HIGH','VERY HIGH') else []},
        'Absolute gameplay HIGH':route_row(lambda p:p['kind']!='acute_candidate' and not p.get('replication_half'),'absolute'),
        'Personal-change HIGH':route_row(lambda p:p.get('personal',False),'personal'),
        'Acute exceptional HIGH':route_row(lambda p:p.get('acute',False),'acute'),
        'Replicated fixed-half HIGH':{'passed':fixed_replication_allowed,
            'blockers':[] if fixed_replication_allowed else list(dict.fromkeys(replication_blockers)),
            'candidate_games':sum(len(p.get('ids',[])) for p in fixed_replication.get('halves',[]))},
        'Recurrence HIGH':{'passed':bool(broad_allowed and replicated),
            'blockers':[] if broad_allowed and replicated else ['Separated, deep-confirmed periods with intervening lower-anomaly play not established.']},
        'Gameplay+timing HIGH':{'passed':bool(
                broad_allowed and result.diagnostics.get('gate_scores',{}).get('Move-Time Pattern',0)>=.5),
            'blockers':[] if broad_allowed and result.diagnostics.get('gate_scores',{}).get('Move-Time Pattern',0)>=.5
                else ['Deep-confirmed broad gameplay plus same-period timing support not jointly established.']},
        'Convergence HIGH':{'passed':bool(convergence.get('raised_priority')),
            'blockers':[] if convergence.get('raised_priority') else (convergence.get('confirmation',{}).get('blockers') or ['Complete-period convergence not established.'])}}
    result.families['Human / Difficulty Evidence']='Elevated' if (allowed or fixed_replication_allowed or sparse_allowed) else 'Limited / not established'
    return result
