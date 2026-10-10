"""Read-only sensitivity audit for the deep gameplay stability gate.

This is NOT a counterfactual priority classifier. Changing the stability
fraction cannot bypass coverage, hit quality, paired retention, personal
baselines, other proof gates or independent label calibration.
"""
from __future__ import annotations

import math

SCHEMA = "sharkbot-shadow-stability-v1"
CANDIDATES = (0.60, 0.65, 0.70, 0.75, 0.80)
STABILITY_BLOCKER = "deep semantic-quality stability"


def summarize_shadow_stability(result):
    gameplay = (getattr(result, "diagnostics", {}) or {}).get("gameplay") or {}
    cases = []
    keys = (("selected", "best"), ("broad", "best_broad"), ("high", "best_high"))
    seen = set()
    for label, key in keys:
        period = gameplay.get(key)
        if not isinstance(period, dict) or period.get("acute"):
            continue
        # Never include account, game IDs, timestamps, moves or raw position data.
        marker = tuple(period.get("ids") or ())
        if marker in seen:
            continue
        seen.add(marker)
        deep = period.get("deep") or {}
        if not isinstance(deep, dict):
            continue
        rate = deep.get("stability_fraction")
        if not isinstance(rate, (int, float)) or not math.isfinite(rate):
            continue
        blockers = list(deep.get("blockers") or ())
        others = [value for value in blockers if value != STABILITY_BLOCKER]
        fast_gate = bool(period.get("qualified"))
        cases.append({
            "kind": label,
            "time_class": period.get("class") if period.get("class") in ("blitz", "rapid", "bullet") else "unknown",
            "games": len(marker),
            "deep_opportunities": int((deep.get("summary") or {}).get("opportunities") or 0),
            "stability_fraction": round(rate, 5),
            "fast_gate_qualified": fast_gate,
            "other_deep_blockers": len(others),
            "threshold_probe": {
                f"{threshold:.2f}": {
                    "stability_gate_pass": bool(rate >= threshold),
                    "all_other_recorded_deep_gates_pass": not others,
                    "candidate_gate_set_pass": bool(fast_gate and not others and rate >= threshold),
                } for threshold in CANDIDATES
            },
        })
    return {
        "schema": SCHEMA,
        "scoring_influence": False,
        "current_stability_threshold": 0.75,
        "cases": cases,
        "interpretation": (
            "Diagnostic gate sensitivity for selected periods only. These "
            "are NOT recalculated LOW/MODERATE/HIGH/VERY HIGH decisions. "
            "All other gates and independent validation remain necessary."),
    }
