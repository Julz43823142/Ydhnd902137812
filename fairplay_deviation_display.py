"""Display-only results deviation index; never consumed by classification.

Scale is heuristic, not calibrated: residual 2.4 and excess .20 each supply
half-saturation. Both are required, so a huge sample with a tiny effect or a
small extreme streak cannot saturate the index alone. No tier boundaries.
"""
from math import isfinite, sqrt
from fairplay_outcome_study import SCENARIOS, CONFIG as STUDY_CONFIG

_BALANCED = next(s for s in SCENARIOS if s.name == 'balanced')


def deviation_score(result):
    selected=result.diagnostics.get('method3',{}).get('selected')
    if result.priority=='INSUFFICIENT DATA' or not selected:
        return None
    if selected.get('classification_guards') or selected.get('rating_unverified_downgrade') or selected.get('rating_reliability_downgrade'):
        return None
    row=selected.get('results') or {}
    n=row.get('games');z=row.get('standardized_residual');excess=row.get('score_excess')
    if not all(type(v) in (int,float) and isfinite(v) for v in (n,z,excess)) or n<5:
        return None
    if z<=0 or excess<=0:return 0
    # Equivalent to sqrt((z/(z+2.4))*(excess/(excess+.20))), overflow-safe.
    return min(99,round(100*sqrt((1/(1+2.4/z))*(1/(1+.20/excess)))))

# This index is intentionally uncalibrated. It is an ordinal screening measure,
# not a misconduct probability or a replacement for Method 3 evidence gates.
# Opening/timing charts remain descriptive until independently validated.
_SUSPICION_BANDS = {'LOW': (0, 24), 'MODERATE': (25, 49),
                    'HIGH': (50, 74), 'VERY HIGH': (75, 99)}
_ALTERNATE_ACCURACY_ROUTES = {
    'accuracy_frequency': 'accuracy_frequency_signals',
    'combined_manual_review': 'corroborating_review_signal',
}


def _finite_number(value):
    return type(value) in (int, float) and isfinite(value)


def _matched_accuracy_strength(selected):
    """Only eligible, search-guarded Accuracy from the same selected window."""
    if selected.get('reference_mode') != 'personal_earlier':
        return None
    checks = (selected.get('scenarios') or {}).get('balanced') or {}
    guard = selected.get('accuracy_search_guard') or {}
    # Recheck the *numeric* gates, not only cached pass booleans. If evidence
    # is changed or partially serialized, an old True must not create a score.
    adjusted = guard.get('adjusted_reference_model_bound')
    if (checks.get('accuracy_pass') is not True or guard.get('moderate_pass') is not True
            or not _finite_number(adjusted) or not 0 <= adjusted <= .10):
        return None
    accuracy = selected.get('accuracy') or {}
    matched, games = accuracy.get('matched_games'), selected.get('games')
    excess, fraction = accuracy.get('median_excess'), accuracy.get('above_reference_fraction')
    if (type(matched) is not int or not _finite_number(games)
            or games < 5 or matched < 5 or matched > games
            or matched < STUDY_CONFIG.coverage * games
            or not _finite_number(excess) or excess < _BALANCED.accuracy_delta
            or not _finite_number(fraction)
            or not _BALANCED.above_reference_fraction <= fraction <= 1):
        return None
    magnitude = excess / (excess + _BALANCED.accuracy_delta)
    consistency = max(0., 2 * fraction - 1.)
    return sqrt(magnitude * consistency)


def _historical_frequency_strength(data):
    """Continuous within-band strength from an eligible historical CHANGE.

    Source evidence must include full denominators and the search-adjusted
    tail guard. Absolute high Accuracy percentages do not qualify, even when
    they look impressive. This is ordinal, not a cheating probability.
    """
    signals = data.get('accuracy_frequency_signals')
    if not isinstance(signals, list):
        return None
    strengths = []
    for row in signals:
        if not isinstance(row, dict) or row.get('time_class') not in ('rapid', 'blitz', 'bullet'):
            continue
        control = row.get('time_control')
        if not isinstance(control, str) or not control:
            continue
        names = ('recent_games', 'recent_at_least_90', 'baseline_games',
                 'baseline_at_least_90', 'recent_accuracy_covered',
                 'recent_total_games', 'baseline_accuracy_covered',
                 'baseline_total_games', 'distinct_opponents')
        if any(type(row.get(k)) is not int for k in names):
            continue
        (recent_n, recent_hits, baseline_n, baseline_hits, recent_covered,
         recent_total, base_covered, base_total, opponents) = (row[k] for k in names)
        adjusted = row.get('adjusted_tail_bound')
        if (recent_n < 20 or baseline_n < 40 or opponents < 10
                or recent_total < 30 or base_total < 60
                or not 0 <= recent_hits <= recent_n <= recent_covered <= recent_total
                or not 0 <= baseline_hits <= baseline_n <= base_covered <= base_total
                or recent_covered < .8 * recent_total
                or base_covered < .6 * base_total
                or not _finite_number(adjusted) or not 0 <= adjusted <= .01):
            continue
        recent_rate = recent_hits / recent_n
        baseline_rate = baseline_hits / baseline_n
        delta = recent_rate - baseline_rate
        if delta <= .15:
            continue
        # Rate headroom is normalized to the baseline; .15 is the existing
        # production effect gate and .01 its multiplicity-adjusted tail guard.
        magnitude = min(1., max(0., (delta - .15) / max(.001, 1. - baseline_rate - .15)))
        search_support = 1. - adjusted / .01
        strengths.append(.8 * magnitude + .2 * search_support)
    return max(strengths) if strengths else None


