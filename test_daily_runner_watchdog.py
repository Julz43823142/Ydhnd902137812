"""Watchdog must never dispatch if a Daily Puzzle run may be active."""
import unittest
from scripts.daily_runner_watchdog import should_dispatch


class WatchdogTests(unittest.TestCase):
    def test_any_active_run_suppresses_restart(self):
        for status in ("queued", "in_progress", "waiting", "requested", "pending"):
            with self.subTest(status=status):
                self.assertFalse(should_dispatch([
                    {"status": "completed"}, {"status": status},
                ]))

    def test_failed_or_completed_runner_may_restart(self):
        self.assertTrue(should_dispatch([
            {"status": "completed", "conclusion": "failure"},
            {"status": "completed", "conclusion": "success"},
        ]))

    def test_invalid_or_missing_api_data_fails_closed(self):
        for state in (None, [], {}, [{"conclusion": "failure"}],
                      [{"status": "mystery"}], [{"status": "completed"}, {}]):
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    should_dispatch(state)
