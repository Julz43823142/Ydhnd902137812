"""Independent HIGH-priority gate for a recent ten-game cheating *hypothesis*.

The ordinary broad path expects dozens of multi-hit contributor games. The
acute path requires >=6 difficult decisions in *every* one of 2–8 games. A
ten-game period with two to four genuinely difficult choices in each game is
therefore untestable by either route, even with exceptionally precise moves.

Here ten consecutive rated games from the same time-control stratum are
scored together. Two disjoint, fixed five-game halves must each contain
substantial paired fast/deep hard-decision evidence. ALL latest fifty rated
games are the search universe (up to forty-one contiguous ten-game windows);
only
FAST data may establish a discovery candidate. Deep proof is independently
recomputed on the exact entire window, including missed moves.

HIGH is **human-review priority only**, not a probability of cheating. This
heuristic requires independent fair-player calibration before release or use
in automatic sanctions. A single ten-game event never yields VERY HIGH.
"""
from __future__ import annotations

import math
from collections import defaultdict

from fairplay_config import CONFIG
from fairplay_confirmation import paired_quality_confirmation
from fairplay_human import period_summary

WINDOW_GAMES = 10
LATEST_RATED_SCOPE = 50


def _verified_entry(game):
    return (getattr(game, 'rated', None) is True
            and not getattr(game, 'probe_only', False)
            and getattr(game, 'deep', False) is True
            and getattr(game, 'time_class', '') in ('blitz', 'rapid')
            and isinstance(getattr(game, 'control_index', None), int)
            and not isinstance(getattr(game, 'control_index', None), bool)
            and game.control_index >= 0
            and isinstance(getattr(game, 'ended', None), (int, float))
            and math.isfinite(game.ended)
            and bool(getattr(game, 'time_control', ''))
            and isinstance(getattr(game, 'rating', None), int)
            and 100 <= game.rating <= 4000
            and isinstance(getattr(game, 'opponent_rating', None), int)
            and 100 <= game.opponent_rating <= 4000
            and 'human' in (getattr(game, 'fast_metrics', None) or {})
            and 'human' in (getattr(game, 'metrics', None) or {}))


def _five_game_proof(games, *, fast):
    rows = [((g.fast_metrics if fast else g.metrics) or {}).get('human') or {}
            for g in games]
    if not all(isinstance(m.get('opportunities'), int)
               and not isinstance(m['opportunities'], bool)
               and isinstance(m.get('hits'), int)
               and not isinstance(m['hits'], bool)
               and 0 <= m['hits'] <= m['opportunities'] for m in rows):
        return False
    opportunities = sum(m['opportunities'] for m in rows)
    hits = sum(m['hits'] for m in rows)
    hit_games = sum(m['hits'] >= 1 for m in rows)
    multi_hit_games = sum(m['hits'] >= 2 for m in rows)
    return bool(opportunities >= 8 and hits >= 6 and hit_games >= 3
                and multi_hit_games >= 2)


def _fast_screen(games, config):
    if not all(_verified_entry(g) for g in games):
        return False
    if not all(_five_game_proof(games[i:i+5], fast=True) for i in (0, 5)):
        return False
    fast = period_summary(games, config, fast=True)
    return bool(fast['opportunities'] >= 18
                and fast['hits'] >= 14
                and fast['hit_games'] >= 6
                and fast['contributors'] >= 4
                and fast['hit_lower'] >= 0.55
                and fast['anomaly_strength'] >= 0.27
                and fast['quality_excess'] >= 0.12
                and fast['information'] >= 0.20)


def _deep_proof(games, config):
    # Verify *each* half, rather than letting a single perfect game dominate.
    halves_ok = all(_five_game_proof(games[i:i+5], fast=False)
                    for i in (0, 5))
    deep = period_summary(games, config)
    fast = period_summary(games, config, fast=True)
    confirmation = paired_quality_confirmation(deep, 0.85, coverage_floor=0.95)
    quality_hit_games = sum(
        (g.metrics.get('human') or {}).get('quality_stable_hits', 0) >= 1
        for g in games)
    quiet_hit_games = sum(
        (g.metrics.get('human') or {}).get('quiet_hits', 0) >= 1
        for g in games)
    # Additional independent position-context check: neither a high score nor
    # a won game is evidence on its own. The upstream human opportunity filter
    # excludes post-opponent-error and easy-conversion moves; incomplete paired
    # contracts block this HIGH path.
    checks = {
        'both five-game halves independently contribute': halves_ok,
        'at least twenty deep difficult opportunities': deep['opportunities'] >= 20,
        'at least sixteen deep anomalous choices': deep['hits'] >= 16,
        'at least six games with deep anomaly hits': deep['hit_games'] >= 6,
        'at least four multi-hit contributor games': deep['contributors'] >= 4,
        'deep anomaly hit lower bound': deep['hit_lower'] >= 0.65,
        'at least six quiet high-information choices': deep['quiet_hits'] >= 6,
        'quiet hit games spread across at least four games': quiet_hit_games >= 4,
        'quality-consistent hits across at least six games': quality_hit_games >= 6,
        'paired fast/deep search coverage': confirmation['coverage_passed'],
        'consistent fast/deep objective quality': confirmation['fraction_passed'],
        'at least fourteen quality-stable anomaly hits': confirmation['hits'] >= 14,
        'paired anomaly retention': (fast['hits'] > 0
                                     and deep['hits'] >= 0.85 * fast['hits']),
        'deep human-information effect size': deep['information'] >= 0.25,
        'deep anomaly strength': deep['anomaly_strength'] >= 0.33,
        'deep raw quality separation': deep['quality_excess'] >= 0.14,
    }
    return checks, {
        'fast_opportunities': fast['opportunities'], 'fast_hits': fast['hits'],
        'deep_opportunities': deep['opportunities'], 'deep_hits': deep['hits'],
        'deep_hit_games': deep['hit_games'], 'deep_multi_hit_contributors': deep['contributors'],
        'deep_quiet_hits': deep['quiet_hits'],
        'paired_quality_fraction': round(confirmation['fraction'], 5),
        'paired_coverage_fraction': round(confirmation['coverage'], 5),
        'quality_stable_hits': confirmation['hits'],
        'deep_hit_lower_bound': round(deep['hit_lower'], 5),
    }


