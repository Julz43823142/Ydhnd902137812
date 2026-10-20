"""Conservative rating and repeated-opponent safeguards for results-only triage.

These are workload-priority limits, not fair-play decisions. A sparse and
mismatched time-control rating is not a calibrated Elo expectation. Repeated
games against a single opponent also violate the independent-opposition
interpretation of a results-only streak.
"""
from collections import Counter
from statistics import median

YEAR_SECONDS = 365 * 24 * 3600
MIN_RATED = 20
MIN_OTHER_RATED = 10
MIN_ELO_GAP = 400


def rating_support(games, members):
    """Use rating anchors available by the end of this exact period.

    A player with years of rated history but only a few recent games does not
    automatically have a reliable CURRENT rating. Unrated games do not add
    rated anchors. The other controls serve solely as a reliability warning,
    never as a substituted expected score.
    """
    kind = members[0].time_class
    end = max(g.ended for g in members)
    recent = [g for g in games if 0 <= end - g.ended <= YEAR_SECONDS]
    same = [g for g in recent if g.time_class == kind and
            isinstance(g.rating, (int, float)) and not isinstance(g.rating, bool)
            and g.rating > 0]
    rated = [g.rating for g in same if g.rated is True]
    other = [g.rating for g in recent if g.time_class != kind and
             g.rated is True and isinstance(g.rating, (int, float))
             and not isinstance(g.rating, bool) and g.rating > 0]
    # An unrated-only control often retains a frozen, untested Elo. Its number
    # is useful to detect a cross-control discrepancy, not to authenticate it.
    own_median = median(rated) if rated else median(g.rating for g in same) if same else None
    other_median = median(other) if other else None
    gap = other_median - own_median if own_median is not None and other_median is not None else None
    unreliable = (len(rated) < MIN_RATED and len(other) >= MIN_OTHER_RATED
                  and gap is not None and gap >= MIN_ELO_GAP)
    return {'time_class':kind, 'rated_anchor_games_365d':len(rated),
            'unrated_games_365d':sum(g.rated is False for g in same),
            'other_control_rated_games_365d':len(other),
            'class_median_elo_365d':own_median,
            'other_control_median_elo_365d':other_median,
            'rating_gap':gap, 'unreliable':unreliable,
            'rule':'Fewer than 20 rated games in this control during the prior year, '
                   'at least 10 rated games in other controls, and a rating gap of '
                   'at least 400 Elo. An untested rating cannot independently support HIGH.'}


def opponent_support(members):
    """Flag heavy repeat-opponent concentration only when identities are known."""
    names = [getattr(g, 'opponent_username', None) for g in members]
    known = [n.strip().casefold() for n in names if isinstance(n, str) and n.strip()]
    counts = Counter(known)
    total = len(members)
    coverage = len(known) / total if total else 0
    largest = max(counts.values(), default=0)
    # Small windows can be meaningful, even if played in a match. Larger,
    # opponent-dominated windows warrant a manual tier rather than HIGH from
    # the Elo-independent-games model alone.
    concentrated = (total >= 25 and coverage >= .80 and
                    (len(counts) <= 2 or largest / total >= .75))
    return {'opponent_names_covered':len(known), 'games':total,
            'distinct_opponents':len(counts), 'largest_opponent_games':largest,
            'opponent_concentrated':concentrated,
            'rule':'At least 25 games, at least 80% identifiable opponents, '
                   'and either at most two opponents or one opponent in at '
                   'least 75% of games. Results-only HIGH is capped at MODERATE.'}


def independent_results_tier(tier, *, games, rating, opponents, accuracy_confirmed):
    """Cap review priority when the Elo-only evidence lacks a sound baseline."""
    if accuracy_confirmed or tier not in ('HIGH', 'MODERATE'):
        return tier, ()
    reasons = []
    if rating['unreliable']:
        reasons.append('unreliable_time_control_rating')
        if games <= 20:
            tier = 'LOW'
        elif tier == 'HIGH':
            tier = 'MODERATE'
    if opponents['opponent_concentrated']:
        reasons.append('repeated_opponent_concentration')
        if tier == 'HIGH':
            tier = 'MODERATE'
    return tier, tuple(reasons)
