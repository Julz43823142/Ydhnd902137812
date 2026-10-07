"""Within-game delay comparison without assuming one absolute cross-game pace.

Normalize each game's valid clocks by its ordinary-move median. This can retain
a repeated easy/hard timing relationship when the absolute delay varies between
games. It does not identify a tool or prove assistance. Premoves and misses stay
in denominators, and no games are selected by whether their timing looks unusual.
"""
import math
import statistics

from fairplay_config import CONFIG
from fairplay_timing import decision_trivial_kind, distribution_overlap


def normalized_delay_profile(games, config=CONFIG):
    from fairplay_clusters import comparison_control
    keys={(g.time_class,comparison_control(g),g.rated) for g in games}
    compatible=(len(keys)==1 and all(g.time_control for g in games) and next(iter(keys))[0] in ('rapid','blitz')
                and bool(next(iter(keys))[1]) and next(iter(keys))[2] is True)
    normalized={k:[] for k in ('trivial','normal','critical')}
    original={k:[] for k in normalized}
    contributors={k:set() for k in normalized}
    anchors=[];shared=set();seen=set()
    for game in games if compatible else []:
        if game.identity in seen or game.probe_only:continue
        seen.add(game.identity)
        groups={k:[] for k in normalized}
        for d in game.decisions:
            if (d.phase=='opening' or not d.clock_valid or d.think is None
                    or not math.isfinite(d.think) or d.think<0):continue
            kind=('trivial' if decision_trivial_kind(d) else
                  'critical' if d.metrics.get('critical') else 'normal')
            groups[kind].append(d.think)
        if len(groups['normal'])<config.local_timing_anchor_moves:continue
        anchor=statistics.median(groups['normal'])
        if anchor<config.timing_min_delay:continue
        anchors.append(anchor)
        if all(groups.values()):shared.add(game.identity)
        for kind,values in groups.items():
            if values:contributors[kind].add(game.identity)
            original[kind].extend(values)
            normalized[kind].extend(value/anchor for value in values)
    count=len(seen)
    required_shared=max(config.convergence_shared_games,math.ceil(count/3))
    samples={}
    for kind,values in normalized.items():
        median=statistics.median(values) if values else None
        raw=original[kind]
        samples[kind]={'count':len(values),'games':len(contributors[kind]),
            'median':median,'mad':statistics.median(abs(v-median) for v in values) if values else None,
            'raw_median':statistics.median(raw) if raw else None,
            'near_instant':sum(v<=config.premove_seconds for v in raw)/len(raw) if raw else None}
    overlap={f'{a}_{b}':distribution_overlap(
        [v/config.local_timing_bin for v in normalized[a]],
        [v/config.local_timing_bin for v in normalized[b]])
        for a,b in (('trivial','normal'),('trivial','critical'),('normal','critical'))}
    sufficient=(compatible and len(anchors)>=config.delay_floor_min_games
        and len(shared)>=required_shared
        and len(normalized['trivial'])>=config.delay_floor_min_trivial
        and len(normalized['normal'])>=config.delay_floor_min_normal
        and len(normalized['critical'])>=config.delay_floor_min_critical
        and min(len(v) for v in contributors.values())>=max(3,math.ceil(count/3)))
    delayed=bool(sufficient and all(row['raw_median']>=config.timing_min_delay
        and row['near_instant']<=config.delay_floor_max_instant for row in samples.values()))
    elevated=bool(delayed and max(r['median'] for r in samples.values())/min(r['median'] for r in samples.values())<=config.delay_floor_median_ratio
        and all(r['mad']/r['median']<=config.delay_floor_relative_mad for r in samples.values())
        and min(overlap.values())>=config.delay_floor_overlap)
    return {'sufficient':sufficient,'elevated':elevated,'games':count,
        'anchor_games':len(anchors),'shared_games':len(shared),'required_shared_games':required_shared,
        'anchor_range':[min(anchors),max(anchors)] if anchors else None,
        'samples':samples,'overlap':overlap,
        'score':(.75 if len(anchors)>=12 else .55) if elevated else 0.0}


def clock_delay_evidence(games, config=CONFIG):
    from fairplay_timing import delay_floor_profile
    raw=delay_floor_profile(games,config)
    local=normalized_delay_profile(games,config)
    return {**raw,'raw_elevated':raw['elevated'],'normalized':local,
        'method':'absolute' if raw['elevated'] else 'within-game' if local['elevated'] else None,
        'elevated':raw['elevated'] or local['elevated'],
        'score':max(raw['score'],local['score'])}
