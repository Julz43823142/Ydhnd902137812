"""Correspondence screening: paired gameplay changes, never live-clock inference.

Daily players can consult opening references and spend days on a decision.
Live-trained Maia probabilities and live Elo quality expectations therefore
cannot establish a Daily HIGH. The initial route compares the same player's
completed Daily games against prior Daily games at comparable ratings. It
requires replicated, deep-confirmed difficult decisions and includes misses.
Thresholds are transparent heuristics awaiting correspondence calibration.
"""
from math import sqrt
from statistics import median
from fairplay_config import CONFIG


def interval(hits, n, z=1.645):
    if not n:
        return 0.0, 1.0
    p = hits / n
    center = p + z*z/(2*n)
    radius = z*sqrt(p*(1-p)/n + z*z/(4*n*n))
    denominator = 1 + z*z/n
    return (center-radius)/denominator, (center+radius)/denominator


def game_evidence(game):
    opportunities = []
    for decision in game.decisions:
        m = decision.metrics
        # Deep geometry and useful status already exclude book, forced,
        # automatic material gain and easy conversions. Keep explicit guards.
        if (not game.deep or not m.get('useful') or not m.get('competitive')
                or m.get('easy_conversion') or m.get('automatic_material_gain')
                or decision.forced or decision.phase == 'opening'
                or m.get('difficulty', 0) < .65 or not m.get('critical')
                or m.get('search_inconsistent') or m.get('search_depth', 0) < 18):
            continue
        opportunities.append(m)
    # Equal per-game bounds stop a single long game dominating the period.
    # Retain the first opportunities chronologically, including misses.
    opportunities = opportunities[:12]
    hits = sum(m.get('near_best', False) and m.get('cpl', 9999) <= 15
               for m in opportunities)
    measured = sum(m.get('search_stability', {}).get('compared') is True for m in opportunities)
    paired = sum(m.get('search_stability', {}).get('compared') is True
                 and m.get('search_stability', {}).get('objective_quality_preserved') is True
                 for m in opportunities)
    return {'opportunities': len(opportunities), 'hits': hits, 'paired': paired, 'measured': measured,
            'rate': hits/len(opportunities) if opportunities else None}


def summarize(games, config=CONFIG):
    rows = [game_evidence(g) for g in games]
    n = sum(r['opportunities'] for r in rows)
    hits = sum(r['hits'] for r in rows)
    lo, hi = interval(hits, n, config.rate_lower_bound_z)
    return {'games': len(games), 'opportunities': n, 'hits': hits,
            'paired': sum(r['paired'] for r in rows),
            'measured': sum(r['measured'] for r in rows),
            'reliable_games':sum(r['opportunities']>0
                and r['measured']>=config.daily_paired_coverage*r['opportunities']
                and r['paired']>=config.daily_quality_stability*r['opportunities'] for r in rows),
            'lower': lo, 'upper': hi,
            'median_game_quality': median([r['rate'] for r in rows if r['rate'] is not None])
                                   if n else 0.0,
            'contributors': sum(r['opportunities'] >= config.daily_min_game_opportunities
                                and r['rate'] >= .85 for r in rows)}


