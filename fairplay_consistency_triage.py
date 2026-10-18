"""Owner-configured worklist selection, separate from gameplay assessment.

These are explicit review preferences, NOT calibrated human-performance norms.
Published Accuracy is not proof of assistance. In particular, honest strong
Bullet players can meet this rule. Never change engine/model evidence or infer
misconduct from account status, identity or a desired validation label.
"""
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import ceil
from math import isfinite
import os
from statistics import median

from fairplay_accuracy_scope import official_score
from fairplay_config import CONFIG


@dataclass(frozen=True)
class ConsistencyRule:
    # Manual workload preferences requested by the server owner. They are not
    # estimated cheating probabilities or an empirical Accuracy-by-Elo model.
    block_games: int = 30
    median_accuracy: float = 85.0
    lower_quartile_accuracy: float = 80.0
    max_accuracy_mad: float = 7.0
    published_fraction: float = .80
    high_score: float = 80.0
    high_score_fraction: float = .70
    max_player_rating: int = 2200
    rating_coverage: float = .80
    max_median_opponent_deficit: int = 150
    competitive_decisions: int = 100
    competitive_games: int = 15
    competitive_decisions_per_game: int = 3


RULE = ConsistencyRule()


def enabled():
    """The owner can disable this overlay without changing gameplay scoring."""
    return os.getenv('FAIRPLAY_BULLET_CONSISTENCY_TRIAGE', '1') == '1'


def _quartile(values):
    if not values:
        return None
    values = sorted(values)
    position = (len(values)-1)*.25
    lower = int(position)
    upper = min(len(values)-1, lower+1)
    return values[lower]+(position-lower)*(values[upper]-values[lower])


def _rating(value):
    return (float(value) if type(value) in (int, float)
            and isfinite(value) and 0 < value <= 4000 else None)


def _competitive(game):
    return sum(bool(d.metrics.get('useful') and d.metrics.get('competitive')
                    and not d.metrics.get('search_inconsistent')
                    and not d.metrics.get('easy_conversion')
                    and not d.metrics.get('automatic_material_gain')
                    and not d.metrics.get('simple_threat_response')
                    and d.phase != 'opening' and not d.forced and not d.trivial_kind)
               for d in game.decisions)


def _block(games, start, config, rule, complete, incomplete_archive_months):
    # Use the same exact depth verifier as the active pipeline. A completed
    # fast search or a partial position cannot satisfy the review rule.
    from fairplay_v51_shadow import _verify_deep
    scores = [official_score(g) for g in games if official_score(g) is not None]
    center = median(scores) if scores else None
    mad = median(abs(x-center) for x in scores) if scores else None
    quartile = _quartile(scores)
    own = [_rating(g.rating) for g in games if _rating(g.rating) is not None]
    opp = [_rating(g.opponent_rating) for g in games if _rating(g.opponent_rating) is not None]
    own_median = median(own) if own else None
    opp_median = median(opp) if opp else None
    opportunities = [_competitive(g) for g in games]
    first = datetime.fromtimestamp(games[0].ended, timezone.utc).strftime('%Y-%m')
    last = datetime.fromtimestamp(games[-1].ended, timezone.utc).strftime('%Y-%m')
    gates = {
        'complete required Stockfish coverage': bool(complete) and all(_verify_deep(g, config) for g in games),
        'complete thirty-game block': len(games) == rule.block_games,
        'unique games': len({g.identity for g in games}) == len(games),
        'no unavailable archive inside the block': not any(first <= month <= last for month in incomplete_archive_months),
        'published Accuracy coverage': len(scores) >= ceil(rule.published_fraction*len(games)),
        'high published median': center is not None and center >= rule.median_accuracy,
        'high published lower quartile': quartile is not None and quartile >= rule.lower_quartile_accuracy,
        'narrow robust score spread': mad is not None and mad <= rule.max_accuracy_mad,
        # Missing scores stay in this denominator; only published successes
        # cannot turn a sparse, selectively reviewed sample into consistency.
        'high scores across the whole block': sum(x >= rule.high_score for x in scores) >= ceil(rule.high_score_fraction*len(games)),
        'known player and opponent ratings': min(len(own), len(opp)) >= ceil(rule.rating_coverage*len(games)),
        'configured sub-elite rating scope': own_median is not None and own_median <= rule.max_player_rating,
        'comparable opponent median': own_median is not None and opp_median is not None
            and opp_median >= own_median-rule.max_median_opponent_deficit,
        'competitive decision coverage': sum(opportunities) >= rule.competitive_decisions,
        'competitive decisions across many games': sum(n >= rule.competitive_decisions_per_game for n in opportunities) >= rule.competitive_games,
    }
    return {'first_game': start+1, 'last_game': start+len(games),
            'first_month': first, 'last_month': last,
            'games': len(games), 'published_games': len(scores),
            'median_accuracy': center, 'lower_quartile_accuracy': quartile,
            'accuracy_mad': mad, 'player_rating_median': own_median,
            'opponent_rating_median': opp_median,
            'competitive_decisions': sum(opportunities),
            'competitive_games': sum(n >= rule.competitive_decisions_per_game for n in opportunities),
            'passed': all(gates.values()), 'gates': gates,
            'blockers': [name for name, passed in gates.items() if not passed]}


