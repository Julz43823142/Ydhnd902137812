"""Player-specific, same-control timing comparisons, not population judgments.

Groups use equal-budget fast engine summaries, never Chess.com accuracy or
timing itself. Exact base+increment buckets avoid speed/increment confounding.
Robust changes are supporting timing evidence, never a misconduct probability.
"""
import math
import statistics as stats
from collections import defaultdict

from fairplay_config import CONFIG
from fairplay_clusters import contiguous, comparison_control
from fairplay_timing import cadence, distribution_overlap


def median(values):
    usable = [v for v in values if v is not None and math.isfinite(v)]
    return stats.median(usable) if usable else None


def quantile(values, fraction):
    if not values:return None
    ordered = sorted(values);position = (len(ordered)-1)*fraction
    lo = math.floor(position);hi = math.ceil(position)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(position-lo)


def profile_stats(values, config=CONFIG):
    """Keep near-instant counts; dispersion excludes premove-like samples."""
    center = median(values)
    delayed = [v for v in values if v>config.premove_seconds]
    delayed_center = median(delayed)
    return {**cadence(delayed,config), 'count':len(values), 'median':center,
            'mad':median([abs(v-center) for v in values]) if values else None,
            'q25':quantile(values,.25), 'q75':quantile(values,.75),
            'near_instant_fraction':sum(v<=config.premove_seconds for v in values)/len(values) if values else None,
            'delayed_median':delayed_center,
            'delayed_mad':median([abs(v-delayed_center) for v in delayed]) if delayed else None,
            'iqr':quantile(delayed,.75)-quantile(delayed,.25) if delayed else None}


def timing_values(game, config=CONFIG):
    groups = {k:[] for k in ('opening','middlegame','ordinary','critical','trivial','overall')}
    for d in game.decisions:
        if not d.clock_valid or d.think is None or not math.isfinite(d.think) or d.think<0:
            continue
        groups['overall'].append(d.think)
        if d.phase=='opening':groups['opening'].append(d.think)
        elif d.trivial_kind:groups['trivial'].append(d.think)
        else:
            groups['critical' if d.metrics.get('critical') else 'ordinary'].append(d.think)
            if d.phase=='middlegame':groups['middlegame'].append(d.think)
    return groups


def timing_profile(games, config=CONFIG):
    per_game = [timing_values(g,config) for g in games]
    pooled = {k:[v for row in per_game for v in row[k]] for k in per_game[0]} if per_game else {}
    profiles = {k:profile_stats(v,config) for k,v in pooled.items()}
    # Equal game weight in comparisons; one long game cannot dominate a group.
    summaries = [profile_stats(row['overall'],config) for row in per_game]
    compare = {k:median([row.get(k) for row in summaries])
               for k in ('delayed_mad','iqr','cv','entropy','cluster_fraction','modal_seconds')}
    response = []
    for row in per_game:
        critical = [v for v in row['critical'] if v>config.premove_seconds]
        ordinary = [v for v in row['ordinary'] if v>config.premove_seconds]
        if len(critical)>=3 and len(ordinary)>=6:
            response.append(stats.median(critical)-stats.median(ordinary))
    return {'games':len(games),'categories':profiles,'comparison':compare,
            'critical_extra_seconds':median(response),
            'critical_ordinary_overlap':distribution_overlap(pooled.get('critical',[]),pooled.get('ordinary',[]))}


def engine_data(game):
    return game.fast_metrics or game.metrics


def engine_interest(game):
    m = engine_data(game)
    # Constant bounded components: no timing, results or API accuracy inputs.
    critical = m.get('critical_top1') if m.get('critical',0)>=3 else None
    unique = m.get('unique_hits',0)/m['unique'] if m.get('unique',0)>=3 else None
    return (2*(m.get('top1') or 0)+(critical or 0)+(unique or 0)*.5
            -min(m.get('median_cpl') if m.get('median_cpl') is not None else 150,150)/100)


def quality_profile(games):
    rows = [engine_data(g) for g in games]
    return {'cpl':median([m.get('robust_cpl',m.get('median_cpl')) for m in rows]),
            'top1':median([m.get('top1') for m in rows]),
            'critical':median([m.get('critical_top1') for m in rows if m.get('critical',0)>=3]),
            'mistake_rate':median([m['mistakes']/m['decisions'] for m in rows if m.get('decisions',0) and m.get('mistakes') is not None]),
            'unique':median([m.get('unique_hits',0)/m['unique'] for m in rows if m.get('unique',0)>=3])}


def increase(a,b,threshold):
    return a is not None and b is not None and b-a>=threshold


