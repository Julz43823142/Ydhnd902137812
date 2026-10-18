"""Predefined recent tails versus earlier comparable full-engine games.

This is a gameplay family, not independent evidence from CPL/rank/human
quality. Published Accuracy is deliberately not an input. Bounds describe the
sample, not a calibrated probability. A hot streak, improvement, preparation
or an underrated account can have innocent explanations; manual review stays
mandatory. These thresholds require external holdout validation.
"""
from collections import Counter
from math import ceil
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from statistics import median

from fairplay_config import CONFIG
from fairplay_human import period_summary, absolute_blockers


@dataclass(frozen=True)
class RecentShiftConfig:
    windows: tuple = (10, 20, 50)
    baseline_games: int = 20
    baseline_limit: int = 100
    baseline_opportunities: int = 40
    recent_opportunity_floor: int = 20
    quality_shift: float = .25
    excess_shift: float = .20
    lower_bound_gap: float = .15
    robust_effect: float = 1.5
    spread_floor: float = .05
    control_mix_distance: float = .20


RECENT_CONFIG = RecentShiftConfig()


def _upper(hits, total, z):
    from fairplay_calibration import lower_bound
    return 1-lower_bound((total-hits)/total if total else None, total, z) if total else 1.0


def _quality_values(games, field):
    return [g.metrics['human'][field] for g in games
            if g.metrics.get('human', {}).get('opportunities', 0)
            and g.metrics['human'].get(field) is not None]


def _mix_distance(left, right):
    a, b = Counter(g.time_control for g in left), Counter(g.time_control for g in right)
    return .5*sum(abs(a[k]/len(left)-b[k]/len(right)) for k in a.keys()|b.keys()) if left and right else 1.0


