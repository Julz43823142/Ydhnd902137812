"""Display-only results deviation index; never consumed by classification.

Scale is heuristic, not calibrated: residual 2.4 and excess .20 each supply
half-saturation. Both are required, so a huge sample with a tiny effect or a
small extreme streak cannot saturate the index alone. No tier boundaries.
"""
from math import isfinite, sqrt


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
