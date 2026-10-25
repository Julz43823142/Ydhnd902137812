"""Deterministic whole-module test shards; weights affect scheduling, never coverage.

Measured weights are local test-only seconds from the October 2026 profile.
They are scheduling hints, not CI performance guarantees. Unknown/new modules
use their AST test count and are always included. Refresh hints when timings drift.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
import math
from pathlib import Path
import sys
import time
import unittest

MEASURED_SECONDS = {
    'test_fairplay_human': 36.04,
    'test_fairplay': 32.83,
    'test_fairplay_v6': 27.89,
    'test_fairplay_v4': 26.40,
    'test_fairplay_local_timing': 15.13,
    'test_independent_formula_lab': 12.00,
    'test_fairplay_method3_unrated': 11.32,
    'test_fairplay_periods': 4.83,
    'test_accuracy_optimizer': 4.29,
    'test_fairplay_repeatability': 3.90,
    'test_pet_trading': 3.88,
    'test_fairplay_method3_coverage': 2.06,
    'test_fairplay_snapshot_fetch': 2.03,
    'test_daily_mystery_box': 1.78,
    'test_fairplay_v50': 1.74,
}


def discover_weights(root: Path) -> dict[str, float]:
    weights = {}
    for path in sorted(root.glob('test_*.py')):
        if not path.is_file():
            continue
        name = path.stem
        if name in MEASURED_SECONDS:
            weights[name] = MEASURED_SECONDS[name]
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            count = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name.startswith('test') for node in ast.walk(tree))
        except (SyntaxError, UnicodeError):
            # Still schedule malformed tests: unittest must report their import error.
            count = 1
        weights[name] = max(1.0, count * .5)
    return weights


def partition(weights: dict[str, float], count: int) -> list[list[str]]:
    if count < 1:
        raise ValueError('Shard count must be positive')
    if any(not math.isfinite(value) or value <= 0 for value in weights.values()):
        raise ValueError('Scheduling weights must be positive and finite')
    groups = [[] for _ in range(count)]
    loads = [0.0] * count
    # Longest first, explicit stable tie breaks independent of dictionary order.
    for name in sorted(weights, key=lambda name: (-weights[name], name)):
        index = min(range(count), key=lambda index: (loads[index], index))
        groups[index].append(name)
        loads[index] += weights[name]
    return [sorted(group) for group in groups]


class TimedResult(unittest.TextTestResult):
    """Diagnostic test-body timings (does not include module/class setup)."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.module_seconds = Counter()

    def startTest(self, test):
        self._started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.module_seconds[type(test).__module__] += time.monotonic() - self._started
        super().stopTest(test)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', type=int, required=True)
    parser.add_argument('--shards', type=int, default=6)
    parser.add_argument('--list', action='store_true', help='Print the plan without importing tests')
    args = parser.parse_args(argv)
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error('Require 0 <= shard < shards and shards >= 1')
    root = Path(__file__).resolve().parents[1]
    weights = discover_weights(root)
    if not weights:
        parser.error('No root test modules found')
    groups = partition(weights, args.shards)
    selected = groups[args.shard]
    print(json.dumps({'shard': args.shard, 'shards': args.shards,
                      'total_modules': len(weights), 'modules': selected,
                      'estimated_seconds': round(sum(weights[name] for name in selected), 2)}), flush=True)
    if args.list:
        return 0
    # Root comes before scripts/, matching the previous workflow's preload.
    sys.path.insert(0, str(root))
    import fairplay_outcome_study  # noqa: F401
    started = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromNames(selected)
    result = unittest.TextTestRunner(verbosity=0, resultclass=TimedResult).run(suite)
    print(json.dumps({'shard': args.shard, 'tests': result.testsRun,
                      'wall_seconds': round(time.monotonic() - started, 3),
                      'test_body_seconds_by_module': dict(result.module_seconds)}), flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
