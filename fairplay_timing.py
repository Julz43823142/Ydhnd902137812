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
    values = [v for v in values if v is not None and math.isfinite(v) and v >= 0]
    if not values:return {}
    center = max(sorted(set(round(v) for v in values)),
                 key=lambda c:(sum(abs(v-c)<=config.timing_band_halfwidth for v in values),-c))
    mean, deviation = stats.mean(values), stats.pstdev(values)
    bins = Counter(int(v//2) for v in values)
    fraction = sum(abs(v-center)<=config.timing_band_halfwidth for v in values)/len(values)
    cv = deviation/mean if mean>0 else None
    return {'modal_seconds':center,'cluster_fraction':fraction,'cv':cv,
            'entropy':-sum((n/len(values))*math.log2(n/len(values)) for n in bins.values()),
            'elevated':len(values)>=config.min_timing_moves and fraction>=config.timing_cluster_min
                       and cv is not None and cv<config.timing_cv_max}


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


def trivial_delay_metrics(decisions, config=CONFIG):
    groups = {'trivial':[],'normal':[],'critical':[]}
    kinds = Counter()
    for decision in decisions:
        if decision.think is None or not math.isfinite(decision.think) or decision.think<0:
            continue
        # Opening, missing/unsupported clocks and time trouble stay excluded.
        if decision.trivial_kind and (decision.clock_valid or decision.clock_reliable):
            groups['trivial'].append(decision.think);kinds[decision.trivial_kind] += 1
        elif decision.clock_reliable and decision.metrics.get('useful'):
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
                    and all(row.get('cv') is not None and row['cv']<config.timing_cv_max for row in samples.values()))
    return {'samples':samples,'overlap':overlap,'common_band_seconds':center,
            'cluster_by_category':fractions,'kinds':dict(kinds),'sufficient':sufficient,'elevated':elevated}


def trivial_delay_summary(games, config=CONFIG):
    pooled = trivial_delay_metrics([d for game in games for d in game.decisions],config)
    center = pooled['common_band_seconds']
    matches = [game for game in games if game.metrics.get('timing',{}).get('trivial_delay',{}).get('elevated')
               and center is not None and abs(game.metrics['timing']['trivial_delay']['common_band_seconds']-center)<=config.timing_band_halfwidth]
    pooled['same_cadence_games'] = len(matches)
    pooled['games_with_trivial_data'] = sum(bool(game.metrics.get('timing',{}).get('trivial_delay',{}).get('samples',{}).get('trivial',{}).get('count')) for game in games)
    pooled['recurrent'] = pooled['elevated'] and len(matches)>=config.trivial_recurrence_games
    return pooled
