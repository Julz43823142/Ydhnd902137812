"""Transparent clock comparisons; structural triviality is a proxy, not proof.

No engine queries: obvious decisions retain zero engine-matching weight. Timing
is compared within a time class and only repeated, adequately sampled patterns
support the existing timing family. Near-instant trivial moves stay in its
distribution, preventing cherry-picking just the unusually slow recaptures.
"""
import math
import statistics as stats
from collections import Counter

import chess
from fairplay_config import CONFIG


def trivial_move_kind(board, move, legal_count, recapture=False):
    """Conservative structural proxies; never classify every check/capture easy."""
    if legal_count == 1:
        return 'only legal move'
    if board.is_check() and legal_count <= 2:
        return 'near-forced check response'
    moving, captured = board.piece_at(move.from_square), board.piece_at(move.to_square)
    values = {chess.PAWN:1, chess.KNIGHT:3, chess.BISHOP:3, chess.ROOK:5, chess.QUEEN:9, chess.KING:100}
    if recapture and not board.is_check() and not board.gives_check(move) and moving and captured and values[moving.piece_type] <= values[captured.piece_type]:
        choices = [m for m in board.legal_moves if m.to_square == move.to_square and board.is_capture(m)]
        if len(choices) == 1:
            return 'obvious recapture'
    if (moving and captured and moving.piece_type == captured.piece_type
            and moving.piece_type in (chess.KNIGHT,chess.BISHOP,chess.ROOK,chess.QUEEN)
            and not board.is_check() and not board.gives_check(move) and not move.promotion):
        after = board.copy(stack=False);after.push(move)
        replies = [m for m in after.legal_moves if m.to_square == move.to_square and after.is_capture(m)]
        if len(replies) == 1:
            reply_piece = after.piece_at(replies[0].from_square)
            if reply_piece and values[reply_piece.piece_type] <= values[moving.piece_type]:
                return 'simple equal-piece exchange'
    # Immediate mate on a sparse board is the only tactical-continuation proxy.
    # Complicated sacrifices/checks are not assumed obvious from shallow scores.
    if len(board.piece_map()) <= 6 and board.gives_check(move):
        after = board.copy(stack=False);after.push(move)
        if after.is_checkmate():
            return 'immediate sparse-board mate'
    return None


