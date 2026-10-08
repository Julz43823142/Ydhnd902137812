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


@dataclass(frozen=True)
class SearchObservation:
    position_key: tuple[int, int] | None
    engine_key: str
    budget_mode: str
    budget_value: int
    completed: bool
    achieved_depth: int | None = None
    exact_scores: bool = True
    cpl: float | None = None
    scaled_loss: float | None = None
    played_rank: int | None = None
    best_move: str | None = None


def _ready(search: SearchObservation) -> Check:
    if not search.engine_key or search.position_key is None:
        return MISSING
    if search.budget_mode not in {"nodes", "depth"}:
        return Check(State.UNKNOWN, Reason.BUDGET_MISMATCH)
    if not isinstance(search.budget_value, int) or isinstance(search.budget_value, bool) or search.budget_value <= 0:
        return Check(State.UNKNOWN, Reason.BUDGET_MISMATCH)
    if not search.completed:
        return Check(State.UNKNOWN, Reason.SEARCH_INCOMPLETE)
    if search.budget_mode == "depth" and (
        search.achieved_depth is None or search.achieved_depth < search.budget_value
    ):
        return Check(State.UNKNOWN, Reason.SEARCH_INCOMPLETE)
    if not search.exact_scores:
        return Check(State.UNKNOWN, Reason.SCORE_BOUND)
    return PASSED


def check_counterfactual_contract(objective: SearchObservation,
                                  alternative: SearchObservation) -> Check:
    """Within one stage, compare objective and alternative at equal budgets."""
    for search in (objective, alternative):
        result = _ready(search)
        if result.state is not State.PASS:
            return result
    if objective.position_key != alternative.position_key or objective.engine_key != alternative.engine_key:
        return Check(State.UNKNOWN, Reason.PROVENANCE_MISMATCH)
    if objective.budget_mode != alternative.budget_mode or objective.budget_value != alternative.budget_value:
        return Check(State.UNKNOWN, Reason.BUDGET_MISMATCH)
    return PASSED


def _valid_loss(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


@dataclass(frozen=True)
class QualityComparison:
    quality: Check
    same_rank: bool | None = None
    same_best_move: bool | None = None
    near_best_preserved: bool | None = None


def compare_search_quality(fast: SearchObservation, deep: SearchObservation, *,
                           near_best_cp: float, cp_tolerance: float,
                           scaled_tolerance: float) -> QualityComparison:
    for value in (near_best_cp, cp_tolerance, scaled_tolerance):
        if not _valid_loss(value):
            raise ValueError("Invalid comparison tolerance")
    for search in (fast, deep):
        result = _ready(search)
        if result.state is not State.PASS:
            return QualityComparison(result)
    if fast.position_key != deep.position_key or fast.engine_key != deep.engine_key:
        return QualityComparison(Check(State.UNKNOWN, Reason.PROVENANCE_MISMATCH))
    if not all(_valid_loss(v) for v in (fast.cpl, deep.cpl, fast.scaled_loss, deep.scaled_loss)):
        return QualityComparison(Check(State.UNKNOWN, Reason.INVALID_SCORE))
    near = fast.cpl <= near_best_cp and deep.cpl <= near_best_cp
    stable = (abs(fast.scaled_loss - deep.scaled_loss) <= scaled_tolerance
              and (near or abs(fast.cpl - deep.cpl) <= cp_tolerance))
    return QualityComparison(PASSED if stable else Check(State.FAIL, Reason.QUALITY_CHANGED),
        same_rank=fast.played_rank == deep.played_rank if fast.played_rank is not None and deep.played_rank is not None else None,
        same_best_move=fast.best_move == deep.best_move if fast.best_move is not None and deep.best_move is not None else None,
        near_best_preserved=near)


@dataclass(frozen=True)
class CandidateAudit:
    route: str
    time_class: str
    games: int
    opportunities: int
    structurally_eligible: bool
    checks: Mapping[str, Check]


def select_route_diagnostics(candidates: Iterable[CandidateAudit]) -> dict[str, CandidateAudit]:
    """Representative per route for display only. Never prunes scoring."""
    grouped = {}
    for candidate in candidates:
        grouped.setdefault(candidate.route, []).append(candidate)
    def key(c):
        measured = tuple(c.checks.values())
        passed = sum(row.state is State.PASS for row in measured)
        return (c.structurally_eligible, bool(measured) and passed == len(measured),
                passed / max(1, len(measured)), c.opportunities, c.games)
    return {route: max(rows, key=key) for route, rows in sorted(grouped.items())}