def _combined_review_strength(result, data, selected):
    """Use corroborated result effect, never a default category midpoint."""
    row = data.get('corroborating_review_signal')
    if not isinstance(row, dict) or row.get('kind') != 'combined_uncalibrated_review':
        return None
    if row.get('priority_effect') != 'LOW_to_MODERATE_only':
        return None
    numbers = ('results_games', 'accuracy_covered', 'total_games',
               'at_least_90', 'at_least_95', 'distinct_observed_opponents')
    # The archived review signal does not contain distinct_observed_opponents
    # in older records; it instead carries evidence for the same control.
    required = numbers[:-1]
    if any(type(row.get(key)) is not int for key in required):
        return None
    n, covered, total, hits90, hits95 = (row[key] for key in required)
    residual = row.get('uncorrected_standardized_residual')
    result_row = selected.get('results') or {}
    actual_z = result_row.get('standardized_residual')
    if (n < 100 or covered < 100 or not 0 <= hits95 <= hits90 <= covered <= total
            or not _finite_number(residual) or residual < 3.
            or not _finite_number(actual_z) or abs(actual_z-residual) > 1e-6
            or result_row.get('games') != n
            or selected.get('time_class') != row.get('time_class')):
        return None
    value = deviation_score(result)
    return value / 100. if value is not None else None


def suspicion_score(result):
    """0–100-scale heuristic: higher means greater *manual review* suspicion.

    The existing safeguarded final priority sets nonoverlapping score bands.
    Eligible results plus qualified historical Accuracy refine the band, never
    create new HIGH votes. Unqualified descriptive results never inflate an
    Accuracy-only MODERATE period. Alternative routes use validated numeric
    evidence; no categorical fixed midpoint is used.
    Unknown inputs stay unknown, not zero. No engines or further PGN work.
    """
    priority = getattr(result, 'priority', None)
    if priority not in _SUSPICION_BANDS or getattr(result, 'partial', False):
        return None
    diagnostics = getattr(result, 'diagnostics', None)
    if not isinstance(diagnostics, dict):
        return None
    data = diagnostics.get('method3')
    if not isinstance(data, dict):
        return None
    lo, hi = _SUSPICION_BANDS[priority]
    basis = data.get('priority_basis', 'results')
    # v81's absolute-frequency route was not calibrated, so its fixed 37
    # must never be reinterpreted as an individualized suspicion score.
    if basis == 'absolute_accuracy_frequency':
        return None
    if basis in _ALTERNATE_ACCURACY_ROUTES:
        if priority != 'MODERATE':
            return None
        selected = data.get('selected')
        if not isinstance(selected, dict):
            return None
        strength = (_historical_frequency_strength(data)
                    if basis == 'accuracy_frequency'
                    else _combined_review_strength(result, data, selected))
        if strength is None:
            return None
        return lo + round((hi-lo) * min(1., max(0., strength)))

    selected = data.get('selected')
    if not isinstance(selected, dict):
        return None
    results_index = deviation_score(result)
    results_strength = results_index / 100. if results_index is not None else None
    accuracy_strength = _matched_accuracy_strength(selected)

    if priority == 'VERY HIGH':
        # Production requires a *strict* adjusted earlier-reference guard.
        limit = (selected.get('accuracy_search_guard') or {}).get('adjusted_reference_model_bound')
        if (results_strength is None or accuracy_strength is None
                or not _finite_number(limit) or not 0 <= limit <= .01):
            return None
    if basis == 'accuracy':
        # An Accuracy-only MODERATE period explicitly FAILED the results-review
        # threshold. Its descriptive results deviation must not inflate the
        # score as though this were corroborated results evidence.
        primary, supporting = accuracy_strength, None
    else:
        primary, supporting = results_strength, accuracy_strength
    if primary is None:
        return None
    strength = primary
    if supporting is not None:
        # Extra qualifying evidence can only add modest support within its
        # existing priority band. Do not double count or infer cheating odds.
        strength += .2 * supporting * (1. - strength)
    return min(99, lo + round((hi - lo) * min(1., max(0., strength))))
