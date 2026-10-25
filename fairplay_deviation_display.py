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
    'absolute_accuracy_frequency': 'absolute_accuracy_frequency_signals',
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


def _has_manual_review_evidence(data, basis):
    """Prevent truthy placeholders from masquerading as an Accuracy route."""
    source = data.get(_ALTERNATE_ACCURACY_ROUTES[basis])
    if basis == 'combined_manual_review':
        return (isinstance(source, dict)
                and source.get('kind') == 'combined_uncalibrated_review'
                and source.get('priority_effect') == 'LOW_to_MODERATE_only')
    return (isinstance(source, list) and any(
        isinstance(item, dict) and item.get('time_class') in ('rapid', 'blitz', 'bullet')
        for item in source))


def suspicion_score(result):
    """0–100-scale heuristic: higher means greater *manual review* suspicion.

    The existing safeguarded final priority sets nonoverlapping score bands.
    Eligible results plus qualified historical Accuracy refine the band, never
    create new HIGH votes. Unqualified descriptive results never inflate an
    Accuracy-only MODERATE period. Alternative published-Accuracy review routes
    use the MODERATE midpoint instead of unrelated results evidence.
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
    if basis in _ALTERNATE_ACCURACY_ROUTES:
        if priority != 'MODERATE' or not _has_manual_review_evidence(data, basis):
            return None
        return (lo + hi) // 2

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
