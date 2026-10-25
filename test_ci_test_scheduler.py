"""Scheduling invariants: balance must never change which tests run."""
import tempfile
import unittest
from pathlib import Path
from scripts.run_ci_tests import discover_weights, partition


class TestScheduler(unittest.TestCase):
    def test_exactly_once_and_order_independent(self):
        weights = {'test_' + str(i): float(i + 1) for i in range(25)}
        first = partition(weights, 6)
        self.assertEqual(first, partition(dict(reversed(list(weights.items()))), 6))
        flattened = [module for group in first for module in group]
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), set(weights))

    def test_new_and_malformed_modules_remain_covered(self):
        with tempfile.TemporaryDirectory(prefix='CI test path with spaces ') as folder:
            root = Path(folder)
            (root / 'test_new.py').write_text('def test_one(): pass\nasync def test_two(): pass\n')
            (root / 'test_bad.py').write_text('this is invalid Python !')
            (root / 'not_a_test.py').write_text('def test_hidden(): pass')
            (root / 'test_directory.py').mkdir()
            nested = root / 'nested'
            nested.mkdir()
            (nested / 'test_nested.py').write_text('')
            weights = discover_weights(root)
            self.assertEqual(set(weights), {'test_new', 'test_bad'})
            self.assertEqual(sum(len(group) for group in partition(weights, 6)), 2)

    def test_heavy_modules_are_spread_before_light_ones(self):
        weights = {f'test_heavy_{i}': 30.0 for i in range(6)}
        weights.update({f'test_light_{i}': 1.0 for i in range(24)})
        groups = partition(weights, 6)
        self.assertEqual([sum(weights[name] for name in group) for group in groups], [34] * 6)

    def test_invalid_weights_or_count_fail(self):
        for value in [0, -1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):partition({'test_a': value}, 6)
        with self.assertRaises(ValueError):partition({'test_a': 1}, 0)

    def test_repository_discovery_covers_every_root_test_file(self):
        root = Path(__file__).resolve().parent
        weights = discover_weights(root)
        expected = {path.stem for path in root.glob('test_*.py') if path.is_file()}
        self.assertEqual(set(weights), expected)
        self.assertEqual(sorted(name for group in partition(weights, 6) for name in group), sorted(expected))

    def test_extra_first_load_reserves_room_for_auxiliary_checks(self):
        weights = {f'test_{i}': 10.0 for i in range(10)}
        groups = partition(weights, 2, extra_first=20)
        self.assertEqual([len(group) for group in groups], [4, 6])
        for bad in [-1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):partition(weights, 2, extra_first=bad)