def review(games, config=CONFIG):
    rows = sorted({g.identity: g for g in games
                   if g.time_class == 'daily' and g.rated is True
                   and not g.probe_only}.values(), key=lambda g: (g.ended, g.identity))
    best = None
    # Only real, contiguous completion windows; controls remain separate.
    for end in range(config.daily_period_games, len(rows)+1):
        period = rows[end-config.daily_period_games:end]
        if not period[0].time_control or len({g.time_control for g in period}) != 1:
            continue
        control = period[0].time_control
        ratings = [g.rating for g in period if g.rating is not None]
        opponents = [g.opponent_rating for g in period if g.opponent_rating is not None]
        reference = rows[:end-config.daily_period_games]
        baseline = [g for g in reference if g.time_control == control
                    and g.rating is not None and g.opponent_rating is not None
                    and len(ratings) == len(period) and len(opponents) == len(period)
                    and abs(g.rating-median(ratings)) <= 200
                    and abs(g.opponent_rating-median(opponents)) <= 250][-30:]
        current, previous = summarize(period, config), summarize(baseline, config)
        indices=[getattr(g,'class_index',None) for g in period]
        consecutive=(all(i is None for i in indices) or
                     all(isinstance(i,int) for i in indices)
                     and all(b==a+1 for a,b in zip(indices,indices[1:])))
        gates = {
            'complete chronological period': consecutive,
            'replicated difficult games': current['contributors'] == len(period),
            'difficult opportunity coverage': current['opportunities'] >= config.daily_min_opportunities,
            'deep-confirmed quality lower bound': current['lower'] >= config.daily_hit_lower,
            'paired objective-quality coverage': current['measured'] >= config.daily_paired_coverage*max(1,current['opportunities']),
            'paired baseline coverage': previous['measured'] >= config.daily_paired_coverage*max(1,previous['opportunities']),
            'reliable candidate games': current['reliable_games']==len(period),
            'baseline search reliability': previous['paired']>=config.daily_quality_stability*max(1,previous['opportunities']),
            'prior daily comparison games': len(baseline) >= config.daily_min_baseline_games,
            'prior daily opportunity coverage': previous['opportunities'] >= config.daily_min_opportunities,
            'baseline is materially different': previous['upper'] <= config.daily_baseline_upper,
            'bounded per-game quality change': current['median_game_quality']-previous['median_game_quality']
                                               >= config.daily_min_quality_shift,
        }
        candidate = {'gates': gates, 'period': current, 'baseline': previous,
                     'supported': all(gates.values())}
        if best is None or (sum(gates.values()), current['opportunities']) > (
                sum(best['gates'].values()), best['period']['opportunities']):
            best = candidate
    return {'rated_games': len(rows), 'deep_reviewed': sum(g.deep for g in rows),
            'timing_used': False, 'learned_policy_scoring_used': False,
            'best': best, 'supported': bool(best and best['supported']),
            'limitations': 'Correspondence permits long deliberation and opening references. '
                          'Live-trained Maia is descriptive only; no calibrated Daily human model. '
                          'Completion order does not imply independent play sessions.'}


def integrate(result, games, config=CONFIG, *, context_games=()):
    daily = [g for g in games if g.time_class == 'daily' and g.rated is True]
    audit = review(daily, config)
    audit['context_games'] = sum(g.time_class == 'daily' for g in context_games)
    result.diagnostics['daily_review'] = audit
    if not daily:
        return result
    from fairplay_analysis import aggregate
    result.classes['daily'] = aggregate(daily)
    usable=[g for g in daily if g.metrics.get('decisions',0)>=config.min_game_decisions]
    result.games = list({g.identity: g for g in (*result.games, *usable)}.values())
    result.totals = aggregate(result.games)
    result.coverage['used'] = len(result.games)
    result.families['Daily Gameplay'] = 'Elevated' if audit['supported'] else 'Not established'
    if audit['supported'] and result.priority not in ('HIGH', 'VERY HIGH'):
        result.priority = 'HIGH'
        result.confidence = 'MEDIUM'  # model/domain and overlapping games limit confidence
        result.reasons = ['A repeated change in difficult Daily decisions was confirmed '
                          'at depth 18 against comparable prior Daily games. Long deliberation '
                          'and permitted study resources remain relevant to manual review.']
    elif (result.priority == 'INSUFFICIENT DATA'
          and len(usable) >= config.min_games):
        result.priority = 'LOW'
        result.confidence = 'MEDIUM' if sum(g.deep for g in daily) >= config.min_games else 'LOW'
        result.reasons = ['No supported Daily gameplay change was established. '
                          'The correspondence reference is limited; this does not establish fair play.']
    return result
