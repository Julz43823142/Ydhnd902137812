"""Fail-closed coverage summary for independently executed research models.

This is NOT a cheating probability. A configured adapter or named project is
not evidence that weights were loaded or a legal prediction was returned.
"""
from __future__ import annotations

TERMINAL = ("evaluated", "partial", "no_positions", "unavailable",
            "failed", "skipped", "incompatible", "unknown")


def summarize_model_audit(audit):
    """Return a small private report without account moves, FENs or endpoints."""
    if not isinstance(audit, dict):
        return {"status": "unavailable", "reason": "audit_missing"}
    requested = audit.get("requested", [])
    statuses = audit.get("statuses", {})
    if not isinstance(requested, list) or not isinstance(statuses, dict):
        return {"status": "invalid", "reason": "malformed_audit"}
    outcomes = {}
    complete = incomplete = missing = 0
    for name in requested:
        item = statuses.get(name, {})
        state = item.get("status", "unknown") if isinstance(item, dict) else "unknown"
        positions = item.get("positions", 0) if isinstance(item, dict) else 0
        if not isinstance(positions, int) or positions < 0:
            positions = 0
        if state == "evaluated" and positions > 0 and name in audit.get("models", {}):
            complete += 1
            measured = True
        elif state == "partial" and positions > 0:
            incomplete += 1
            measured = True
        else:
            missing += 1
            measured = False
        outcomes[name] = {"status": state, "measured": measured,
                          "positions": positions}
    return {
        "schema": "sharkbot-research-coverage-v1",
        "status": "complete" if not (missing or incomplete) else "incomplete",
        "configured_models": len(requested),
        "fully_measured_models": complete,
        "partially_measured_models": incomplete,
        "not_measured_models": missing,
        "per_model": outcomes,
        "scoring_influence": False,
        "warning": ("Model agreement is descriptive; no model observation "
                    "can establish cheating or raise LOW/HIGH priority."),
    }