def recent_shift(games, config=CONFIG, *, complete, thresholds=RECENT_CONFIG,
                 incomplete_archive_months=(), include_bullet=False):
    """Do not average a recent tail into the whole 500-game history.

    Only three predefined endpoints per live non-Bullet class are tested.
    Baseline selection uses time/rating/control, never quality, Accuracy,
    result, account identity or closure status. Low-opportunity recent games
    stay in chronology. Different widths are alternative views of one tail,
    never independent replications and never a VERY HIGH route.
    """
    candidates = []
    kinds = ('rapid', 'blitz', 'bullet') if include_bullet else ('rapid', 'blitz')
    for kind in kinds:
        group = sorted((g for g in games if g.rated is True and g.time_class == kind),
                       key=lambda g: (g.ended, g.identity))
        for width in thresholds.windows:
            # Bullet requires a longer recent period and stronger per-period
            # evidence. A separate matched baseline replaces the old demand
            # for two 30-game absolute blocks; clocks are never mandatory.
            if kind == 'bullet' and width < 20:
                continue
            if len(group) < width+thresholds.baseline_games:
                continue
            recent = group[-width:]
            controls = {g.time_control for g in recent}
            ratings = [g.rating for g in recent if g.rating is not None]
            opponents = [g.opponent_rating for g in recent if getattr(g, 'opponent_rating', None) is not None]
            earlier = group[:-width]
            # Missing comparison context is missing evidence. It is not zero
            # Elo and is not evidence of a behavioral change.
            baseline = []
            if ratings and opponents:
                own, opponent = median(ratings), median(opponents)
                baseline = [g for g in earlier
                    if g.time_control in controls and g.rating is not None and getattr(g, 'opponent_rating', None) is not None
                    and abs(g.rating-own) <= config.baseline_player_rating_tolerance
                    and abs(g.opponent_rating-opponent) <= config.baseline_opponent_rating_tolerance
                    and abs((g.rating-g.opponent_rating)-(own-opponent)) <= config.baseline_rating_gap_tolerance
                    ][-thresholds.baseline_limit:]
            summary = period_summary(recent, config)
            reference = period_summary(baseline, config)
            now_quality = _quality_values(recent, 'observed_quality')
            old_quality = _quality_values(baseline, 'observed_quality')
            now_excess = _quality_values(recent, 'quality_excess')
            old_excess = _quality_values(baseline, 'quality_excess')
            quality_delta = median(now_quality)-median(old_quality) if now_quality and old_quality else None
            excess_delta = median(now_excess)-median(old_excess) if now_excess and old_excess else None
            old_center = median(old_quality) if old_quality else 0
            mad = median(abs(v-old_center) for v in old_quality) if old_quality else 0
            effect = quality_delta/max(thresholds.spread_floor, 1.4826*mad) if quality_delta is not None else None
            upper = _upper(reference['hits'], reference['opportunities'], config.rate_lower_bound_z)
            gap = summary['hit_lower']-upper
            midpoint = width//2
            halves = [period_summary(part, config) for part in (recent[:midpoint], recent[midpoint:])]
            half_breadth = all(part['contributors'] >= max(2, (part['games']+2)//3) for part in halves)
            # Relative-change evidence has a separately covered earlier
            # denominator. Keep its recent minimum proportional; Wilson and
            # every quality/contributor gate still apply to the real count.
            recent_config=replace(config,human_min_opportunities=thresholds.recent_opportunity_floor)
            if kind == 'bullet':
                recent_config=replace(recent_config,
                    human_min_opportunities=config.bullet_human_deep_opportunities,
                    human_min_contributors=max(config.bullet_human_deep_games, ceil(.6*width)),
                    human_hit_lower=config.bullet_human_deep_hit_lower,
                    human_absolute_information_floor=config.bullet_human_information)
            absolute_failures = absolute_blockers(summary, recent_config,relative=True)
            required = baseline+recent
            earliest = datetime.fromtimestamp(min(g.ended for g in required), timezone.utc).strftime('%Y-%m')
            latest = datetime.fromtimestamp(max(g.ended for g in required), timezone.utc).strftime('%Y-%m')
            chronology_complete = not any(earliest <= month <= latest for month in incomplete_archive_months)
            gates = {
                'complete required engine coverage': bool(complete),
                'no missing archive within required comparison': chronology_complete,
                'absolute recent gameplay evidence': not absolute_failures,
                'twenty comparable earlier games': len(baseline) >= thresholds.baseline_games,
                'earlier difficult opportunity coverage': reference['opportunities'] >= thresholds.baseline_opportunities,
                'known recent opponent context': len(opponents) >= .8*width,
                'comparable control mixture': _mix_distance(recent, baseline) <= thresholds.control_mix_distance,
                'replication across both recent halves': half_breadth,
                'large median difficult-quality rise': quality_delta is not None and quality_delta >= thresholds.quality_shift,
                'large median rating-adjusted excess rise': excess_delta is not None and excess_delta >= thresholds.excess_shift,
                'robust earlier-quality separation': effect is not None and effect >= thresholds.robust_effect,
                'sample-protected hit-fraction separation': gap >= thresholds.lower_bound_gap,
                'unique chronological games': len({g.identity for g in baseline+recent}) == len(baseline)+len(recent),
            }
            candidates.append({'time_class': kind, 'recent_games': width,
                'baseline_games': len(baseline), 'recent_opportunities': summary['opportunities'],
                'recent_contributors': summary['contributors'],
                'baseline_opportunities': reference['opportunities'],
                'quality_shift': quality_delta, 'quality_excess_shift': excess_delta,
                'robust_effect': effect, 'hit_lower': summary['hit_lower'], 'baseline_hit_upper': upper,
                'hit_bound_gap': gap, 'gates': gates,
                'absolute_blockers': absolute_failures,
                'blockers': [name for name, passed in gates.items() if not passed],
                'passed': all(gates.values())})
    passing = [candidate for candidate in candidates if candidate['passed']]
    # Alternative windows never accumulate a score or create VERY HIGH.
    best = max(passing or candidates,
               key=lambda row: (row['passed'], -len(row['blockers']), row['quality_shift'] or 0),
               default=None)
    return {'passed': bool(passing), 'max_priority': 'HIGH', 'selected': best,
            'candidates': candidates,
            'method': 'Predefined recent tails; rating/control-matched earlier gameplay; heuristic, not probability'}
