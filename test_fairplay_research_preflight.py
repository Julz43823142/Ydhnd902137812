"""Offline tests: preflight must verify true persistence and never overwrite work."""
import unittest
from unittest.mock import patch

from scripts.preflight_fairplay_research_store import preflight


class ResearchPreflightTests(unittest.TestCase):
    def test_initial_marker_requires_durable_reload(self):
        class Store:
            enabled = True
            state = {}
            def research_state(self):
                return dict(self.state)
            def save_research(self, value):
                Store.state = dict(value)
                return True
        with patch("scripts.preflight_fairplay_research_store.read_cohorts",
                   return_value=({}, "test-digest")):
            self.assertEqual(preflight(Store), 0)
            self.assertEqual(Store.state["archive_version"], 3)
            self.assertEqual(preflight(Store), 0)

    def test_failed_initial_save_fails_closed(self):
        class Store:
            enabled = True
            def research_state(self):
                return {}
            def save_research(self, value):
                return False
        with patch("scripts.preflight_fairplay_research_store.read_cohorts",
                   return_value=({}, "test-digest")):
            with self.assertRaisesRegex(RuntimeError, "initialization failed"):
                preflight(Store)

    def test_unverified_initial_save_fails_closed(self):
        class Store:
            enabled = True
            def research_state(self):
                return {}
            def save_research(self, value):
                return True
        with patch("scripts.preflight_fairplay_research_store.read_cohorts",
                   return_value=({}, "test-digest")):
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                preflight(Store)

    def test_existing_checkpoint_is_not_overwritten(self):
        class Store:
            enabled = True
            def research_state(self):
                return {"digest": "test-digest", "cursor": 35,
                        "rows": [{} for _ in range(35)],
                        "evidence": ["ciphertext"] * 35,
                        "archive_version": 3, "finished": False}
            def save_research(self, value):
                raise AssertionError("must not overwrite existing research")
        with patch("scripts.preflight_fairplay_research_store.read_cohorts",
                   return_value=({}, "test-digest")):
            self.assertEqual(preflight(Store), 35)

    def test_changed_cohort_is_rejected(self):
        class Store:
            enabled = True
            def research_state(self):
                return {"digest": "different", "cursor": 0, "rows": [],
                        "evidence": [], "archive_version": 3}
            def save_research(self, value):
                raise AssertionError("must not overwrite incompatible state")
        with patch("scripts.preflight_fairplay_research_store.read_cohorts",
                   return_value=({}, "test-digest")):
            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                preflight(Store)


if __name__ == "__main__":
    unittest.main()