def compare_groups(baseline, high, config=CONFIG):
    before,after = timing_profile(baseline,config),timing_profile(high,config)
    bq,hq = quality_profile(baseline),quality_profile(high)
    sufficient = (len(baseline)>=config.baseline_min_games and len(high)>=config.baseline_min_games
                  and all(sum(d.clock_valid for d in g.decisions)>=config.baseline_min_timing for g in baseline+high))
    gains = {'engine_agreement':increase(bq['top1'],hq['top1'],config.baseline_top1_gap),
             'critical_precision':increase(bq['critical'],hq['critical'],config.baseline_critical_gap),
             'unique_hits':increase(bq['unique'],hq['unique'],config.baseline_critical_gap),
             'fewer_mistakes':increase(hq['mistake_rate'],bq['mistake_rate'],.03)}
    lower_cpl = (bq['cpl'] is not None and hq['cpl'] is not None and bq['cpl']-hq['cpl']>=config.baseline_cpl_gap)
    consistently_better = (lower_cpl and sum(engine_data(g).get('robust_cpl') is not None and engine_data(g)['robust_cpl']<=bq['cpl']-config.baseline_cpl_gap/2
                                            for g in high)>=math.ceil(len(high)*.8))
    quality_gain = sufficient and consistently_better and any(gains.values())
    bc,hc = before['comparison'],after['comparison']
    high_shapes = [profile_stats(timing_values(g,config)['overall'],config) for g in high]
    def shrink(metric):
        a,b = bc[metric],hc[metric]
        return a is not None and b is not None and a>=1 and b<=a*config.baseline_spread_ratio
    narrower = (shrink('delayed_mad') and shrink('iqr')
                and sum(row['delayed_mad'] is not None and row['delayed_mad']<=bc['delayed_mad']*config.baseline_spread_ratio
                        for row in high_shapes)>=math.ceil(len(high)*config.baseline_consistency))
    entropy = increase(hc['entropy'],bc['entropy'],config.baseline_entropy_drop)
    cluster = increase(bc['cluster_fraction'],hc['cluster_fraction'],config.baseline_cluster_gain)
    cadence_gain = (hc['modal_seconds'] is not None and hc['modal_seconds']>=config.trivial_delay_seconds
                    and hc['cluster_fraction'] is not None and hc['cluster_fraction']>=config.baseline_band_min
                    and cluster and entropy
                    and sum(row.get('cluster_fraction',0)>=config.baseline_band_min
                            and row.get('modal_seconds',0)>=config.trivial_delay_seconds
                            for row in high_shapes)>=math.ceil(len(high)*config.baseline_consistency))
    response_lost = (before['critical_extra_seconds'] is not None and after['critical_extra_seconds'] is not None
                     and before['critical_extra_seconds']>=1.5
                     and before['critical_extra_seconds']-after['critical_extra_seconds']>=config.baseline_response_drop)
    category_delays = []
    for category in ('opening','trivial'):
        a,b = before['categories'].get(category,{}),after['categories'].get(category,{})
        if min(a.get('count',0),b.get('count',0))>=config.baseline_category_moves:
            if (a['near_instant_fraction']>=.4 and b['near_instant_fraction']<=.1
                    and b['median']>=config.trivial_delay_seconds):category_delays.append(category)
    points = int(narrower)+int(cadence_gain)+int(response_lost)+int(bool(category_delays))
    state = ('Insufficient Data' if not sufficient else 'Normal' if not quality_gain or points==0
             else ('Slight','Moderate','Strong','Very Strong')[points-1])
    reasons = []
    if narrower:reasons.append('Robust timing dispersion decreased compared with the player’s baseline.')
    if cadence_gain:reasons.append('A delayed narrow cadence replaced a more variable timing style.')
    if response_lost:reasons.append('The player’s usual extra time on critical decisions decreased.')
    if category_delays:reasons.append('Previously near-instant '+ '/'.join(category_delays)+' decisions became delayed.')
    if not quality_gain:reasons=[]
    return {'state':state,'sufficient':sufficient,'baseline_ids':[g.identity for g in baseline],'high_ids':[g.identity for g in high],'baseline':before,'high_signal':after,
            'baseline_quality':bq,'high_signal_quality':hq,'quality_gain':quality_gain,
            'quality_changes':gains,'reasons':reasons}


def personal_timing(games, config=CONFIG):
    buckets = defaultdict(list)
    for game in games:
        if (game.time_control and engine_data(game).get('decisions',0)>=config.baseline_min_decisions
                and sum(d.clock_valid for d in game.decisions)>=config.baseline_min_timing):
            buckets[(game.time_class,comparison_control(game))].append(game)
    ranks = {'Insufficient Data':-1,'Normal':0,'Slight':1,'Moderate':2,'Strong':3,'Very Strong':4}
    results = []
    for (kind,control),group in sorted(buckets.items()):
        group = sorted(group,key=lambda g:(g.ended,g.identity))
        # Bottom half versus highest third, disjoint, at least six each.
        base_count = len(group)//2
        high_count = min(max(config.baseline_min_games,len(group)//3),len(group)-base_count)
        ranked = sorted(group,key=lambda g:(engine_interest(g),g.ended,g.identity))
        lower,high = ranked[:base_count],ranked[-high_count:]
        comparison = compare_groups(lower,high,config)
        best_change = None
        window = max(config.baseline_window,config.baseline_min_games)
        # Compare complete adjacent blocks, never two isolated games.
        for split in range(window,len(group)-window+1):
            if not contiguous(group[split-window:split+window]):continue
            change = compare_groups(group[split-window:split],group[split:split+window],config)
            if ranks[change['state']]>=2 and (best_change is None or ranks[change['state']]>ranks[best_change['state']]):
                best_change = {**change,'boundary':group[split].ended}
        state = max([comparison['state'],best_change['state'] if best_change else 'Insufficient Data'],key=ranks.get)
        results.append({'time_class':kind,'time_control':control,'games':len(group),
                        **comparison,'state':state,'chronological_shift':best_change})
    return results
