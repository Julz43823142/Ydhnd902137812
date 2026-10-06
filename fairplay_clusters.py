"""Same-control chronological evidence; a short regime is never averaged away.

Scores are heuristic evidence strengths, not probabilities. Windows, sessions
and ranked subsets are identified separately. Only chronological/session groups
can establish persistence; ranked subsets are descriptive candidate selectors.
"""
import math
import statistics as stats
from collections import defaultdict
from fairplay_config import CONFIG
from fairplay_calibration import (strength_expectations, posterior_rate, lower_bound,
    baseline_comparison, comparable_baseline, representative_controls, high_cluster_qualification)


def clamp(value):
    return max(0.0, min(1.0, value))


def ramp(value, low, high):
    return clamp((value-low)/(high-low)) if value is not None else 0.0


def med(values, default=None):
    values = [v for v in values if v is not None]
    return stats.median(values) if values else default


def rows_for(games, fast=False):
    return [g.fast_metrics or g.metrics if fast else g.metrics for g in games]


def summary(games, fast=False):
    """Per-decision agreement counts, robust equal-game CPL, explicit denominators."""
    rows = rows_for(games, fast)
    n = sum(m.get('decisions', 0) for m in rows)
    c = sum(m.get('critical', 0) for m in rows)
    u = sum(m.get('unique', 0) for m in rows)
    def rate(key, count):
        denom = sum(m.get(count, 0) for m in rows if m.get(key) is not None)
        return sum(m[key]*m.get(count, 0) for m in rows if m.get(key) is not None)/denom if denom else None
    return {'games': len(games), 'decisions': n, 'critical': c, 'unique': u,
            'eligible_games':sum(m.get('decisions',0)>=CONFIG.min_game_decisions for m in rows),
            'competitive_decisions':sum(m.get('competitive_decisions',0) for m in rows),
            'competitive_top1':rate('competitive_top1','competitive_decisions'),
            'competitive_cpl':med([m.get('competitive_cpl') for m in rows]),
            'competitive_critical':sum(m.get('competitive_critical',0) for m in rows),
            'competitive_critical_top1':rate('competitive_critical_top1','competitive_critical'),
            'easy_conversion_decisions':sum(m.get('easy_conversion_decisions',0) for m in rows),
            'opponent_blunder_exposure':sum(m.get('opponent_blunder_exposure',0) for m in rows),
            'post_opponent_error_decisions':sum(m.get('post_opponent_error_decisions',0) for m in rows),
            'easy_winning_position_fraction':rate('easy_winning_position_fraction','position_context_decisions'),
            'top1': rate('top1', 'decisions'), 'weighted_top1':rate('weighted_top1','effective_decisions'),
            'effective_decisions':sum(m.get('effective_decisions',m.get('decisions',0)) for m in rows), 'top3': rate('top3', 'decisions'),
            'critical_top1': rate('critical_top1', 'critical'),
            'unique_hits': sum(m.get('unique_hits', 0) for m in rows),
            'median_cpl': med([m.get('median_cpl') for m in rows]),
            'robust_cpl': med([m.get('robust_cpl') for m in rows]),
            'p90_cpl': med([m.get('p90_cpl') for m in rows]),
            'mistakes': sum(m.get('mistakes', 0) for m in rows),
            'blunders': sum(m.get('blunders', 0) for m in rows),
            'quiet_critical': sum(m.get('quiet_critical', 0) for m in rows),
            'tactical_critical': sum(m.get('tactical_critical', 0) for m in rows)}


