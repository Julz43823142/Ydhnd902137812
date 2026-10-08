"""Evidence accounting and search contracts; no production scoring."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable, Mapping


class State(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Reason(str, Enum):
    NONE = "none"
    MISSING = "missing_measurement"
    NOT_REACHED = "stage_not_reached"
    EXISTING_GATE = "existing_eligibility_gate"
    BOOK = "opening_reference"
    FORCED = "forced_decision"
    TRIVIAL = "trivial_decision"
    EASY_CONVERSION = "easy_conversion"
    SEARCH_INCOMPLETE = "search_incomplete"
    SCORE_BOUND = "non_exact_engine_score"
    INVALID_SCORE = "invalid_engine_score"
    PROVENANCE_MISMATCH = "search_provenance_mismatch"
    BUDGET_MISMATCH = "search_budget_mismatch"
    QUALITY_CHANGED = "semantic_quality_changed"
    POLICY_UNAVAILABLE = "policy_unavailable"
    POLICY_MASS = "insufficient_known_policy_mass"
    ALTERNATIVES_INCOMPLETE = "counterfactuals_incomplete"
    UNSUPPORTED_REFERENCE = "unsupported_human_reference"


@dataclass(frozen=True)
class Check:
    state: State
    reason: Reason = Reason.NONE
    def __post_init__(self):
        if not isinstance(self.state, State) or not isinstance(self.reason, Reason):
            raise TypeError("Check must use fixed State and Reason")


PASSED = Check(State.PASS)
MISSING = Check(State.UNKNOWN, Reason.MISSING)


@dataclass(frozen=True)
class DecisionAudit:
    game_index: int
    ply: int
    checks: Mapping[str, Check]
    branches: Mapping[str, Check]


def _counts(checks):
    items = list(checks)
    states = Counter(item.state.value for item in items)
    failures = Counter(item.reason.value for item in items if item.state is State.FAIL)
    unknowns = Counter(item.reason.value for item in items if item.state is State.UNKNOWN)
    return {"pass": states["pass"], "fail": states["fail"], "unknown": states["unknown"],
            "failure_reasons": dict(sorted(failures.items())),
            "unknown_reasons": dict(sorted(unknowns.items()))}


def audit_decisions(decisions: Iterable[DecisionAudit], *, scope: str,
                    game_indices: Iterable[int], gate_order: Iterable[str],
                    branch_names: Iterable[str] = ()) -> dict:
    gates, branches = tuple(gate_order), tuple(branch_names)
    if len(gates) != len(set(gates)) or len(branches) != len(set(branches)):
        raise ValueError("Duplicate audit key")
    members = frozenset(game_indices)
    rows = [row for row in decisions if row.game_index in members]
    keys = [(row.game_index, row.ply) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate decision in evidence scope")
    survivors, stages = rows, []
    for gate in gates:
        checks = [row.checks.get(gate, MISSING) for row in survivors]
        counted = _counts(checks)
        before = len(survivors)
        survivors = [row for row, check in zip(survivors, checks) if check.state is State.PASS]
        assert before == counted["pass"] + counted["fail"] + counted["unknown"]
        assert len(survivors) == counted["pass"]
        stages.append({"gate": gate, "before": before, **counted, "after": len(survivors)})
    return {"scope": scope, "scope_games": len(members),
            "games_with_decisions": len({row.game_index for row in rows}),
            "decisions": len(rows), "stages": stages, "survivors": len(survivors),
            "branches": {name: {
                "whole_scope": _counts(row.branches.get(name, MISSING) for row in rows),
                "eligible_survivors": _counts(row.branches.get(name, MISSING) for row in survivors)}
                for name in branches}}