def consistency_triage(games, config=CONFIG, *, complete,
                       incomplete_archive_months=(), rule=RULE, is_enabled=None):
    """Two disjoint fixed blocks in ONE Bullet control, without ranked sets.

    This can allocate HIGH manual-review priority, never VERY HIGH. It does
    not assert that Stockfish or Maia established a gameplay anomaly.
    """
    active = enabled() if is_enabled is None else bool(is_enabled)
    result = {'enabled': active, 'passed': False, 'max_priority': 'HIGH',
              'method': 'Owner-configured published-Accuracy consistency triage; not a calibrated misconduct model',
              'config': asdict(rule), 'controls': {}, 'selected': None,
              'limitation': 'Honest strong players can qualify. Accuracy consistency is a manual-review preference, not proof or engine/Maia confirmation.'}
    if not active:
        return result
    groups = defaultdict(list)
    for game in sorted(games, key=lambda g: (g.ended, g.identity)):
        if game.rated is True and game.time_class == 'bullet':
            groups[game.time_control].append(game)
    selected = []
    for control, group in sorted(groups.items()):
        offset = len(group) % rule.block_games
        blocks = [_block(group[start:start+rule.block_games], start, config, rule,
                         complete, incomplete_archive_months)
                  for start in range(offset, len(group), rule.block_games)]
        passing = [index for index, block in enumerate(blocks) if block['passed']]
        result['controls'][control] = {'games': len(group), 'blocks': blocks,
                                      'qualifying_disjoint_blocks': len(passing)}
        if len(passing) >= 2:
            left, right = passing[-2:]
            earlier, later = blocks[left], blocks[right]
            members = group[offset+left*rule.block_games:offset+(left+1)*rule.block_games]+group[offset+right*rule.block_games:offset+(right+1)*rule.block_games]
            if len({g.identity for g in members}) != 2*rule.block_games:
                continue
            if any(earlier['first_month'] <= month <= later['last_month'] for month in incomplete_archive_months):
                continue
            selected.append({'time_control': control, 'games': 2*rule.block_games,
                             'first_game': earlier['first_game'], 'last_game': later['last_game'],
                             'span_games': later['last_game']-earlier['first_game']+1,
                             'contiguous': right == left+1,
                             'blocks': [earlier, later]})
    if selected:
        result['passed'] = True
        result['selected'] = selected[-1]
    return result


def apply_triage(assessment, triage):
    """Preserve the actual analytical conclusion and label the overlay plainly."""
    result = dict(assessment)
    result['gameplay_priority'] = assessment['priority']
    result['gameplay_reasons'] = list(assessment['reasons'])
    result['gameplay_profile'] = assessment.get('classifier_profile')
    result['classifier_profile'] = 'v55-configured-review-triage'
    result['consistency_triage'] = triage
    result['priority_basis'] = 'gameplay_assessment'
    result['triggered_routes'] = list(assessment['triggered_routes'])
    if triage['passed'] and assessment['priority'] in ('LOW', 'MODERATE'):
        result['priority'] = 'HIGH'
        result['priority_basis'] = 'configured_manual_review_rule'
        result['triggered_routes'].append('bullet_accuracy_consistency_triage')
        chosen = triage['selected']
        result['reasons'] = [
            f"Configured manual-review rule: two disjoint {triage['config']['block_games']}-game "
            f"rated Bullet blocks ({chosen['time_control']}) met the published-Accuracy consistency "
            f"and coverage preferences. Underlying Stockfish/gameplay assessment: {assessment['priority']}. "
            'This is not engine/Maia-confirmed assistance; honest strong players can qualify.']
    return result