def evidence(metrics, rating=None, personal=False, config=CONFIG, *, shrink=True):
    """Strength-aware absolute evidence; personal is a compatibility-only argument.

    Personal differences are measured explicitly by baseline_comparison. Neither
    discovery nor deep confirmation may bypass the rating expectation.
    """
    top_floor, top3_floor, critical_floor = strength_expectations(rating,config)
    n, c = metrics.get('effective_decisions',metrics.get('decisions', 0)), metrics.get('critical', 0)
    top=metrics.get('weighted_top1') if metrics.get('weighted_top1') is not None else metrics.get('top1')
    top3=metrics.get('top3');hits=metrics.get('critical_top1')
    u=metrics.get('unique',0);unique=metrics.get('unique_hits',0)/u if u else None
    if shrink:
        top=posterior_rate(top,n,top_floor,config.engine_shrink_decisions)
        top3=posterior_rate(top3,n,top3_floor,config.engine_shrink_decisions)
        hits=posterior_rate(hits,c,critical_floor,config.critical_shrink_positions)
        unique=posterior_rate(unique,u,critical_floor,config.critical_shrink_positions)
    cpl=metrics.get('robust_cpl',metrics.get('median_cpl'))
    if shrink and cpl is not None:cpl=(n*cpl+config.engine_shrink_decisions*config.engine_cpl_weak)/(n+config.engine_shrink_decisions)
    engine=(ramp(top,top_floor,config.engine_top1_ceiling)*.65
            +ramp(top3,top3_floor,1.0)*.15
            +((1-ramp(cpl,config.engine_cpl_good,config.engine_cpl_weak))*.20 if cpl is not None else 0)) if n else 0
    critical=(ramp(hits,critical_floor,config.critical_hit_ceiling)*.75
              +ramp(unique,critical_floor,config.critical_hit_ceiling)*.25) if c else 0
    return clamp(engine), clamp(critical)


def comparison_control(game):
    """Never infer that a missing rated flag is rated, or mix casual baselines."""
    mode = 'rated' if game.rated is True else 'casual' if game.rated is False else 'rated status unknown'
    return (game.time_control or 'unknown:'+game.identity)+' · '+mode


def buckets(games):
    result = defaultdict(list)
    for game in sorted(games, key=lambda g:(g.ended, g.identity)):
        # Unknown base/increment never combines disparate games into a baseline.
        key = (game.time_class, comparison_control(game))
        result[key].append(game)
    return result



def contiguous(group):
    """Targeted history must not bridge unscanned intervening eligible games."""
    return all(a.control_index is None or b.control_index is None or b.control_index==a.control_index+1
               for a,b in zip(group,group[1:]))

def group_record(group, kind, config=CONFIG, fast=False):
    m = summary(group, fast)
    m['eligible_games']=sum(r.get('decisions',0)>=config.min_game_decisions for r in rows_for(group,fast))
    engine, critical = evidence(m, med([g.rating for g in group]),config=config)
    reliability = .35 if group[0].time_class == 'bullet' else 1.0
    def sustained_game(g):
        row=rows_for([g],fast)[0]
        n=row.get('effective_decisions',row.get('decisions',0))
        top=row.get('weighted_top1') if row.get('weighted_top1') is not None else row.get('top1')
        critical_n=row.get('critical',0)
        return (n>=config.min_game_decisions and lower_bound(top,n,config.rate_lower_bound_z)>=config.persistence_top1_lower
                or critical_n>=config.persistence_min_critical
                and lower_bound(row.get('critical_top1'),critical_n,config.rate_lower_bound_z)>=config.persistence_critical_lower)
    sustained = sum(sustained_game(g) for g in group)
    # Per-game 2/2 never becomes strong evidence. A much larger independently
    # repeated opportunity pool can establish period-level persistence instead.
    pool_games=sum(row.get('critical',0)>0 and (row.get('critical_top1') or 0)>=config.persistence_critical_hits
                   for row in rows_for(group,fast))
    pooled=(len(group)>=config.pooled_critical_games and m['decisions']>=config.high_cluster_decisions
            and m['critical']>=config.min_critical
            and lower_bound(m['critical_top1'],m['critical'],config.rate_lower_bound_z)>=config.pooled_critical_lower
            and pool_games>=math.ceil(len(group)*config.pooled_critical_fraction))
    return {'kind': kind, 'time_class': group[0].time_class, 'time_control': group[0].time_control, 'rated':group[0].rated,
            'start': group[0].ended, 'end': group[-1].ended,
            'ids': [g.identity for g in group], 'metrics': m,
            'engine_score': engine*reliability, 'critical_score': critical*reliability,
            'strength': max(engine, critical)*reliability,
            'sustained_games': sustained,'pooled_critical_support':bool(pooled),'pooled_critical_games':pool_games,
            'persistent': kind != 'ranked' and len(group)>=config.cluster_min_games
                          and (sustained>=math.ceil(len(group)*config.persistence_fraction) or pooled)}