def cadence(values, config=CONFIG):
    """Robust delayed-band shape, not a test against a magic five-second value.

    A 1.5-second mode gets a narrower band than a 5-second mode. Occasional
    long pauses no longer destroy an otherwise repeated cadence through CV.
    Raw CV stays descriptive; median/MAD and band coverage drive the flag.
    Premoves remain in the denominator and cannot become delayed evidence.
    """
    from bisect import bisect_left, bisect_right
    values = sorted(v for v in values if v is not None and math.isfinite(v) and v >= 0)
    if not values:return {}
    middle = stats.median(values)
    def width(center):return min(config.timing_band_halfwidth,max(.1,center*config.timing_relative_band))
    def count(center):return bisect_right(values,center+width(center))-bisect_left(values,center-width(center))
    center = max(sorted(set(round(v,1) for v in values)),key=lambda c:(count(c),-abs(c-middle),-c))
    mean, deviation = stats.mean(values), stats.pstdev(values)
    mad = stats.median(abs(v-middle) for v in values)
    bins = Counter(int(v//2) for v in values)
    fraction = count(center)/len(values)
    cv = deviation/mean if mean>0 else None
    robust_cv = 1.4826*mad/middle if middle>0 else None
    return {'modal_seconds':center,'band_halfwidth':width(center),'cluster_fraction':fraction,
            'median':middle,'mad':mad,'cv':cv,'robust_cv':robust_cv,
            'near_instant_fraction':sum(v<=config.premove_seconds for v in values)/len(values),
            'entropy':-sum((n/len(values))*math.log2(n/len(values)) for n in bins.values()),
            'elevated':len(values)>=config.min_timing_moves and center>=config.timing_min_delay
                       and fraction>=config.timing_cluster_min
                       and robust_cv is not None and robust_cv<config.timing_cv_max}


def clock_values(game):
    """Clock evidence does not disappear when the position is already won."""
    return [d.think for d in game.decisions if d.phase!='opening' and d.clock_valid
            and d.think is not None and math.isfinite(d.think) and d.think>=0]


def cadence_recurrence(games, config=CONFIG):
    """Recurrence within a caller-supplied exact-control/rated bucket.

    Individual short games need eight clocks; the group needs five games and
    eighty clocks. A minority period stays visible against a variable baseline.
    These are descriptive screening gates, not population-calibrated p-values.
    """
    rows=[]
    for game in games:
        values=clock_values(game);shape=cadence(values,config)
        if (len(values)>=config.timing_min_game_moves and shape.get('modal_seconds',0)>=config.timing_min_delay
                and shape.get('cluster_fraction',0)>=config.timing_recurrence_fraction
                and shape.get('robust_cv') is not None and shape['robust_cv']<config.timing_cv_max):
            rows.append((game,values,shape))
    candidates=[]
    for _,_,shape in rows:
        center=shape['modal_seconds'];width=shape['band_halfwidth']
        matched=[(g,v,m) for g,v,m in rows if abs(m['modal_seconds']-center)<=width
                 and sum(abs(t-center)<=width for t in v)/len(v)>=config.timing_recurrence_fraction]
        if not matched:continue  # guard floating-point band-edge disagreement
        count=sum(len(v) for _,v,_ in matched)
        candidates.append({'games':len(matched),'moves':count,'ids':[g.identity for g,_,_ in matched],
                           'modal_seconds':center,'band_halfwidth':width,
                           'fraction':stats.median([sum(abs(t-center)<=width for t in v)/len(v) for _,v,_ in matched]),
                           'recurrent':len(matched)>=config.timing_recurrence_games and count>=config.timing_recurrence_moves})
    best=max(candidates,key=lambda r:(r['recurrent'],r['games'],r['fraction']),default=None)
    return best or {'games':0,'moves':0,'ids':[],'modal_seconds':None,'band_halfwidth':None,'fraction':None,'recurrent':False}


def distribution_overlap(left, right):
    """0..1 histogram intersection with linear one-second bin membership.

    Splitting samples across adjacent integer bins avoids a discontinuity where
    4.99s and 5.01s would otherwise look like wholly disjoint distributions.
    This is descriptive overlap, never a probability of misconduct.
    """
    if not left or not right:return None
    def histogram(values):
        bins = Counter()
        for value in values:
            lower = math.floor(value);fraction = value-lower
            bins[lower] += (1-fraction)/len(values)
            bins[lower+1] += fraction/len(values)
        return bins
    a,b = histogram(left),histogram(right)
    return min(1.0,max(0.0,sum(min(a[k],b[k]) for k in a.keys()|b.keys())))


def decision_trivial_kind(decision):
    return decision.trivial_kind or ('obvious material gain' if decision.metrics.get('automatic_material_gain') and decision.phase!='opening' else None)


def trivial_delay_metrics(decisions, config=CONFIG):
    groups = {'trivial':[],'normal':[],'critical':[]}
    kinds = Counter()
    for decision in decisions:
        if decision.think is None or not math.isfinite(decision.think) or decision.think<0:
            continue
        # Opening, missing/unsupported clocks and time trouble stay excluded.
        trivial=decision_trivial_kind(decision)
        if trivial and (decision.clock_valid or decision.clock_reliable):
            groups['trivial'].append(decision.think);kinds[trivial] += 1
        elif decision.clock_valid and decision.phase!='opening':
            groups['critical' if decision.metrics.get('critical') else 'normal'].append(decision.think)
    samples = {}
    for kind,values in groups.items():
        samples[kind] = {'count':len(values),'median':stats.median(values) if values else None,
                         'near_instant':sum(v<=config.premove_seconds for v in values),
                         'delayed':sum(v>=config.trivial_delay_seconds for v in values),
                         **cadence(values,config)}
    combined = [v for values in groups.values() for v in values]
    center = cadence(combined,config).get('modal_seconds')
    overlap = {f'{a}_{b}':distribution_overlap(groups[a],groups[b])
               for a,b in (('trivial','normal'),('trivial','critical'),('normal','critical'))}
    sufficient = (len(groups['trivial'])>=config.trivial_min_moves
                  and len(groups['normal'])>=config.normal_min_moves
                  and len(groups['critical'])>=config.critical_min_moves
                  and len(combined)>=config.trivial_total_min)
    fractions = {k:sum(abs(v-center)<=config.timing_band_halfwidth for v in values)/len(values)
                 if values and center is not None else None for k,values in groups.items()}
    medians = [row['median'] for row in samples.values() if row['median'] is not None]
    elevated = bool(sufficient and center is not None and center>=config.trivial_delay_seconds
                    and max(medians)-min(medians)<=config.trivial_median_max_gap
                    and all(v is not None and v>=config.trivial_overlap_min for v in overlap.values())
                    and all(v is not None and v>=config.timing_cluster_min for v in fractions.values())
                    and all(row.get('robust_cv') is not None and row['robust_cv']<config.timing_cv_max for row in samples.values()))
    return {'samples':samples,'overlap':overlap,'common_band_seconds':center,
            'cluster_by_category':fractions,'kinds':dict(kinds),'sufficient':sufficient,'elevated':elevated}


def trivial_delay_summary(games, config=CONFIG):
    # Pool only a recurrent same-cadence period. Requiring six trivial and four
    # critical decisions in EACH game discarded most real, shorter games.
    recurrence=cadence_recurrence(games,config)
    matched=[g for g in games if g.identity in recurrence['ids']]
    subset=matched if len(matched)>=config.trivial_recurrence_games else games
    pooled = trivial_delay_metrics([d for game in subset for d in game.decisions],config)
    center = pooled['common_band_seconds']
    matches=[]
    for game in matched:
        m=trivial_delay_metrics(game.decisions,config)
        # Every counted game must contribute to all three categories; one game
        # cannot supply all of the critical clocks for unrelated trivial games.
        if (all(m['samples'][k]['count'] for k in ('trivial','normal','critical'))
                and center is not None and abs((m['common_band_seconds'] or 0)-center)<=config.timing_band_halfwidth):
            matches.append(game)
    pooled['same_cadence_games'] = len(matches)
    pooled['games_with_trivial_data'] = sum(any(decision_trivial_kind(d) and d.clock_valid for d in g.decisions) for g in games)
    pooled['recurrent'] = pooled['elevated'] and len(matches)>=config.trivial_recurrence_games
    return pooled


def delay_floor_profile(games, config=CONFIG):
    """Repeated comparable non-instant delays, even with occasional long pauses.

    This differs from an 80%-in-one-band cadence: the lower spread and typical
    times of easy and hard decisions can match without every move being fixed.
    It is a timing-only supporting feature, never an independent verdict.
    No category may borrow clocks from another control or rated/casual group.
    """
    groups={k:[] for k in ('trivial','normal','critical')}
    contributors={k:set() for k in groups}
    for game in games:
        for d in game.decisions:
            if not d.clock_valid or d.think is None or not math.isfinite(d.think) or d.think<0 or d.phase=='opening':continue
            category='trivial' if decision_trivial_kind(d) else 'critical' if d.metrics.get('critical') else 'normal'
            groups[category].append(d.think);contributors[category].add(game.identity)
    profiles={}
    for key,values in groups.items():
        center=stats.median(values) if values else None
        profiles[key]={'count':len(values),'games':len(contributors[key]),'median':center,
                       'mad':stats.median(abs(v-center) for v in values) if values else None,
                       'near_instant':sum(v<=config.premove_seconds for v in values)/len(values) if values else None}
    sufficient=(len(games)>=config.delay_floor_min_games and len(groups['trivial'])>=config.delay_floor_min_trivial
                and len(groups['normal'])>=config.delay_floor_min_normal and len(groups['critical'])>=config.delay_floor_min_critical
                and min(len(v) for v in contributors.values())>=max(3,math.ceil(len(games)/3)))
    overlaps={f'{a}_{b}':distribution_overlap(groups[a],groups[b]) for a,b in (('trivial','normal'),('trivial','critical'),('normal','critical'))}
    delayed=bool(sufficient and all(r['median']>=config.timing_min_delay and r['near_instant']<=config.delay_floor_max_instant for r in profiles.values()))
    similar=bool(delayed and max(r['median'] for r in profiles.values())/min(r['median'] for r in profiles.values())<=config.delay_floor_median_ratio
                 and all(r['mad']/r['median']<=config.delay_floor_relative_mad for r in profiles.values())
                 and min(overlaps.values())>=config.delay_floor_overlap)
    return {'sufficient':sufficient,'elevated':similar,'games':len(games),'samples':profiles,'overlap':overlaps,
            'score':(.75 if len(games)>=12 else .55) if similar else 0.0}


def delay_floor_periods(games, config=CONFIG):
    """Bounded chronological search; descriptive, no multiple-testing p-value."""
    from fairplay_clusters import contiguous
    from fairplay_local_timing import clock_delay_evidence
    best=clock_delay_evidence(games,config)
    best['ids']=[g.identity for g in games]
    # One full group plus non-overlapping and half-overlapping periods. Many
    # neighboring windows must not become many 'independent' timing signals.
    for width in (6,12,20):
        for start in range(0,len(games)-width+1,max(1,width//2)):
            group=games[start:start+width]
            if not contiguous(group):continue
            row=clock_delay_evidence(group,config)
            if (row['score'],row['games'])>(best['score'],best['games']):
                best={**row,'ids':[g.identity for g in group]}
    return best
