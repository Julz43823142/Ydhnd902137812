"""Synthetic Game Review progress, truthfulness, and Discord provenance regressions.

No workflow is dispatched; review input is synthetic.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import bot
from game_review_progress import ReviewProgress, duration_label
from game_review_service import GameReviewService
import chess_play
import game_review_remote as remote
from test_game_review_remote import ENV, REVISION, TICKET, FakeStore, review_fixture


class ProgressFormattingTests(unittest.TestCase):
    def test_no_eta_before_observed_compute_and_real_percentage(self):
        tick = [0.0]
        progress = ReviewProgress(80, clock=lambda: tick[0])
        self.assertIn("**0%** (0/80 plies)", progress.render("Synthetic vs Stockfish"))
        self.assertIn("Calculating from actual progress", progress.render("Synthetic"))
        tick[0] = 100.0
        progress.update({"stage": "remote-dispatched", "completed": 0, "total": 80})
        self.assertIn("Waiting for 14 GitHub Actions workers", progress.render("Synthetic"))
        self.assertIn("**0%** (0/80 plies)", progress.render("Synthetic"))
        tick[0] = 120.0
        progress.update({"stage": "remote-analysis", "completed": 20, "total": 80})
        self.assertIn("**25%** (20/80 plies)", progress.render("Synthetic"))
        self.assertIn("Calculating from actual progress", progress.render("Synthetic"))
        tick[0] = 140.0
        self.assertIn("~1m 00s (estimate)", progress.render("Synthetic"))
        self.assertIn("**Elapsed:** 2m 20s", progress.render("Synthetic"))

    def test_fallback_resets_progress_and_never_forges_completion(self):
        tick = [0.0]
        progress = ReviewProgress(100, clock=lambda: tick[0])
        progress.update({"stage": "remote-analysis", "completed": 45, "total": 100})
        progress.update({"stage": "remote-fallback", "completed": 0})
        self.assertIn("**0%** (0/100 plies)", progress.render("Synthetic"))
        progress.update({"stage": "local-analysis", "completed": 7})
        self.assertIn("**7%** (7/100 plies)", progress.render("Synthetic"))

    def test_invalid_reports_are_ignored(self):
        progress = ReviewProgress(50, clock=lambda: 0.0)
        progress.update({"stage": "remote-analysis", "completed": 10})
        progress.update({"stage": "remote-analysis", "completed": 999999})
        progress.update({"stage": "remote-analysis", "completed": -1})
        progress.update({"stage": "remote-analysis", "completed": True})
        self.assertIn("**20%** (10/50 plies)", progress.render("Synthetic"))
        self.assertEqual(duration_label(125), "2m 05s")


class MetadataTests(unittest.TestCase):
    def test_disclaimer_version_route_and_actual_seconds(self):
        analysis = {
            "analysis_version": "ordinary-review-v8",
            "analysis_depth": 18,
            "review_compute": "local-fallback", "review_total_elapsed_seconds": 195,
            "review_remote_seconds": 100, "review_local_seconds": 95,
        }
        embed = bot._review_details_embed(analysis)
        self.assertIn("not official Chess.com ratings", embed.description)
        self.assertIn("ordinary-review-v8", embed.description)
        self.assertIn("3m 15s", embed.description)
        self.assertIn("unsuccessful remote attempt", embed.description)
        self.assertIn("1m 40s", embed.description)
        analysis.update({"review_compute": "github-actions-idle-fairplay-capacity",
                         "review_workers": 2, "review_remote_seconds": 160,
                         "review_local_seconds": 0})
        self.assertIn("remote (2 workers)", bot._review_details_embed(analysis).description)

    def test_cache_hit_reports_cache_not_new_runner_speed(self):
        def fake(*args):
            return {"moves": [{"played": "e4"}], "analysis_version": "synthetic"}
        service = GameReviewService(fake)
        service.analyse(["e4"])
        reports = []
        cached = service.analyse(["e4"], progress_callback=reports.append)
        self.assertTrue(cached["review_cache_hit"])
        self.assertEqual(reports[-1]["stage"], "cache")
        self.assertGreaterEqual(cached["review_total_elapsed_seconds"], 0)
        self.assertIn("Cached result", bot._review_details_embed(cached).description)

    def test_live_local_engine_emits_verified_per_ply_counts(self):
        from test_game_review import ReviewEngine
        moves = ["e4", "e5", "Nf3", "Nc6"]
        reports = []
        with patch.object(chess_play, "_get_analysis_engine", return_value=ReviewEngine(moves)):
            answer = chess_play.analyse_game_moves(moves, progress_callback=reports.append)
        self.assertEqual(answer["analysed_plies"], 4)
        self.assertEqual([p["completed"] for p in reports], list(range(5)))
        self.assertTrue(all(p["stage"] == "local-analysis" for p in reports))

    def test_worker_progress_is_encrypted_and_bounded(self):
        store = FakeStore()
        sections, _ = remote._segments(
            __import__("test_game_review_remote").MOVES, None, False, None)
        store.owner = TICKET
        store.put(remote._name("req", TICKET), {
            "schema": remote.SCHEMA, "ticket": TICKET,
            "revision": REVISION, "engine": "Stockfish 19",
            "engine_source": chess_play.STOCKFISH_SOURCE_REVISION,
            "analysis_version": remote.ENGINE_VERSION,
            "depth": chess_play.STOCKFISH_GAME_REVIEW_DEPTH,
            "threads": remote.REMOTE_WORKER_THREADS,
            "hash_mb": chess_play.STOCKFISH_HASH_MB,
            "created": __import__("time").time(), "sections": sections,
        })
        clock = [0.0]
        def worker(moves, limit, fen, chess960, *, interrupt_check):
            for _ in range(5):
                clock[0] += 8
                interrupt_check()
            return review_fixture(sections[0])
        with (patch.object(remote, "_git", return_value=SimpleNamespace(
                returncode=0, stdout=(REVISION + "\n").encode())),
              patch.object(chess_play, "STOCKFISH_THREADS", remote.REMOTE_WORKER_THREADS)):
            remote.compute_worker(
                TICKET, 0, env={**ENV, "INPUT_REVISION": REVISION},
                store=store, idle=lambda *args: True, analyser=worker,
                clock=lambda: clock[0],
            )
        status = store.payloads[remote._name("progress", TICKET, 0)]
        self.assertEqual(status["state"], "running")
        self.assertGreater(status["completed"], 0)
        self.assertNotIn("fen", str(status).lower())
        self.assertNotIn("e4", str(status))

    def test_worker_checkout_includes_theory_data(self):
        workflow = (Path(__file__).parent / ".github/workflows/ordinary_review_distributed.yml").read_text()
        self.assertIn("/assets/fairplay/opening-lines.json", workflow)


class AsyncProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_notifies_without_blocking_review(self):
        progress = ReviewProgress(4)
        status = SimpleNamespace(edit=AsyncMock())
        def compute(*args, progress_callback=None):
            progress_callback({"stage": "local-analysis", "completed": 4, "total": 4})
            return {"moves": [], "analysis_version": "synthetic"}
        with patch.object(bot.game_review_service, "analyse", side_effect=compute):
            result = await bot._analyse_game_review_with_progress(
                status, progress, "Synthetic match",
                ["e4", "e5", "Nf3", "Nc6"], None, False,
            )
        self.assertEqual(result["analysis_version"], "synthetic")
        self.assertIn("**100%** (4/4 plies)", progress.render("Synthetic match"))


class WorkerShutdownTests(unittest.TestCase):
    def test_worker_closes_engine_after_success(self):
        from scripts import ordinary_review_compute_worker as entry
        with (patch.dict("os.environ", {"INPUT_TICKET": TICKET, "INPUT_SHARD": "0"}),
              patch.object(entry, "compute_worker", return_value=53),
              patch.object(entry.chess_play, "_close_analysis_engine") as close):
            entry.main()
        close.assert_called_once_with()

    def test_worker_closes_engine_after_failure(self):
        from scripts import ordinary_review_compute_worker as entry
        with (patch.dict("os.environ", {"INPUT_TICKET": TICKET, "INPUT_SHARD": "1"}),
              patch.object(entry, "compute_worker", side_effect=RuntimeError("private error")),
              patch.object(entry.chess_play, "_close_analysis_engine") as close):
            with self.assertRaisesRegex(SystemExit, "failed closed"):
                entry.main()
        close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