def find_clusters(games, config=CONFIG, fast=False):
    # Retain known fully analyzed short/easy games in the timeline. Their actual
    # losses and missed critical moves remain in the period denominator. Missing
    # engine games still break chronology through control_index; no gap bridging.
    games=[g for g in games if g.rated is True and not g.probe_only and 'decisions' in rows_for([g],fast)[0]]
    candidates = []
    for _, group in sorted(buckets(games).items()):
        for width in config.cluster_windows:
            for start in range(len(group)-width+1):
                window=group[start:start+width]
                if contiguous(window):candidates.append(group_record(window, 'chronological', config, fast))
        session = []
        for game in group:
            if session and (game.ended-session[-1].ended > config.session_gap_seconds or not contiguous([session[-1],game])):
                if len(session)>=config.cluster_min_games:candidates.append(group_record(session, 'session', config, fast))
                session = []
            session.append(game)
        if len(session)>=config.cluster_min_games:candidates.append(group_record(session, 'session', config, fast))
        if len(group)>=config.cluster_min_games:
            ranked = sorted(group, key=lambda g:max(evidence(summary([g],fast),config=config)), reverse=True)
            candidates.append(group_record(sorted(ranked[:10],key=lambda g:(g.ended,g.identity)), 'ranked', config, fast))
    candidates.sort(key=lambda r:(r['strength'], r['persistent'], r['metrics']['critical'], r['end']), reverse=True)
    # Deduplicate overlapping windows for recurrence. 6+ disjoint strong games
    # count; many overlapping views of the same block do not multiply evidence.
    independent, used = [], set()
    for row in candidates:
        if row['persistent'] and row['strength']>=.65 and not (used & set(row['ids'])):
            independent.append(row);used.update(row['ids'])
    # Disjoint windows inside one long uninterrupted plateau are one period.
    recurrence=False
    recurrence_groups=[]
    for i,left in enumerate(independent):
        for right in independent[i+1:]:
            if (left['time_class'],left['time_control'],left['rated']) != (right['time_class'],right['time_control'],right['rated']):continue
            lo,hi=sorted((left,right),key=lambda r:r['start'])
            between=[g for g in games if lo['end']<g.ended<hi['start']
                     and g.time_class==lo['time_class'] and g.time_control==lo['time_control'] and g.rated==lo['rated']]
            if (len(between)>=config.cluster_min_games
                    and min(lo['metrics']['decisions'],hi['metrics']['decisions'])>=config.high_cluster_decisions
                    and min(lo['metrics']['critical'],hi['metrics']['critical'])>=config.min_critical
                    and sum((rows_for([g],fast)[0].get('top1') or 0)<.65 and (rows_for([g],fast)[0].get('critical_top1') or 0)<.65 for g in between)>=config.cluster_min_games):
                recurrence=True
                if len(recurrence_groups)<40:
                    recurrence_groups.append({'time_class':lo['time_class'],'time_control':lo['time_control'],
                                              'rated':lo['rated'],'ids':lo['ids']+hi['ids']})
    finalists=[r for r in candidates if r['persistent']][:config.baseline_candidate_limit]
    for row in finalists:
        row['personal']=baseline_comparison(row,games,config,fast=fast)
        row['high_qualifying']=high_cluster_qualification(row,games,config)
    finalists.sort(key=lambda r:(r['high_qualifying'],r['personal']['established'],r['strength'],r['end']),reverse=True)
    best=finalists[0] if finalists else None
    discovery=max((r for r in candidates if r['kind']!='ranked'
                   and r['metrics']['eligible_games']>=config.high_cluster_games
                   and r['metrics']['decisions']>=config.high_cluster_decisions
                   and r['strength']>=.5),key=lambda r:r['strength'],default=None)
    return {'strongest': best, 'strongest_engine': max(candidates,key=lambda r:r['engine_score'],default=None),
            'strongest_critical': max(candidates,key=lambda r:r['critical_score'],default=None),
            'independent': independent, 'recurrence': recurrence, 'recurrence_groups':recurrence_groups,
            'candidates': finalists[:12] if finalists else candidates[:12], 'discovery':discovery}


