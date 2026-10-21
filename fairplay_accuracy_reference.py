"""Finite-reference upper tails for earlier personal Accuracy comparisons.

The null is independent scores from the same stationary continuous distribution
within each matched context. It is a model assumption, not a fair-play norm.
An empirical 90th percentile is random; its future exceedances are dependent
after averaging over the shared reference. Treating them as IID p=.10 is wrong.

For n reference scores, the interpolated percentile is at least order statistic
k=floor(.9*(n-1))+1. Its exceedance probability is stochastically bounded by
Beta(n+1-k,k); mixing future Bernoulli trials gives a beta-binomial upper tail.
Ties only make strict exceedances more conservative.

Different, possibly overlapping reference pools use a union bound: at a FIXED q,
the probability any pool has true exceedance probability above q is at most the
sum of its exact order-statistic tails. Otherwise the independent future scores
have exceedance probabilities <=q. Add their binomial tail. Minimizing these
valid bounds over a fixed grid is valid because every bound describes the same
unconditional event, and depends only on sample sizes and the hit threshold.
"""
from functools import lru_cache
from math import exp, lgamma, log, log1p


def _sum_logs(values):
    values = list(values)
    if not values:
        return 0.0
    largest = max(values)
    return min(1.0, exp(largest) * sum(exp(value-largest) for value in values))


@lru_cache(maxsize=32768)
def _binomial_lower(n, last, probability):
    if last < 0:
        return 0.0
    if last >= n or probability <= 0:
        return 1.0
    if probability >= 1:
        return 0.0
    return _sum_logs(lgamma(n+1)-lgamma(j+1)-lgamma(n-j+1)
                     + j*log(probability)+(n-j)*log1p(-probability)
                     for j in range(last+1))


@lru_cache(maxsize=16384)
def _beta_binomial_tail(n, order, future, hits):
    alpha, beta = n+1-order, order
    normalizer = lgamma(alpha+beta)-lgamma(alpha)-lgamma(beta)
    return _sum_logs(lgamma(future+1)-lgamma(j+1)-lgamma(future-j+1)
                     + lgamma(j+alpha)+lgamma(future-j+beta)
                     - lgamma(future+alpha+beta)+normalizer
                     for j in range(hits, future+1))


@lru_cache(maxsize=16384)
def _reference_tail(pools, future, hits):
    if hits <= 0 or not pools:
        return 1.0, 'unavailable'
    if len(pools) == 1:
        n, order = pools[0]
        return _beta_binomial_tail(n, order, future, hits), 'finite_reference_beta_binomial'
    best = 1.0
    # A prespecified grid, with no reference scores or labels used to tune q.
    for step in range(1, 100):
        q = step/100
        if q >= hits/future:
            break
        uncertainty = sum(_binomial_lower(n, n-order, q) for n, order in pools)
        if uncertainty >= best:
            continue
        conditional_tail = _binomial_lower(future, future-hits, 1-q)
        best = min(best, uncertainty+conditional_tail)
    return min(1.0, best), 'finite_reference_union_bound'


def accuracy_reference_bound(accuracy):
    """Return a guard from deduplicated pool metadata, without game identities.

The beta-binomial law does not describe the research comparison's median of
several players' quantiles. Such references remain descriptive until a suitable
model is provided; missing metadata never silently falls back to p=.10.
    """
    future = accuracy['matched_games']
    groups = accuracy.get('reference_pools', ())
    if (not future or accuracy.get('unmodelled_reference_games', 0)
            or sum(group['matched_games'] for group in groups) != future):
        return 1.0, 'unavailable'
    pools = tuple(sorted((group['games'], group['upper_order']) for group in groups))
    if any(n < 1 or not 1 <= order <= n for n, order in pools):
        return 1.0, 'unavailable'
    hits = round((accuracy['above_reference_fraction'] or 0)*future)
    return _reference_tail(pools, future, hits)
