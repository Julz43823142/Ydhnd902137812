"""Conservative combined manual-review triage for Method 3.

Uses already calculated statistics: no engine calls or additional searches.
A qualifying combination can yield MODERATE triage, never HIGH, and is not a calibrated misconduct estimate.
"""
from math import isfinite


def corroboration(selected, sparse_flags):
    if not isinstance(selected,dict) or not sparse_flags:
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
        'priority_effect':'LOW_to_MODERATE_only',
        'caution':'MODERATE is an exploratory manual-review tier based on combined observations, not a multiple-testing-corrected finding, gameplay proof, or a cheating probability. High is never assigned by this rule.'
    }