def regime_changes(games, config=CONFIG):
    """Adjacent 5/8/10-game robust shifts, exact time control, sustained quality."""
    changes = []
    for (kind, control), group in buckets([g for g in games if g.rated is True]).items():
        for width in (5, 8, 10):
            for cut in range(width,len(group)-width+1):
                before, after = group[cut-width:cut], group[cut:cut+width]
                if not contiguous(before+after):continue
                a, b = summary(before,True), summary(after,True)
                old, new = a['robust_cpl'], b['robust_cpl']
                if old is None or new is None:continue
                spread = max(10, med([abs((rows_for([g],True)[0].get('robust_cpl') or 0)-old) for g in before],0))
                top_gain = (b['top1'] or 0)-(a['top1'] or 0)
                critical_gain = (b['critical_top1'] or 0)-(a['critical_top1'] or 0)
                sustained = sum((rows_for([g],True)[0].get('robust_cpl') or 0)<=old-15 for g in after)>=math.ceil(width*.8)
                if old-new>=max(25,2.5*spread) and sustained and (top_gain>=.15 or critical_gain>=.20):
                    changes.append({'class':kind,'control':control,'at':after[0].ended,'width':width,
                                    'before_cpl':old,'after_cpl':new,'top1_gain':top_gain,
                                    'critical_gain':critical_gain,'effect_mad':(old-new)/spread,
                                    'ids':[g.identity for g in after], 'elevated':True})
    return sorted(changes,key=lambda r:(r['effect_mad'],r['width']),reverse=True)[:12]


def review_candidate(clusters, config=CONFIG):
    if clusters['strongest'] is not None:return clusters['strongest']
    if clusters.get('discovery') is not None:return clusters['discovery']
    return max((r for r in clusters['candidates'] if r['kind']!='ranked'
                and r['metrics']['eligible_games']>=config.high_cluster_games
                and r['metrics']['decisions']>=config.high_cluster_decisions
                and r['strength']>=.5),key=lambda r:r['strength'],default=None)


def select_deep_games(games, config=CONFIG):
    games=[g for g in games if g.rated is True and not g.probe_only and 'decisions' in (g.fast_metrics or g.metrics)]
    clusters = find_clusters(games,config,True)
    lookup = {g.identity:g for g in games}
    selected = []
    def add(group, count):
        if count<=0:return
        for g in group:
            if len(selected)>=config.deep_games:return
            if not g.deep and g not in selected:
                selected.append(g);count-=1
                if count<=0:return
    # Near-threshold chronological candidates receive real confirmation data.
    # This selection does not itself qualify them for a HIGH priority.
    strongest = review_candidate(clusters,config)
    if strongest:
        members=[lookup[i] for i in strongest['ids'] if (lookup[i].fast_metrics or lookup[i].metrics).get('decisions',0)>=config.min_game_decisions]
        controls=representative_controls(comparable_baseline(strongest,games,config),config.baseline_deep_games)
        reserve=min(len(controls),config.baseline_deep_games, max(0,config.deep_games-5))
        count=min(len(members),config.deep_games-reserve)
        period=[members[round(i*(len(members)-1)/max(1,count-1))] for i in range(count)]
        if strongest['critical_score']>strongest['engine_score'] and count>=3:
            # Improve opportunity coverage, never choose by hit rate. Preserve
            # endpoint coverage and the ten-game budget including controls.
            anchors={members[0].identity,members[-1].identity}
            for candidate in sorted(members,key=lambda g:((g.fast_metrics or g.metrics).get('critical',0),g.ended),reverse=True):
                if sum((g.fast_metrics or g.metrics).get('critical',0) for g in period)>=config.min_deep_critical:break
                eligible=[g for g in period if g.identity not in anchors]
                worst=min(eligible,key=lambda g:(g.fast_metrics or g.metrics).get('critical',0),default=None)
                if worst and candidate not in period and (candidate.fast_metrics or candidate.metrics).get('critical',0)>(worst.fast_metrics or worst.metrics).get('critical',0):
                    period[period.index(worst)]=candidate
        add(period,count)
        add(controls,reserve)
    games=[g for g in games if (g.fast_metrics or g.metrics).get('decisions',0)>=config.min_game_decisions]
    add(sorted(games,key=lambda g:evidence(summary([g],True),config=config)[1],reverse=True),2)
    # A second independent period helps test recurrence instead of cherry-picking.
    for row in clusters['independent'][1:2]:add([lookup[i] for i in row['ids'] if (lookup[i].fast_metrics or lookup[i].metrics).get('decisions',0)>=config.min_game_decisions],2)
    add(sorted(games,key=lambda g:evidence(summary([g],True),config=config)[0],reverse=True),config.deep_games)
    return selected[:config.deep_games]


