"""Conservative combined manual-review triage for Method 3.

Uses already calculated statistics: no engine calls or additional searches.
A qualifying combination can yield MODERATE triage, never HIGH, and is not a calibrated misconduct estimate.
"""
from math import isfinite


def _selected_accuracy(selected, period_games):
    """Validate published Accuracy on the actual selected chronological period.

    Historical class-wide counts alone do not corroborate an unrelated hot
    streak. Missing official Accuracy is unknown, never a zero or a hit.
    """
    if not isinstance(period_games, list):
        return None
    kind = selected.get('time_class')
    first, last = selected.get('first_game'), selected.get('last_game')
    n = (selected.get('results') or {}).get('games')
    if (type(first) is not int or type(last) is not int or
            type(n) is not int or first < 1 or last < first):
        return None
    class_games = [game for game in period_games
                   if getattr(game, 'time_class', None) == kind]
    if last > len(class_games):
        return None
    members = class_games[first - 1:last]
    if len(members) != n or n < 100:
        return None
    covered = [game for game in members
               if type(getattr(game, 'accuracy', None)) in (int, float)
               and isfinite(game.accuracy) and 0 <= game.accuracy <= 100]
    distinct = {name.strip().casefold() for game in covered
                if isinstance((name := getattr(game, 'opponent_username', None)), str)
                and name.strip()}
    high90 = sum(game.accuracy >= 90 for game in covered)
    high95 = sum(game.accuracy >= 95 for game in covered)
    # Do not promote a results period solely on Chess.com Accuracy that was
    # published for another portion of the player's history.
    if (len(covered) < 30 or len(covered) < .60 * n or len(distinct) < 15
            or high90 < 10 or high95 < 5):
        return None
    return {'games': n, 'accuracy_covered': len(covered),
            'at_least_90': high90, 'at_least_95': high95,
            'distinct_observed_opponents': len(distinct)}


def corroboration(selected, sparse_flags, *, period_games=None):
    if not isinstance(selected, dict) or not sparse_flags:
        return None
    period_accuracy = _selected_accuracy(selected, period_games)
    if period_accuracy is None:
        return None
    results=selected.get('results') or {}
    guard=selected.get('search_guard') or {}
    rating=selected.get('rating_support') or {}
    opponent=selected.get('opponent_support') or {}
    chronology=selected.get('chronology_coverage') or {}
    z=results.get('standardized_residual')
    n=results.get('games')
    if (not isinstance(z,(int,float)) or isinstance(z,bool) or not isfinite(z)
            or not isinstance(n,int) or isinstance(n,bool) or n<100
            or z<3.0 or rating.get('unreliable') or rating.get('unverified')
            or rating.get('anchor_status')!='supported'
            or opponent.get('opponent_concentrated')
            or opponent.get('opponent_identity_unverified')
            or opponent.get('period_distinct_opponents',0)<30
            or chronology.get('complete') is not True):
        return None
    match=next((flag for flag in sparse_flags
                if flag.get('time_class')==selected.get('time_class')),None)
    if (match is None or match.get('accuracy_covered',0)<100
            or match.get('at_least_90',0)<20 or match.get('at_least_95',0)<10
            or match.get('distinct_observed_opponents',0)<40):
        return None
    return {
        'kind':'combined_uncalibrated_review',
        'time_class':selected['time_class'],
        'results_games':n,
        'uncorrected_standardized_residual':z,
        'adjusted_model_bound':guard.get('adjusted_model_bound'),
        'searched_windows':guard.get('searched_windows'),
        'accuracy_covered':match['accuracy_covered'],
        'total_games':match['total_games'],
        'at_least_90':match['at_least_90'],
        'at_least_95':match['at_least_95'],
        'selected_period_accuracy':period_accuracy,
        'priority_effect':'LOW_to_MODERATE_only',
        'caution':'MODERATE is an exploratory manual-review tier based on combined observations, not a multiple-testing-corrected finding, gameplay proof, or a cheating probability. High is never assigned by this rule.'
    }


def apply_combined_review(priority, basis, combined_review):
    """Keep the manual-review promotion auditable and capped at MODERATE."""
    if priority=='LOW' and combined_review is not None:
        return 'MODERATE','combined_manual_review'
    return priority,basis
