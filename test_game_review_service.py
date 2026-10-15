"""Independent cache, concurrency, engine-metadata and source-clock regressions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, Lock
from types import SimpleNamespace
import time
import unittest

import chess
import bot
import chess_play
from game_review_service import GameReviewService, review_cache_key
from review_pgn_exports import PgnExportMode


class ReviewCacheTests(unittest.TestCase):
    def test_cache_hit_retains_independent_results_and_defends_mutation(self):
        calls = []
        def fake(moves, max_plies, fen, chess960):
            calls.append(tuple(moves))
            return {"moves": [{"classification": "best"}], "white": {"accuracy": 100}}

        service = GameReviewService(fake)
        first = service.analyse(["e4"])
        first["moves"][0]["classification"] = "blunder"
        second = service.analyse(["e4"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(second["moves"][0]["classification"], "best")
        self.assertEqual(service.stats()["hits"], 1)

    def test_failed_analysis_is_not_cached_and_retry_recovers(self):
        calls = []
        def flaky(*args):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("synthetic Stockfish unavailable")
            return {"moves": []}
        service = GameReviewService(flaky)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            service.analyse(["e4"])
        self.assertEqual(service.stats()["in_flight"], 0)
        self.assertEqual(service.analyse(["e4"]), {"moves": []})
        self.assertEqual(len(calls), 2)

    def test_parallel_identical_requests_share_one_engine_search(self):
        entered, release = Event(), Event()
        calls = []
        lock = Lock()
        def blocking(*args):
            with lock:
                calls.append(1)
            entered.set()
            self.assertTrue(release.wait(3))
            return {"moves": [{"classification": "best"}]}
        service = GameReviewService(blocking)
        with ThreadPoolExecutor(max_workers=2) as executor:
            one = executor.submit(service.analyse, ["e4", "e5"])
            self.assertTrue(entered.wait(3))
            two = executor.submit(service.analyse, ["e4", "e5"])
            deadline = time.monotonic() + 3
            while service.stats()["hits"] < 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            release.set()
            self.assertEqual(one.result(timeout=5), two.result(timeout=5))
        self.assertEqual(len(calls), 1)

    def test_cache_is_bounded_and_expiry_recomputes(self):
        now = [0.0]
        calls = []
        def fake(*args):
            calls.append(1)
            return {"moves": []}
        service = GameReviewService(fake, max_entries=1, ttl_seconds=10, clock=lambda: now[0])
        service.analyse(["e4"])
        service.analyse(["d4"])
        self.assertEqual(service.stats()["cached"], 1)
        service.analyse(["e4"])
        self.assertEqual(len(calls), 3)
        now[0] = 12
        service.analyse(["e4"])
        self.assertEqual(len(calls), 4)

    def test_distinct_start_position_config_and_moves_do_not_collide(self):
        base = review_cache_key(["e4", "e5"])
        self.assertNotEqual(base, review_cache_key(["d4", "d5"]))
        self.assertNotEqual(base, review_cache_key(["e4", "e5"], max_plies=1))
        self.assertNotEqual(base, review_cache_key(["e4", "e5"], chess960=True))
        self.assertNotEqual(
            review_cache_key(["Kd7"], start_fen="4k3/4p3/8/8/8/8/4P3/4K3 b - - 0 37"),
            review_cache_key(["Ke7"], start_fen="4k3/4p3/8/8/8/8/4P3/4K3 b - - 0 37"),
        )

    def test_same_move_text_with_different_clock_comments_reuses_analysis(self):
        source_a = '[Result "*"]\\n\\n1. e4 {[%clk 0:03:00]} e5 *'
        source_b = '[Result "*"]\\n\\n1. e4 {[%clk 0:02:54]} e5 *'
        owner = SimpleNamespace(id=999, display_name="Synthetic")
        first_game, first_moves = bot._parse_review_pgn(source_a, owner)
        second_game, second_moves = bot._parse_review_pgn(source_b, owner)
        calls = []
        service = GameReviewService(lambda *args: (calls.append(1) or
            {"moves": [{"classification": "best"}, {"classification": "best"}]}))
        review_a = service.analyse(first_moves)
        review_b = service.analyse(second_moves)
        self.assertEqual(len(calls), 1)
        a, _ = bot._make_review_pgn_export(first_game, review_a, PgnExportMode.CLOCKS_AND_ANALYSIS)
        b, _ = bot._make_review_pgn_export(second_game, review_b, PgnExportMode.CLOCKS_AND_ANALYSIS)
        self.assertIn("[%clk 0:03:00]", a)
        self.assertIn("[%clk 0:02:54]", b)

    def test_untrusted_nag_does_not_change_independent_review_result(self):
        a = '[Result "*"]\\n\\n1. e4 $4 {bad move} e5 *'
        b = '[Result "*"]\\n\\n1. e4 $3 {brilliant!} e5 *'
        owner = SimpleNamespace(id=999, display_name="Synthetic")
        game_a, moves_a = bot._parse_review_pgn(a, owner)
        game_b, moves_b = bot._parse_review_pgn(b, owner)
        calls = []
        service = GameReviewService(lambda *args: (calls.append(1) or {"moves": [{"classification": "best"}]}))
        self.assertEqual(service.analyse(moves_a), service.analyse(moves_b))
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(game_a["source_pgn"], game_b["source_pgn"])


class ReviewMateProbability(unittest.TestCase):
    def test_terminal_and_mate_probabilities_are_finite_and_symmetric(self):
        self.assertEqual(chess_play._win_percent_from_cp(100000), 100.0)
        self.assertEqual(chess_play._win_percent_from_cp(-100000), 0.0)
        self.assertEqual(chess_play._win_percent_from_cp(0), 50.0)
        self.assertAlmostEqual(
            chess_play._win_percent_from_cp(250) +
            chess_play._win_percent_from_cp(-250), 100, places=6,
        )

    def test_reported_accuracy_is_not_a_chesscom_official_score(self):
        self.assertGreater(chess_play._move_accuracy_from_win_loss(0), 99.9)
        self.assertLess(chess_play._move_accuracy_from_win_loss(50), 15)


if __name__ == "__main__":
    unittest.main()