def confirm_cluster(cluster, games, config=CONFIG):
    if cluster is None:return {'confirmed':False,'engine':False,'critical':False,'games':0,'decisions':0,'critical_count':0,'stability':None}
    subset = [g for g in games if g.identity in cluster['ids'] and g.deep]
    deep, fast = summary(subset), summary(subset,True)
    rating=med([g.rating for g in subset])
    de,dc = evidence(deep,rating=rating,config=config,shrink=False);fe,fc = evidence(fast,rating=rating,config=config,shrink=False)
    # Discovery already accounts for the full period's sample size. Test
    # paired fast/deep effect retention on this smaller confirmation subset;
    # shrinking it again introduced a second, stricter sample-size penalty.
    # Explicit minimum games/decisions/critical opportunities still apply.
    stability = min(1.0,max(de,dc)/max(.001,max(fe,fc)))
    adequate = len(subset)>=5 and deep['decisions']>=config.min_deep_decisions
    engine_retention=min(1.0,de/max(.001,fe));critical_retention=min(1.0,dc/max(.001,fc))
    engine = adequate and de>=config.high_engine_score and engine_retention>=.8
    critical = adequate and deep['critical']>=config.min_deep_critical and dc>=config.high_critical_score and critical_retention>=.8
    baseline=comparable_baseline(cluster,games,config)
    deep_controls=[g for g in representative_controls(baseline,config.baseline_deep_games) if g.deep]
    fast_delta=baseline_comparison(cluster,games,config,fast=True)
    # Paired deep comparisons use the same reviewed cluster subset.
    paired={**cluster,'ids':[g.identity for g in subset]}
    deep_delta=baseline_comparison(paired,subset+deep_controls,config,fast=False,controls=deep_controls)
    paired_fast=baseline_comparison(paired,subset+deep_controls,config,fast=True,controls=deep_controls)
    anomaly_confirmed=(fast_delta['engine'] and paired_fast['engine'] and deep_delta['engine']) or (fast_delta['critical'] and paired_fast['critical'] and deep_delta['critical'])
    core='critical' if cluster['critical_score']>cluster['engine_score'] else 'engine'
    return {'confirmed':bool(critical if core=='critical' else engine),'engine':bool(engine),'critical':bool(critical),
            'games':len(subset),'decisions':deep['decisions'],'critical_count':deep['critical'],
            'anomaly_confirmed':bool(anomaly_confirmed),'baseline_games':len(deep_controls),
            'fast_personal':fast_delta,'paired_fast_personal':paired_fast,'deep_personal':deep_delta,'absolute_engine':de,'absolute_critical':dc,
            'fast_engine':fe,'fast_critical':fc,
            'stability':stability,'core':core,'engine_retention':engine_retention,'critical_retention':critical_retention,'metrics':deep,'fast_metrics':fast}
