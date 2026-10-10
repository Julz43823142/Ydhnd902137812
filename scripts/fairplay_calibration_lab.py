"""Offline, player-disjoint Fair Play calibration reporting.

Never imported by live scoring. Labels must come from controlled experiments or
independently verified ground truth; ordinary suspicions are *not* labels.
All output consists of aggregate counts and confidence intervals, without
player identifiers, moves, usernames, game URLs or individual predictions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

PRIORITIES = ("INSUFFICIENT DATA", "LOW", "MODERATE", "HIGH", "VERY HIGH")
THRESHOLDS = ("MODERATE", "HIGH", "VERY HIGH")
LABELS = frozenset(("controlled_fair", "controlled_assisted"))
SCHEMA = "sharkbot-v24-offline-calibration-v1"


def wilson_interval(positive, total, z=1.96):
    """Wilson binomial interval; descriptive when samples are correlated."""
    if total <= 0:
        return None
    p = positive / total
    z2 = z * z
    denominator = 1 + z2 / total
    middle = (p + z2 / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / denominator
    return [round(max(0.0, middle - margin), 6),
            round(min(1.0, middle + margin), 6)]


def partition(player_key, *, salt="sharkbot-v24-research-only", holdout_fraction=0.25):
    """Fixed player-level assignment; never split different games of one player."""
    if not isinstance(player_key, str) or not player_key:
        raise ValueError("A nonempty pseudonymous player_key is required")
    if not 0 < holdout_fraction < 1:
        raise ValueError("holdout_fraction must be between zero and one")
    digest = hashlib.sha256((salt + "\0" + player_key).encode("utf-8")).digest()
    score = int.from_bytes(digest[:8], "big") / (1 << 64)
    return "holdout" if score < holdout_fraction else "development"


def _priority(value):
    if value not in PRIORITIES:
        raise ValueError("Invalid review priority")
    return PRIORITIES.index(value)


def _parse_cases(rows):
    """Validate all rows before calculating anything, to avoid partial reports."""
    parsed = []
    label_by_player = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every case must be an object")
        player = row.get("player_key")
        if not isinstance(player, str) or not player or len(player) > 128:
            raise ValueError("player_key must be a bounded pseudonymous string")
        truth = row.get("truth")
        if truth not in LABELS:
            # Suspected/not-reviewed cases are never treated as positives.
            if truth in (None, "unknown", "suspected"):
                continue
            raise ValueError("Unsupported truth label")
        previous = label_by_player.setdefault(player, truth)
        if previous != truth:
            raise ValueError("Conflicting labels for one player")
        _priority(row.get("priority"))
        shadow = row.get("shadow", {})
        if not isinstance(shadow, dict):
            raise ValueError("shadow must be a mapping")
        for name, prediction in shadow.items():
            if (not isinstance(name, str) or len(name) > 48 or not name
                    or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name)):
                raise ValueError("Invalid shadow scenario identifier")
            _priority(prediction)
        parsed.append((player, truth, row["priority"], dict(shadow)))
    return parsed


def _metrics(player_outcomes):
    """Count *players*, not correlated moves/games, for each category."""
    fair = [p for truth, p in player_outcomes if truth == "controlled_fair"]
    assisted = [p for truth, p in player_outcomes if truth == "controlled_assisted"]
    result = {"players": len(fair) + len(assisted),
              "controlled_fair": len(fair), "controlled_assisted": len(assisted)}
    for threshold in THRESHOLDS:
        rank = _priority(threshold)
        fp = sum(score >= rank for score in fair)
        tp = sum(score >= rank for score in assisted)
        result[threshold] = {
            "false_positive": fp, "true_positive": tp,
            "false_positive_rate": round(fp / len(fair), 6) if fair else None,
            "sensitivity": round(tp / len(assisted), 6) if assisted else None,
            "false_positive_wilson95": wilson_interval(fp, len(fair)),
            "sensitivity_wilson95": wilson_interval(tp, len(assisted)),
            "precision_on_test_cohort": (
                round(tp / (tp + fp), 6) if tp + fp else None),
        }
    return result


def evaluate(rows, *, salt="sharkbot-v24-research-only"):
    parsed = _parse_cases(rows)
    grouped = defaultdict(list)
    for player, truth, priority, shadow in parsed:
        grouped[player].append((truth, priority, shadow))
    scenarios = {"production"}
    for cases in grouped.values():
        for _, _, shadows in cases:
            scenarios.update(shadows)
    output = {"schema": SCHEMA, "research_only": True, "production_influence": False,
              "scans_evaluated": len(parsed), "players_evaluated": len(grouped),
              "partition": "SHA256 fixed seed, player-disjoint 75/25",
              "scenarios": {}}
    for scenario in sorted(scenarios):
        groups = defaultdict(list)
        missing = 0
        for player, cases in grouped.items():
            # A scenario is comparable only when ALL scans for that player have
            # a score; otherwise do not silently cherry-pick a favorable scan.
            values = []
            for truth, current, shadows in cases:
                prediction = current if scenario == "production" else shadows.get(scenario)
                if prediction is None:
                    break
                values.append(_priority(prediction))
            if len(values) != len(cases):
                missing += 1
                continue
            groups[partition(player, salt=salt)].append((cases[0][0], max(values)))
        output["scenarios"][scenario] = {
            "missing_players": missing,
            "development": _metrics(groups["development"]),
            "holdout": _metrics(groups["holdout"]),
        }
    output["limitations"] = [
        "Only independently controlled labels are valid; suspected accounts are excluded.",
        "Holdout rates remain uncertain for small cohorts and do not prove calibration.",
        "A selected shadow threshold must be judged on untouched holdout players.",
        "Cross-platform rating and time-control shifts require separate validation.",
        "No model score is a misconduct probability and no automatic punishment is authorized.",
    ]
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline labeled Fair Play priority comparison")
    parser.add_argument("--cases", required=True, type=Path,
                        help="PRIVATE JSONL: player_key, truth, priority, optional shadow map")
    parser.add_argument("--output", type=Path, help="aggregate-only JSON report path")
    args = parser.parse_args(argv)
    with args.cases.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    report = evaluate(rows)
    summary = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(summary + "\n", encoding="utf-8")
    else:
        print(summary)


if __name__ == "__main__":
    main()
