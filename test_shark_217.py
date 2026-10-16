"""SharkBot 2.17 follow-up context memory regression tests."""
import time
import unittest
from types import SimpleNamespace

import shark_stats_query as stats


class ContextBoundTests(unittest.TestCase):
    def setUp(self):
        stats._CONTEXT.clear()

    def tearDown(self):
        stats._CONTEXT.clear()

    def test_expired_context_is_pruned_when_cache_is_full(self):
        old = time.monotonic() - stats._CONTEXT_TTL - 10
        for i in range(512):
            stats._CONTEXT[(1, 2, i)] = ("123", "badges", old)
        guild = SimpleNamespace(id=1)
        author = SimpleNamespace(id=999, display_name="Tester")
        self.assertIsNone(stats.answer("hello", guild, author, 2))
        self.assertEqual(len(stats._CONTEXT), 0)

    def test_capacity_is_enforced_for_unexpired_entries(self):
        now = time.monotonic()
        for i in range(512):
            stats._CONTEXT[(1, 2, i)] = ("123", "badges", now)
        guild = SimpleNamespace(id=1)
        author = SimpleNamespace(id=999, display_name="Tester")
        self.assertIsNone(stats.answer("hello", guild, author, 2))
        self.assertEqual(len(stats._CONTEXT), 511)
