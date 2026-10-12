"""Regressions for per-phase timeouts, progress and English text."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import chess.engine

from fairplay_config import CONFIG
from fairplay_analysis import scan_engine_timeout
from fairplay_progress import LiveTiming, ReliableTiming, duration, estimate


class ScanTimingTests(unittest.TestCase):
    def test_scan_timing_detail_shows_measured_non_additive_stages(self):
        from fairplay_ui import ReportView, detail_embed
        result=SimpleNamespace(username='synthetic-player',diagnostics={'runtime':{
            'elapsed_seconds':300,'collection_seconds':20,'fast_seconds':80,
            'maia_inference_seconds':30,'fast_maia_alternatives_seconds':35,
            'remote_compute_elapsed_seconds':90,'remote_join_seconds':70,
            'local_deep_stockfish_seconds':0,
            'deep_maia_alternatives_seconds':40,'scoring_postprocess_seconds':10,
            'engine_searches':{'fast_multipv_seconds':120}}})
        card=detail_embed(result,'Scan Timing').to_dict()
        content=str(card)
        self.assertIn('Total scan',content)
        self.assertIn('stage times overlap',content)
        self.assertIn('Fast Stockfish and candidate review',content)
        self.assertTrue(any(button.label=='Scan Timing' for button in ReportView().children))

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

    def test_error_card_has_no_false_eighty_percent_completion(self):
        from fairplay_ui import progress_embed
        failed=progress_embed('Synthetic','❌ Stockfish failed',80,LiveTiming())
        self.assertIn('Review stopped — no result.',failed.description)
        self.assertNotIn('**80%**',failed.description)
        self.assertNotIn('▰',failed.description)

    def test_duration_english(self):
        self.assertEqual(duration(44*60+16),'44m 16s')
        self.assertEqual(duration(3600+5*60),'1h 05m')

    def test_remote_eta_includes_dispatch_and_adapts_to_slowest_shards(self):
        clock=ReliableTiming(started=0)
        clock.observe('Fast engine scan: 0 / 100 positions',now=0)
        clock.observe('Fast engine scan: 50 / 100 positions',now=100)
        clock.observe('Remote Stockfish dispatched…',now=120)
        clock.observe('Depth-18 rapid/blitz · depth-12 bullet: 180 / 1000 positions',now=180)
        first=clock.rate['deep']
        self.assertAlmostEqual(first,60/180)
        clock.observe('Depth-18 rapid/blitz · depth-12 bullet: 360 / 1000 positions',now=210)
        self.assertLess(clock.rate['deep'],first)
        clock.observe('Depth-18 rapid/blitz · depth-12 bullet: 362 / 1000 positions',now=290)
        self.assertIn('no longer reliable',clock.summary('Depth-18 rapid/blitz',now=351))

    def test_restored_eta_accepts_new_worker_progress(self):
        clock=ReliableTiming()
        clock.observe('Depth-18 rapid/blitz · depth-12 bullet: 10 / 100 positions')
        restored=ReliableTiming.from_checkpoint(clock.snapshot())
        now=restored.phases['deep']['last']+30
        restored.observe('Depth-18 rapid/blitz · depth-12 bullet: 30 / 100 positions',now=now)
        self.assertEqual(restored.phases['deep']['done'],30)

    def test_local_recovery_discards_remote_eta(self):
        clock=ReliableTiming(started=0)
        clock.observe('Fast engine scan: 0 / 100 positions',now=0)
        clock.observe('Fast engine scan: 50 / 100 positions',now=100)
        clock.observe('Remote Stockfish dispatched…',now=120)
        clock.observe('Depth-18 rapid/blitz · depth-12 bullet: 180 / 1000 positions',now=180)
        self.assertIn('deep',clock.rate)
        clock.observe('Recovering missing Stockfish games locally…',now=200)
        self.assertNotIn('deep',clock.rate)
        self.assertIn('Re-estimating',clock.summary('Recovering missing Stockfish games locally…',now=201))
        clock.observe('Depth-18 rapid/blitz · depth-12 bullet: 0 / 100 positions',now=210)
        clock.observe('Depth-18 rapid/blitz · depth-12 bullet: 20 / 100 positions',now=240)
        self.assertAlmostEqual(clock.rate['deep'],30/20)


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
