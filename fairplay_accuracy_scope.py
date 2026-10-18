"""Bounded model allocation and descriptive published-Accuracy comparisons.

Published scores exist only for some reviewed games. They allocate compute,
not misconduct labels. Keep deterministic controls and all engine-reviewed
games in chronology; a high-score selection is not a representative sample.
"""
from collections import defaultdict
from math import isfinite
from statistics import median

ACCURACY_THRESHOLD = 80.0
CONTROL_GAMES_PER_CLASS = 10
TAIL_WIDTHS = (10, 20, 50)


def official_score(game):
    value = game.accuracy
    return (float(value) if type(value) in (int, float)
            and isfinite(value) and 0 <= value <= 100 else None)


def _spread(rows, count):
    if count <= 0:
        return []
    if len(rows) <= count:
        return rows
    if count == 1:
        return [rows[len(rows)//2]]
    return [rows[round(i*(len(rows)-1)/(count-1))] for i in range(count)]


def maia_scope(games, *, complete_reference=False):
    """Full move inference in >80 games plus fixed comparison games.

    Comparators are sampled by chronology, not engine matches, wins, names,
    model likelihood or labels. Include missing-score games explicitly rather
    than silently treating missing published Accuracy as a low score.
    """
    ordered = sorted((g for g in games if g.rated is True),
                     key=lambda g: (g.ended, g.identity))
    candidates = [g for g in ordered if official_score(g) is not None
                  and official_score(g) > ACCURACY_THRESHOLD]
    candidate_ids = {g.identity for g in candidates}
    groups = defaultdict(list)
    for game in ordered:
        if game.identity not in candidate_ids:
            groups[game.time_class].append(game)
    controls = []
    for group in groups.values():
        # Reserve half the comparison budget for unknown scores if present.
        missing = [g for g in group if official_score(g) is None]
        known = [g for g in group if official_score(g) is not None]
        quota = min(len(missing), CONTROL_GAMES_PER_CLASS//2)
        chosen = _spread(missing, quota) if quota else []
        chosen += _spread(known, CONTROL_GAMES_PER_CLASS-len(chosen))
        remaining = CONTROL_GAMES_PER_CLASS-len(chosen)
        if remaining:
            chosen_ids = {g.identity for g in chosen}
            chosen += _spread([g for g in missing if g.identity not in chosen_ids], remaining)
        controls += chosen
    selected_ids = candidate_ids | {g.identity for g in controls}
    if complete_reference:
        # A scoring reference cannot depend on which games somebody happened
        # to review on Chess.com. Retain misses and missing published scores.
        selected_ids = {g.identity for g in ordered}
    selected = [g for g in ordered if g.identity in selected_ids]
    return selected, {
        'threshold': ACCURACY_THRESHOLD, 'eligible_rated_games': len(ordered),
        'published_accuracy_games': sum(official_score(g) is not None for g in ordered),
        'missing_accuracy_games': sum(official_score(g) is None for g in ordered),
        'accuracy_selected_games': len(candidates), 'comparison_games': len(controls),
        'selected_games': len(selected),
        'selected_positions': sum(len(g.decisions) for g in selected),
        'all_rated_games': bool(complete_reference),
        'selection': ('Every collected rated game, independent of published Accuracy'
                      if complete_reference else
                      'Published Accuracy >80 plus chronological comparison games; not a representative population sample'),
    }


def accuracy_tails(games):
    """Report fixed latest tails without deleting gaps or pooling controls.

    These are screening observations only. No external accuracy-by-Elo norm
    has been calibrated; an 85 score at 2000 Elo cannot imply HIGH by itself.
    """
    groups = defaultdict(list)
    for game in sorted(games, key=lambda g: (g.ended, g.identity)):
        if game.rated is True:
            groups[(game.time_class, game.time_control)].append(game)
    rows = []
    for (kind, control), group in sorted(groups.items()):
        for width in TAIL_WIDTHS:
            if len(group) < width:
                continue
            recent, earlier = group[-width:], group[:-width][-100:]
            own = [g.rating for g in recent if g.rating is not None]
            opp = [g.opponent_rating for g in recent if g.opponent_rating is not None]
            matched = [g for g in earlier if own and opp and g.rating is not None
                       and g.opponent_rating is not None
                       and abs(g.rating-median(own)) <= 200
                       and abs(g.opponent_rating-median(opp)) <= 250
                       and abs((g.rating-g.opponent_rating)-(median(own)-median(opp))) <= 200]
            now = [official_score(g) for g in recent if official_score(g) is not None]
            old = [official_score(g) for g in matched if official_score(g) is not None]
            covered = len(now) >= .8*width and len(old) >= 20
            rise = median(now)-median(old) if covered else None
            rows.append({'time_class': kind, 'time_control': control,
                         'recent_games': width, 'recent_published': len(now),
                         'recent_median': median(now) if now else None,
                         'baseline_games': len(matched), 'baseline_published': len(old),
                         'baseline_median': median(old) if old else None,
                         'median_shift': rise, 'comparison_complete': covered,
                         'review_marker': bool(rise is not None and rise >= 10),
                         'priority_authority': False})
    return rows


def model_summary(games, scope, reference):
    """Keep successes AND misses, with scope/coverage separate from evidence."""
    from fairplay_maia import valid_policy, quality
    result = dict(scope, available=reference.get('available') is True,
                  model=reference.get('model'), positions=0,
                  inference_positions=reference.get('positions', 0),
                  reason=reference.get('reason'), by_class={},
                  role=('Full-sample human reference; bounded engine/model period comparison can set review priority'
                        if scope.get('all_rated_games') else
                        'Selected-game human move reference; not a cheating probability or a standalone priority route'))
    for kind in ('bullet', 'blitz', 'rapid'):
        group = [g for g in games if g.time_class == kind] if result['available'] else []
        compared = rare = rare_strong = contributors = 0
        for game in group:
            hits = 0
            for d in game.decisions:
                m = d.metrics
                if not valid_policy(d, d.human_policy):
                    continue
                result['positions'] += 1
                if (not m.get('useful')
                        or not m.get('competitive') or d.forced or d.trivial_kind
                        or d.phase == 'opening' or m.get('easy_conversion')
                        or m.get('automatic_material_gain') or m.get('simple_threat_response')
                        or m.get('search_inconsistent')):
                    continue
                compared += 1
                probability = d.human_policy.get(d.move, 0)/sum(d.human_policy.values())
                rare += probability <= .05
                strong = probability <= .05 and quality(m.get('cpl', 1000), m.get('scaled_loss', 1)) >= .85
                rare_strong += strong
                hits += strong
            contributors += hits >= 2
        result['by_class'][kind] = {'compared_decisions': compared,
            'low_policy_moves': rare, 'low_policy_strong_moves': rare_strong,
            'multi_hit_games': contributors}
    result['complete_selected_positions'] = (result['available']
        and result['positions'] == scope['selected_positions']
        and result['inference_positions'] == scope['selected_positions'])
    return result
