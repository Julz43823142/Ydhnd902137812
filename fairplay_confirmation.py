"""Paired Stockfish *search reliability*, independent of player success.

The legacy 'stable' predicate conflated a player's move matching the engine
with the engine being consistent across fast/deep searches. Because every
difficult opportunity (including the player's mistakes) entered the
denominator, requiring 75% 'stable' effectively required 75% nearly perfect
human choices *in addition* to the separately tested anomaly hit rate.

v26 retains the original strict geometric predicate in owner evidence, but
uses completed paired objective-quality comparisons to gate the reliability
of gameplay claims. It never creates an anomaly hit or bypasses the existing
hit-rate, coverage, contributor, rating and period gates.
"""
from __future__ import annotations

import math


def paired_quality_confirmation(summary, threshold, *, coverage_floor=0.95):
    """Return testable facts and fail closed if paired scores are missing.

    Missing-v26-key fallback is restricted to synthetic/legacy fixtures:
    current live period_summary *always* supplies the three new counters,
    including zero when measurement is unavailable.
    """
    opportunities = summary.get("opportunities", 0)
    if (not isinstance(opportunities, (int, float)) or opportunities <= 0
            or not math.isfinite(opportunities)):
        return {"fraction": 0.0, "coverage": 0.0, "hits": 0,
                "fraction_passed": False, "coverage_passed": False,
                "legacy_fixture": False}
    legacy_fixture = not any(k in summary for k in (
        "paired_evaluated_opportunities", "quality_stable_opportunities",
        "quality_stable_hits"))
    if legacy_fixture:
        measured = opportunities
        consistent = summary.get("stable_opportunities", 0)
        hits = summary.get("stable_hits", 0)
    else:
        measured = summary.get("paired_evaluated_opportunities", 0)
        consistent = summary.get("quality_stable_opportunities", 0)
        hits = summary.get("quality_stable_hits", 0)
    valid = all(isinstance(value, (int, float))
                and not isinstance(value, bool) and math.isfinite(value)
                for value in (measured, consistent, hits))
    valid = valid and 0 <= hits <= consistent <= measured <= opportunities
    if not valid:
        return {"fraction": 0.0, "coverage": 0.0, "hits": 0,
                "fraction_passed": False, "coverage_passed": False,
                "legacy_fixture": legacy_fixture}
    rate = consistent / opportunities
    coverage = measured / opportunities
    return {"fraction": rate, "coverage": coverage, "hits": hits,
            "fraction_passed": rate >= threshold,
            "coverage_passed": coverage >= coverage_floor,
            "legacy_fixture": legacy_fixture}
