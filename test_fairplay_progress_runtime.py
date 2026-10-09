"""Regressions for per-phase timeouts, progress and English text."""
import unittest
from unittest.mock import patch

import chess.engine

from fairplay_config import CONFIG
from fairplay_analysis import scan_engine_timeout
from fairplay_progress import LiveTiming, duration, estimate


class ScanTimingTests(unittest.TestCase):
    def test_english_elapsed_and_phase_eta(self):
        clock=LiveTiming(started=0.0)
        clock.observe('Fast engine scan: 0 / 100 positions',now=0.0)
        clock.observe('Fast engine scan: 50 / 100 positions',now=100.0)
        text=clock.summary('Fast engine scan: 50 / 100 positions',now=100.0)
        self.assertIn('Elapsed: **1m 40s**',text)
        self.assertIn('Estimated time left in fast analysis: **~1m 40s**',text)
        self.assertNotIn('Bezig:',text)

    def test_no_unreliable_total_eta(self):
        clock=LiveTiming(started=0.0)
        self.assertIn('not yet reliably estimated',clock.summary('Collecting rated games…',now=5.0))

    def test_error_clears_eta(self):
        clock=LiveTiming(started=0.0)
        clock.observe('Deep confirmation: 0 / 100 positions',now=0.0)
        clock.observe('Deep confirmation: 30 / 100 positions',now=100.0)
        clock.observe('❌ Stockfish search timed out',now=101.0)
        text=clock.summary('❌ Stockfish search timed out',now=101.0)
        self.assertIn('Review stopped; no result was issued.',text)
        self.assertNotIn('Estimated time left',text)

    def test_depth18_position_progress_not_stuck_at_eighty(self):
        stage='Depth-18 rapid/blitz · depth-12 bullet: {} / 100 positions'
        self.assertEqual(estimate(stage.format(0)),80)
        self.assertEqual(estimate(stage.format(50)),88)
        self.assertEqual(estimate(stage.format(100)),97)
        self.assertEqual(estimate('Complete'),100)

    def test_duration_english(self):
        self.assertEqual(duration(44*60+16),'44m 16s')
        self.assertEqual(duration(3600+5*60),'1h 05m')


class EngineSearchTimeoutTests(unittest.TestCase):
    def test_search_modes_have_distinct_default_limits(self):
        self.assertEqual(scan_engine_timeout(chess.engine.Limit(nodes=CONFIG.fast_nodes)),30)
        self.assertEqual(scan_engine_timeout(chess.engine.Limit(nodes=CONFIG.deep_nodes)),180)
        self.assertEqual(scan_engine_timeout(chess.engine.Limit(depth=18)),1200)

    def test_overrides_clamped(self):
        with patch.dict('os.environ',{'FAIRPLAY_DEEP_ENGINE_TIMEOUT_SECONDS':'0'}):
            self.assertEqual(scan_engine_timeout(chess.engine.Limit(nodes=CONFIG.deep_nodes)),30)
        with patch.dict('os.environ',{'FAIRPLAY_DEPTH18_ENGINE_TIMEOUT_SECONDS':'999999'}):
            self.assertEqual(scan_engine_timeout(chess.engine.Limit(depth=18)),7200)


if __name__=='__main__':
    unittest.main()
