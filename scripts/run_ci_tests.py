"""Deterministic whole-module test shards; weights affect scheduling, never coverage.

Measured weights are test-body seconds from a successful full GitHub CI run.
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

# Timing hints from successful full CI run 40147584590; no account data.
MEASURED_SECONDS = json.loads(Path(__file__).with_name('ci_test_weights.json').read_text())


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


def partition(weights: dict[str, float], count: int, *, extra_first: float = 0) -> list[list[str]]:
    if count < 1:
        raise ValueError('Shard count must be positive')
    if any(not math.isfinite(value) or value <= 0 for value in weights.values()):
        raise ValueError('Scheduling weights must be positive and finite')
    groups = [[] for _ in range(count)]
    if not math.isfinite(extra_first) or extra_first < 0:
        raise ValueError('Extra load must be finite and non-negative')
    loads = [extra_first] + [0.0] * (count - 1)
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
    groups = partition(weights, args.shards, extra_first=20)
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
