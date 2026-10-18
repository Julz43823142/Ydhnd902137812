"""Bounded full-depth engine/Maia periods, not misconduct probabilities.

A move policy predicts human choices, not honesty. We compare actual objective
quality with an *upper bound* on the quality of Maia's alternatives: unsearched
moves get perfect quality and an extra model-error allowance. Successes and
misses are sampled uniformly before their values are inspected. Engine and
model evidence are one related gameplay family, never independent votes.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from statistics import mean

from fairplay_calibration import lower_bound
from fairplay_config import CONFIG
from fairplay_human import period_raw_excess_floor
from fairplay_maia import inspect_policy


@dataclass(frozen=True)
class ModelPeriodConfig:
    windows: tuple = (6, 10, 20, 50)
    game_cap: int = 6
    minimum_games: int = 6
    minimum_decisions: int = 12
    minimum_contributors: int = 4
    contributor_fraction: float = .60
    high_hit_lower: float = .65
    move_excess: float = .20
    game_signed_excess: float = .15
    information: float = .12
    # Bullet has greater human-model/domain uncertainty and keeps a separate
    # longer, stronger route. It cannot borrow Rapid/Blitz moves or clocks.
    bullet_games: int = 20
    bullet_decisions: int = 40
    bullet_hit_lower: float = .80
    bullet_excess: float = .20


MODEL_PERIOD = ModelPeriodConfig()


def _cap(rows, count):
    if len(rows) <= count:
        return rows
    return [rows[round(i*(len(rows)-1)/(count-1))] for i in range(count)]


def _game(game, config, thresholds):
    from fairplay_v51_shadow import _verified_decision
    model = []
    engine = []
    coverage = bool(game.decisions and game.rating is not None
                    and game.opponent_rating is not None)
    for decision in game.decisions:
        m = decision.metrics
        verified = _verified_decision(decision, game.time_class, config)
        try:
            aligned, row = inspect_policy(decision, decision.human_policy)
        except (ValueError, TypeError, KeyError, OverflowError):
            aligned, row = False, None
        coverage = coverage and aligned and verified
        # Missing comparison data at a meaningful decision cannot silently
        # remove a miss from the denominator. Structural exclusions remain
        # exclusions; an invalid model/engine pair is incomplete evidence.
        if (row is None and m.get('useful') and m.get('competitive')
                and not decision.forced and not decision.trivial_kind
                and decision.phase != 'opening'):
            coverage = False
        if not verified:
            continue
        if m.get('human_opportunity'):
            engine.append(m)
        if (m.get('human_opportunity') and row and row['eligible']
                and row['reference_weight'] > 0):
            # Use the SAME difficult decisions, including misses. Strong model
            # moves elsewhere in a game cannot corroborate unrelated engine
            # hits. No mixing two convenient subsets into one apparent signal.
            model.append(dict(row, engine_hit=bool(m.get('high_information'))))
    engine = _cap(engine, thresholds.game_cap)
    model = _cap(model, thresholds.game_cap)
    model_hits = sum(row['engine_hit'] and row['observed_quality'] >= .85
                     and row['quality_excess_lower_bound'] >= thresholds.move_excess
                     for row in model)
    signed = mean(row['signed_quality_excess'] for row in model) if model else 0
    information = mean(row['information'] for row in model) if model else 0
    engine_hits = sum(bool(m.get('high_information')) for m in engine)
    return {
        'complete': bool(coverage),
        'engine_n': len(engine), 'engine_hits': engine_hits,
        'engine_contributor': engine_hits >= 2,
        'engine_excess': mean(m['quality_excess'] for m in engine) if engine else 0,
        'engine_reference': mean(m['expected_human_quality'] for m in engine) if engine else 1,
        'n': len(model), 'hits': model_hits,
        'signed_excess': signed, 'information': information,
        'known_mass': mean(row['known_policy_mass'] for row in model) if model else 0,
        'contributor': model_hits >= 2 and signed >= thresholds.game_signed_excess
                       and information >= thresholds.information,
    }


def _summary(rows, config):
    active = [row for row in rows if row['n']]
    engine = [row for row in rows if row['engine_n']]
    total = sum(row['n'] for row in rows)
    hits = sum(row['hits'] for row in rows)
    engine_n = sum(row['engine_n'] for row in rows)
    engine_hits = sum(row['engine_hits'] for row in rows)
    def average(values, key, default=0):
        return mean(row[key] for row in values) if values else default
    return {
        'games': len(rows), 'complete': all(row['complete'] for row in rows),
        'opportunities': total, 'hits': hits,
        'hit_fraction': hits/total if total else None,
        'hit_lower': lower_bound(hits/total if total else None, total, config.rate_lower_bound_z),
        'contributors': sum(row['contributor'] for row in rows),
        'signed_excess': average(active, 'signed_excess'),
        'information': average(active, 'information'),
        'known_mass': average(active, 'known_mass'),
        'engine_opportunities': engine_n, 'engine_hits': engine_hits,
        'engine_hit_lower': lower_bound(engine_hits/engine_n if engine_n else None,
                                       engine_n, config.rate_lower_bound_z),
        'engine_contributors': sum(row['engine_contributor'] for row in rows),
        'engine_excess': average(engine, 'engine_excess'),
        'quality_reference': average(engine, 'engine_reference', 1),
    }


def model_periods(games, config=CONFIG, *, complete, reference,
                  incomplete_archive_months=(), thresholds=MODEL_PERIOD):
    """Predefined blocks plus the latest tail; never ranked best-game sets.

    Wilson limits describe a sampled hit fraction, NOT its rarity in a human
    population. These experimental gates need external holdout calibration.
    They cannot create VERY HIGH or a result without complete required data.
    """
    report = {'available': bool(reference and reference.get('available') is True),
              'passed': False, 'moderate': False, 'selected': None,
              'periods_examined': 0, 'candidates': [], 'max_priority': 'HIGH',
              'method': 'Related engine/learned-policy quality evidence; heuristic thresholds, not misconduct probabilities'}
    if {'human', 'neural'} & set(config.disabled_features):
        report['available'] = False
    if not report['available']:
        report['reason'] = 'Local human reference unavailable; existing engine routes remain active.'
        return report
    rated = sorted((g for g in games if g.rated is True
                    and g.time_class in ('rapid', 'blitz', 'bullet')),
                   key=lambda g: (g.ended, g.identity))
    if len({g.identity for g in rated}) != len(rated):
        report['reason'] = 'Duplicate game identities cannot establish replication.'
        return report
    summaries = {g.identity: _game(g, config, thresholds) for g in rated}
    for kind in ('rapid', 'blitz', 'bullet'):
        group = [g for g in rated if g.time_class == kind]
        for width in thresholds.windows:
            floor = thresholds.bullet_games if kind == 'bullet' else thresholds.minimum_games
            if width < floor or width > len(group):
                continue
            # Fixed partitions and one latest endpoint are alternative views.
            # No pair of overlapping candidates is ever added as replication.
            starts = sorted(set(range(0, len(group)-width+1, width)) | {len(group)-width})
            for start in starts:
                members = group[start:start+width]
                rows = [summaries[g.identity] for g in members]
                s = _summary(rows, config)
                required = max(thresholds.minimum_contributors,
                               ceil(thresholds.contributor_fraction*width))
                opportunities = max(thresholds.minimum_decisions, 2*required)
                lower = thresholds.high_hit_lower
                excess = thresholds.game_signed_excess
                if kind == 'bullet':
                    opportunities = max(opportunities, thresholds.bullet_decisions)
                    lower, excess = thresholds.bullet_hit_lower, thresholds.bullet_excess
                first = datetime.fromtimestamp(members[0].ended, timezone.utc).strftime('%Y-%m')
                last = datetime.fromtimestamp(members[-1].ended, timezone.utc).strftime('%Y-%m')
                archive_complete = not any(first <= month <= last for month in incomplete_archive_months)
                # Count the same games, including empty/missed decisions, in
                # both model/engine breadth and both chronological halves.
                halves = (rows[:width//2], rows[width//2:])
                gates = {
                    'complete required depth and policy coverage': bool(complete and s['complete']),
                    'no missing archive within period': archive_complete,
                    'proportional multi-game opportunity coverage': s['opportunities'] >= opportunities,
                    'distributed model contributors': s['contributors'] >= required,
                    'model contributors in both halves': all(sum(r['contributor'] for r in half) >= 2 for half in halves),
                    'model anomaly hit lower bound': s['hit_lower'] >= lower,
                    'signed model quality excess retains misses': s['signed_excess'] >= excess,
                    'bounded model information': s['information'] >= thresholds.information,
                    'difficult engine opportunity coverage': s['engine_opportunities'] >= opportunities,
                    'distributed difficult engine contributors': s['engine_contributors'] >= required,
                    'difficult engine hit lower bound': s['engine_hit_lower'] >= lower,
                    'rating-adjusted difficult quality excess': s['engine_excess'] >= period_raw_excess_floor(s, config),
                }
                moderate = (complete and s['complete'] and archive_complete
                            and s['opportunities'] >= thresholds.minimum_decisions
                            and s['contributors'] >= 3 and s['hit_lower'] >= .40
                            and s['signed_excess'] >= .10 and s['information'] >= thresholds.information
                            and s['engine_opportunities'] >= thresholds.minimum_decisions
                            and s['engine_contributors'] >= 3 and s['engine_hit_lower'] >= .40
                            and s['engine_excess'] >= period_raw_excess_floor(s, config))
                report['candidates'].append({'time_class': kind, 'first_game': start+1,
                    'last_game': start+width, 'summary': s, 'gates': gates,
                    'blockers': [name for name, passed in gates.items() if not passed],
                    'passed': all(gates.values()), 'moderate': bool(moderate)})
    report['periods_examined'] = len(report['candidates'])
    report['passed'] = any(row['passed'] for row in report['candidates'])
    report['moderate'] = any(row['moderate'] for row in report['candidates'])
    report['selected'] = max(report['candidates'], key=lambda row: (
        row['passed'], row['moderate'], -len(row['blockers']), row['summary']['signed_excess']), default=None)
    return report


def integrate_model_periods(result, games, config=CONFIG, *, complete,
                            reference=None, incomplete_archive_months=()):
    diagnostic = model_periods(games, config, complete=complete, reference=reference,
                              incomplete_archive_months=incomplete_archive_months)
    result['model_periods'] = diagnostic
    # The broad account minimum is still ten useful games. One huge game,
    # missing model data or an unconfirmed fraction cannot bypass it.
    if result['priority'] in ('INSUFFICIENT DATA', 'VERY HIGH'):
        return result
    if diagnostic['passed'] and result['priority'] in ('LOW', 'MODERATE'):
        result['priority'] = 'HIGH'
        result['triggered_routes'].append('model_engine_period')
        result['reasons'] = [
            'A chronological multi-game period repeatedly exceeded the local human '
            'reference on objectively strong decisions, with difficult rating-adjusted '
            'engine evidence in the same games. The sample is bounded and this '
            'experimental review flag requires manual review; it is not proof.']
    elif diagnostic['moderate'] and result['priority'] == 'LOW':
        result['priority'] = 'MODERATE'
        result['triggered_routes'].append('model_engine_period_moderate')
        result['reasons'] = [
            'A multi-game period shows elevated combined engine/human-reference '
            'quality evidence, with insufficient support for the HIGH route. '
            'Manual review is required; this is not proof of misconduct.']
    return result
