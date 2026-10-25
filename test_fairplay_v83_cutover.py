"""Deployment cutover: only explicit operator-tagged pushes may preempt Daily."""
import unittest
from pathlib import Path


class FairPlayCutover(unittest.TestCase):
    def test_only_explicit_push_marker_may_interrupt_daily_worker(self):
        source=Path('.github/workflows/daily_puzzle_and_answer.yml').read_text(encoding='utf-8')
        self.assertIn('group: daily-puzzle',source)
        self.assertIn("github.event_name == 'push'",source)
        self.assertIn("contains(github.event.head_commit.message, '[replace-daily-run]')",source)
        # Same permissions and normal clean self-rotation must remain.
        self.assertIn('actions: write',source)
        self.assertIn('Dispatch replacement after planned rotation',source)
        self.assertIn('DAILY_WORKFLOW_ROTATION_SECONDS: "15600"',source)
        # The ordinary scheduled and plain push runs do not match the marker.
        def cancel(event,commit_message):
            return event=='push' and '[replace-daily-run]' in commit_message
        self.assertFalse(cancel('schedule','[replace-daily-run]'))
        self.assertFalse(cancel('push','Update Economy and Pet Market'))
        self.assertFalse(cancel('workflow_dispatch','[replace-daily-run]'))
        self.assertTrue(cancel('push','Merge v83 [replace-daily-run]'))

    def test_v83_does_not_enable_engine_or_name_based_exemptions(self):
        from fairplay_method3 import VERSION
        self.assertEqual(VERSION,'fairplay-v83-landscape-and-coverage')
        sources=[Path(name).read_text(encoding='utf-8') for name in (
            'fairplay_method3.py','fairplay_method3_accuracy_frequency.py',
            'fairplay_method3_behavior_charts.py','fairplay_method3_frequency_charts.py')]
        for source in sources:
            self.assertNotIn('magnuscarlsen',source.casefold())
            self.assertNotIn('vladimirkramnik',source.casefold())
            self.assertNotIn('hikaru',source.casefold())
        # Production entry still exposes the externally configurable engine-free
        # Method 3 path; neither timing nor openings become score inputs.
        self.assertIn("'engine_used':False",sources[0])
        self.assertIn("'timing_scoring':False",sources[0])


if __name__ == '__main__':
    unittest.main()