def analyze(games, *, config=CONFIG):
    """Predeclared recent same-control sliding windows, no outcomes/rank picks."""
    groups = defaultdict(list)
    # V29 first guarantees every one of the latest 50 rated games was selected
    # for deep analysis. Scope the chronology to those fifty across ALL time
    # controls, not the latest twenty in each time-control class. This catches
    # a ten-game incident at the BEGINNING of the fifty-game period.
    eligible=[g for g in games if getattr(g,'rated',None) is True
              and not getattr(g,'probe_only',False)
              and isinstance(getattr(g,'ended',None),(int,float))
              and math.isfinite(g.ended)]
    latest=sorted({g.identity:g for g in eligible}.values(),
                  key=lambda g:(g.ended,g.identity))[-LATEST_RATED_SCOPE:]
    for g in latest:
        if _verified_entry(g):
            groups[(g.time_class, g.time_control)].append(g)
    examined = 0
    fast_discovered = 0
    fully_confirmed = []
    for (kind, control), group in sorted(groups.items()):
        # One sampled observation per game, duplicate IDs cannot manufacture
        # contributor games or duplicated-window evidence.
        unique = {g.identity: g for g in group}
        ordered = sorted(unique.values(), key=lambda g:(g.ended,g.identity))
        for start in range(0,len(ordered)-WINDOW_GAMES+1):
            window = ordered[start:start+WINDOW_GAMES]
            if len(window)!=WINDOW_GAMES:
                continue
            # Prevent 10 apparent consecutive *selected* games from bridging
            # an unreviewed rated same-control game. The original archive
            # assigns indices before the 100+50 deep-scope selection.
            if any(b.control_index != a.control_index+1
                   for a,b in zip(window,window[1:])):
                continue
            examined += 1
            if not _fast_screen(window, config):
                continue
            fast_discovered += 1
            checks, details = _deep_proof(window, config)
            if all(checks.values()):
                fully_confirmed.append({
                    'class':kind, 'time_control':control,
                    'start':int(window[0].ended), 'end':int(window[-1].ended),
                    'games':WINDOW_GAMES, 'checks':checks, **details,
                })
    # Overlapping windows are candidates for one incident, not independent
    # replication. At most one HIGH elevation is issued per account/review.
    best = max(fully_confirmed,key=lambda r:(
        r['deep_hit_lower_bound'],r['quality_stable_hits'],r['deep_hit_games']
    ),default=None)
    return {
        'schema':'fairplay-v28-ten-game-burst-v1',
        'window_games':WINDOW_GAMES,
        'rated_latest_scope':LATEST_RATED_SCOPE,
        'recent_rated_games_selected':len(latest),
        'windows_examined':examined,
        'fast_discovery_windows':fast_discovered,
        'deep_confirmed_windows':len(fully_confirmed),
        'best':best,
        'qualified':bool(best),
        'scoring_ceiling':'HIGH (manual-review priority)',
        'uncertainty':(
            'Rating-conditioned quality is heuristic, not an independently '
            'calibrated cheating probability. Multiple recent windows are '
            'searched; positive and negative player-disjoint holdouts are '
            'required to establish a false-positive rate. Results are not '
            'evidence of misconduct on their own.'),
    }


def integrate(result, games, config=CONFIG):
    report = analyze(games, config=config)
    result.diagnostics['ten_game_burst'] = report
    complete = (not result.partial and
                result.coverage.get('primary_engine_complete', False) and
                result.coverage.get('required_deep_complete', False))
    enabled = not {'human','difficulty','burst_high'} & set(config.disabled_features)
    if (report['qualified'] and complete and enabled
            and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA')):
        result.priority = 'HIGH'
        result.deep_confirmed = True
        result.diagnostics['high_path'] = 'Ten-game distributed deep-confirmed gameplay burst'
        result.diagnostics['high_blocked'] = []
        result.reasons = [
            'A recent ten-game stretch of the same time control contained '
            'unusually many competitive, difficult decisions with '
            'independently replicated evidence across both five-game halves.',
            'Every party was deep-reviewed; many quiet high-information moves '
            'retained objective quality under paired Stockfish searches. '
            'HIGH is a manual-review priority, not proof of engine use. '
            'The heuristic requires independent false-positive calibration.',
        ]
    report['coverage_gate_passed'] = bool(complete)
    report['feature_gate_passed'] = bool(enabled)
    return result
