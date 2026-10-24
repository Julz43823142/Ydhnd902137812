"""Remote ordinary review: CAS reservation, priority, integrity, safe fallback.

Every example is synthetic; never schedules a live GitHub Actions workflow.
"""
import hashlib
import hmac
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import chess
import chess_play
import game_review_remote as remote
from game_review_service import GameReviewService


SECRET = "synthetic-game-review-encryption-key"
REVISION = "a" * 40
TICKET = "b" * 24
ENV = {
    "FAIRPLAY_DISTRIBUTED": "1", "GITHUB_REPOSITORY": "owner/repo",
    "GITHUB_SHA": REVISION, "GITHUB_TOKEN": "synthetic-token",
    "FAIRPLAY_DISTRIBUTED_KEY": SECRET,
}
MOVES = ("e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 "
         "c3 O-O h3 Nb8 d4 Nbd7").split()


def review_fixture(section, *, root_baseline=50):
    board = chess.Board(section["fen"], chess960=section["chess960"])
    rows = []
    for ply, san in enumerate(section["moves"], 1):
        color = board.turn
        move = board.parse_san(san)
        best = board.san(move)
        board.push(move)
        rows.append({
            "ply": ply, "played": best, "best": best,
            "move": f"{board.fullmove_number}.{best}", "fen": board.fen(),
            "side": "white" if color else "black", "mover_color": color,
            "classification": "best", "category": "ok", "is_best": True,
            "win_loss_pct": 0, "move_accuracy": 100, "loss_cp": 0,
            "eval_white_cp": 0, "eval_white_mate": None,
            "sacrifice_cp": 0, "comment": "Synthetic engine best.",
        })
    return {
        "moves": rows, "engine": "Stockfish 19", "analysis_depth": 18,
        "analysis_version": remote.ENGINE_VERSION, "analysed_plies": len(rows),
        "skipped_plies": 0, "requested_plies": len(rows),
        "initial_white_win_pct": root_baseline,
        "engine_configuration": {
            "threads": remote.REMOTE_WORKER_THREADS,
            "hash_mb": chess_play.STOCKFISH_HASH_MB,
            "depth": chess_play.STOCKFISH_GAME_REVIEW_DEPTH,
        },
        "accuracy_provenance": "SharkBot estimate (not Chess.com Accuracy)",
        "accuracy_model": "synthetic", "classification_model": "synthetic",
        "brilliant_model": "synthetic", "analysis_time_per_position": None,
    }


class FakeStore:
    def __init__(self):
        self.payloads = {}
        self.owner = None
        self.released = []

    def claim(self, ticket):
        if self.owner is not None:
            return False
        self.owner = ticket
        return True

    def owns(self, ticket, **kwargs):
        return self.owner == ticket

    def release(self, ticket):
        self.released.append(ticket)
        if self.owner == ticket:
            self.owner = None
            return True
        return False

    def put(self, name, data):
        self.payloads[name] = data
        return True

    def read_many(self, names):
        return {name: self.payloads[name] for name in names if name in self.payloads}


class TransportAndPriorityTests(unittest.TestCase):
    def test_isolated_crypto_never_decrypts_fairplay_ciphertext(self):
        from fairplay_distributed import pack, unpack
        encoded = pack({"user": "SYNTHETIC-private-PGN"}, SECRET)
        isolated = remote._derive(SECRET)
        self.assertNotEqual(SECRET, isolated)
        with self.assertRaises(Exception):
            unpack(encoded, isolated)
        own = pack({"moves": ["e4", "e5"]}, isolated)
        with self.assertRaises(Exception):
            unpack(own, SECRET)

    def test_fairplay_queued_running_and_unknown_fail_closed(self):
        for status in ("queued", "in_progress", "waiting", "pending"):
            http = Mock()
            http.get.return_value.status_code = 200
            http.get.return_value.json.return_value = {"workflow_runs": [{"status": status}]}
            self.assertFalse(remote.fairplay_idle("owner/repo", "token", http=http))
        http = Mock()
        http.get.return_value.status_code = 200
        http.get.return_value.json.return_value = {"workflow_runs": [
            {"status": "completed"}, {"status": "completed"}
        ]}
        self.assertTrue(remote.fairplay_idle("owner/repo", "token", http=http))
        http.get.return_value.status_code = 503
        self.assertFalse(remote.fairplay_idle("owner/repo", "token", http=http))
        self.assertFalse(remote.fairplay_idle("evil/repo/invalid", "token", http=http))

    def test_dispatch_never_includes_pgn_or_chess_positions(self):
        http = Mock()
        http.post.return_value.status_code = 204
        remote._dispatch(TICKET, REVISION, "owner/repo", "token", http=http)
        request = http.post.call_args.kwargs
        self.assertEqual(request["json"], {
            "ref": "main", "inputs": {"ticket": TICKET, "revision": REVISION},
        })
        self.assertNotIn("moves", str(request))
        self.assertNotIn("e4", str(request))

    def test_ordinary_and_fairplay_use_distinct_git_branches_and_names(self):
        from fairplay_distributed import BRANCH
        self.assertNotEqual(remote.BRANCH, BRANCH)
        self.assertTrue(remote.FILENAMES.fullmatch("lease_review.enc"))
        self.assertFalse(remote.FILENAMES.fullmatch("req_game123.enc"))
        self.assertTrue(remote.FILENAMES.fullmatch(remote._name("res", TICKET, 13)))


class SparseEncryptedPollingTests(unittest.TestCase):
    def test_only_existing_encrypted_files_are_opened(self):
        store = remote.ReviewStore(SECRET)
        files = [remote._name("res", TICKET, i) for i in range(remote.MAX_WORKERS)]
        files += [remote._name("progress", TICKET, i)
                  for i in range(remote.MAX_WORKERS)]
        head = "d" * 40
        present = files[3]
        with (patch.object(store, "_head", return_value=head) as get_head,
              patch.object(store, "_snapshot", return_value={present: "e" * 40}) as snapshot,
              patch.object(store, "_read_at", return_value={"completed": 3}) as open_blob):
            self.assertEqual(store.read_many(files), {present: {"completed": 3}})
        get_head.assert_called_once_with()
        snapshot.assert_called_once_with(head)
        open_blob.assert_called_once_with(head, present)

    def test_invalid_packet_name_is_rejected_before_git_access(self):
        store = remote.ReviewStore(SECRET)
        with patch.object(store, "_head") as get_head:
            with self.assertRaises(Exception):
                store.read_many(["../../other-review.enc"])
            get_head.assert_not_called()

    def test_missing_branch_requires_no_blob_reads(self):
        store = remote.ReviewStore(SECRET)
        with (patch.object(store, "_head", return_value=None),
              patch.object(store, "_snapshot") as snapshot):
            self.assertEqual(store.read_many([remote._name("res", TICKET, 0)]), {})
            snapshot.assert_not_called()


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.sections, self.count = remote._segments(MOVES, None, False, None)

    def test_segments_cover_all_plies_and_keep_original_fens(self):
        self.assertEqual(sum(len(s["moves"]) for s in self.sections), len(MOVES))
        self.assertEqual(self.sections[0]["offset"], 0)
        self.assertEqual(self.sections[1]["offset"], len(self.sections[0]["moves"]))
        self.assertEqual(len(self.sections), remote.MAX_WORKERS)
        out = remote._combine(self.sections, [review_fixture(s) for s in self.sections],
                              self.count, elapsed=2.2)
        self.assertEqual(len(out["moves"]), len(MOVES))
        self.assertEqual([row["ply"] for row in out["moves"]], list(range(1, len(MOVES) + 1)))
        self.assertEqual(out["white"]["accuracy"], 100)
        self.assertEqual(out["black"]["accuracy"], 100)
        self.assertEqual(out["white"]["blunders"], 0)
        self.assertEqual(out["skipped_plies"], 0)
        self.assertEqual(out["review_workers"], remote.MAX_WORKERS)
        self.assertEqual(out["analysis_depth"], 18)

    def test_complexity_balancing_covers_every_position_exactly_once(self):
        from scripts.benchmark_review_hash import TACTICAL
        sections, total = remote._segments(TACTICAL, None, False, None)
        self.assertEqual(total, len(TACTICAL))
        self.assertEqual(len(sections), 14)
        self.assertEqual([s["index"] for s in sections], list(range(14)))
        self.assertTrue(all(s["moves"] for s in sections))
        self.assertEqual([san for s in sections for san in s["moves"]], TACTICAL)
        self.assertEqual([s["offset"] for s in sections],
                         [sum(len(p["moves"]) for p in sections[:i])
                          for i in range(len(sections))])
        # Previously every worker received 5-6 consecutive plies. The
        # measured legal-complexity planner deliberately creates uneven
        # sections while the verified FEN chain stays contiguous.
        self.assertEqual(len(sections[0]["moves"]), 8)
        board = chess.Board()
        for section in sections:
            self.assertEqual(section["fen"], board.fen(en_passant="fen"))
            for san in section["moves"]:
                board.push_san(san)

    def test_balancer_preserves_minimum_one_ply_per_runner(self):
        opening = MOVES[:remote.MAX_WORKERS]
        cuts = remote._balanced_cuts(opening, chess.Board())
        self.assertEqual(cuts, list(range(remote.MAX_WORKERS + 1)))
        with self.assertRaises(remote.RemoteReviewError):
            remote._balanced_cuts(opening[:-1], chess.Board())

    def test_bad_fen_label_and_missing_moves_rejected(self):
        for field, value in (("fen", chess.STARTING_FEN), ("classification", "brilliant cheating"),
                             ("eval_white_cp", float("nan")), ("win_loss_pct", 500)):
            with self.subTest(field=field):
                outputs = [review_fixture(s) for s in self.sections]
                outputs[0]["moves"][0][field] = value
                with self.assertRaises(remote.RemoteReviewError):
                    remote._combine(self.sections, outputs, self.count, elapsed=1)
        output = review_fixture(self.sections[0])
        output["moves"].pop()
        with self.assertRaises(remote.RemoteReviewError):
            remote._validate_segment(self.sections[0], output)

    def test_nonstandard_start_and_black_to_move(self):
        fen = "4k3/4p3/8/8/8/8/4P3/4K3 b - - 0 37"
        sections, count = remote._segments(["Kd7", "Kd2", "Ke6", "Ke3"] * 4, fen, False, None)
        result = remote._combine(sections, [review_fixture(s) for s in sections],
                                 count, elapsed=3)
        self.assertEqual(result["moves"][0]["side"], "black")
        self.assertEqual(result["moves"][0]["ply"], 1)
        self.assertEqual(result["white"]["accuracy"], 100)
        self.assertEqual(result["black"]["accuracy"], 100)


class CoordinatorTests(unittest.TestCase):
    def test_under_threshold_no_dispatch_and_no_lease(self):
        store = FakeStore()
        dispatch = Mock()
        result = remote.try_remote_review(MOVES, env=ENV, store=store,
                                          idle=lambda *args: True, dispatch=dispatch)
        self.assertIsNone(result)
        dispatch.assert_not_called()
        self.assertIsNone(store.owner)

    def test_when_fairplay_active_returns_none_without_lease(self):
        store = FakeStore()
        long_moves = MOVES * 5
        result = remote.try_remote_review(long_moves, env=ENV, store=store,
                                          idle=lambda *args: False, dispatch=Mock())
        self.assertIsNone(result)
        self.assertIsNone(store.owner)

    def test_bad_worker_response_falls_back_and_releases_lease(self):
        store = FakeStore()
        # Repeated opening cannot be validated; return early via mocked sections.
        sections, count = remote._segments(MOVES, None, False, None)
        with patch.object(remote, "_segments", return_value=(sections, count)):
            time_now = [0.0]
            def advance(seconds):
                time_now[0] += seconds
                if time_now[0] > 0:
                    ticket = store.owner
                    store.put(remote._name("res", ticket, 0), {
                        "schema": remote.SCHEMA, "ticket": ticket,
                        "revision": REVISION, "index": 0,
                        "result": {"moves": []},
                    })
            result = remote.try_remote_review(
                MOVES * 5, env=ENV, store=store, idle=lambda *args: True,
                dispatch=Mock(), clock=lambda: time_now[0], sleep=advance,
            )
        self.assertIsNone(result)
        self.assertIsNone(store.owner)
        self.assertEqual(len(store.released), 1)

    def test_verified_shards_return_full_game(self):
        store = FakeStore()
        sections, total = remote._segments(MOVES, None, False, None)
        with patch.object(remote, "_segments", return_value=(sections, total)):
            tick = [0.0]
            def supply(seconds):
                tick[0] += seconds
                ticket = store.owner
                for section in sections:
                    index = section["index"]
                    data = review_fixture(section)
                    store.put(remote._name("res", ticket, index), {
                        "schema": remote.SCHEMA, "ticket": ticket,
                        "revision": REVISION, "index": index,
                        "section_sha256": remote._result_digest(section),
                        "result_sha256": remote._result_digest(data),
                        "result": data,
                    })
            result = remote.try_remote_review(
                MOVES * 5, env=ENV, store=store, idle=lambda *args: True,
                dispatch=Mock(), clock=lambda: tick[0], sleep=supply)
        self.assertEqual(result["analysed_plies"], len(MOVES))
        self.assertEqual(result["white"]["accuracy"], 100)
        self.assertIsNone(store.owner)

    def test_failed_dispatch_is_local_fallback(self):
        store = FakeStore()
        with patch.object(remote, "_segments", return_value=(
                remote._segments(MOVES, None, False, None))):
            answer = remote.try_remote_review(
                MOVES * 5, env=ENV, store=store, idle=lambda *args: True,
                dispatch=Mock(side_effect=remote.RemoteReviewError("synthetic failure")))
        self.assertIsNone(answer)
        self.assertIsNone(store.owner)


class AtomicCapacityTests(unittest.TestCase):
    def test_real_git_lease_is_atomic_and_owner_scoped(self):
        from fairplay_distributed import EncryptedGitStore
        with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP", "/tmp")) as temp:
            remote_repo = Path(temp) / "remote.git"
            working = Path(temp) / "working"
            subprocess.run(["git", "init", "--bare", "-q", str(remote_repo)], check=True)
            subprocess.run(["git", "init", "-q", str(working)], check=True)
            subprocess.run(["git", "-C", str(working), "remote", "add", "origin",
                            str(remote_repo)], check=True)
            def isolated_git(args, data=None):
                env = os.environ.copy()
                env.setdefault("GIT_AUTHOR_NAME", "Synthetic ordinary review")
                env.setdefault("GIT_AUTHOR_EMAIL", "synthetic@example.invalid")
                env.setdefault("GIT_COMMITTER_NAME", "Synthetic ordinary review")
                env.setdefault("GIT_COMMITTER_EMAIL", "synthetic@example.invalid")
                return subprocess.run(["git", "-C", str(working), *args],
                                      input=data, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, check=False,
                                      timeout=25, env=env)
            with (patch("fairplay_distributed._git", side_effect=isolated_git),
                  patch("game_review_remote._git", side_effect=isolated_git)):
                first = remote.ReviewStore(SECRET)
                second = remote.ReviewStore(SECRET)
                first_ticket, second_ticket = "c" * 24, "d" * 24
                self.assertTrue(first.claim(first_ticket))
                self.assertFalse(second.claim(second_ticket))
                first.put(remote._name("req", first_ticket), {"private": "secret-PGN"})
                self.assertTrue(first.owns(first_ticket))
                self.assertFalse(second.release(second_ticket))
                self.assertTrue(first.release(first_ticket))
                self.assertTrue(second.claim(second_ticket))
                self.assertFalse(first.release(first_ticket))
                self.assertTrue(second.owns(second_ticket))
                self.assertTrue(second.release(second_ticket))
                # Raw ref must not disclose moves or personal game data.
                show = isolated_git(["show", "refs/remotes/origin/" + remote.BRANCH
                                     + ":lease_review.enc"])
                self.assertNotIn(b"secret-PGN", show.stdout)


class WorkerProtocolTests(unittest.TestCase):
    def _prepared(self):
        store = FakeStore()
        sections, _ = remote._segments(MOVES, None, False, None)
        store.owner = TICKET
        store.put(remote._name("req", TICKET), {
            "schema": remote.SCHEMA, "ticket": TICKET,
            "revision": REVISION, "engine": "Stockfish 19",
            "engine_source": chess_play.STOCKFISH_SOURCE_REVISION,
            "analysis_version": remote.ENGINE_VERSION,
            "depth": chess_play.STOCKFISH_GAME_REVIEW_DEPTH,
            "threads": remote.REMOTE_WORKER_THREADS,
            "hash_mb": chess_play.STOCKFISH_HASH_MB,
            "created": time.time(), "sections": sections,
        })
        return store, sections

    def test_worker_publishes_only_authenticated_complete_partition(self):
        store, sections = self._prepared()
        env = {**ENV, "INPUT_REVISION": REVISION}
        fake_git = SimpleNamespace(returncode=0, stdout=(REVISION + "\n").encode())
        def fake_engine(moves, limit, fen, chess960, *, interrupt_check):
            interrupt_check()
            return review_fixture(sections[0])
        with (patch.object(remote, "_git", return_value=fake_git),
              patch.object(chess_play, "STOCKFISH_THREADS", remote.REMOTE_WORKER_THREADS)):
            self.assertEqual(remote.compute_worker(
                TICKET, 0, env=env, store=store,
                idle=lambda *args: True, analyser=fake_engine
            ), len(sections[0]["moves"]))
        packet = store.payloads[remote._name("res", TICKET, 0)]
        self.assertEqual(packet["section_sha256"], remote._result_digest(sections[0]))
        self.assertEqual(packet["result_sha256"], remote._result_digest(packet["result"]))
        self.assertNotIn("username", str(packet))
        # Short shards never publish a redundant zero-progress git commit.
        self.assertNotIn(remote._name("progress", TICKET, 0), store.payloads)

    def test_worker_preempts_when_fairplay_becomes_active(self):
        store, sections = self._prepared()
        env = {**ENV, "INPUT_REVISION": REVISION}
        fake_git = SimpleNamespace(returncode=0, stdout=(REVISION + "\n").encode())
        calls = [0]
        def fairness(*args):
            calls[0] += 1
            return calls[0] == 1
        def fake_engine(moves, limit, fen, chess960, *, interrupt_check):
            interrupt_check()
            return review_fixture(sections[0])
        with (patch.object(remote, "_git", return_value=fake_git),
              patch.object(chess_play, "STOCKFISH_THREADS", remote.REMOTE_WORKER_THREADS)):
            with self.assertRaises(remote.RemoteReviewError):
                remote.compute_worker(
                    TICKET, 0, env=env, store=store,
                    idle=fairness, analyser=fake_engine)
        self.assertNotIn(remote._name("res", TICKET, 0), store.payloads)
        self.assertEqual(store.payloads[remote._name("progress", TICKET, 0)]["state"], "failed")

    def test_worker_rejects_unpinned_revision_before_engine(self):
        store, _ = self._prepared()
        env = {**ENV, "INPUT_REVISION": REVISION}
        bad_git = SimpleNamespace(returncode=0, stdout=("b" * 40 + "\n").encode())
        with patch.object(remote, "_git", return_value=bad_git):
            with self.assertRaises(remote.RemoteReviewError):
                remote.compute_worker(TICKET, 0, env=env, store=store,
                                      analyser=Mock(), idle=lambda *args: True)


class ReviewServiceRoutingTests(unittest.TestCase):
    def test_successful_remote_result_is_cached_without_local_engine(self):
        with patch.object(chess_play, "analyse_game_moves") as engine:
            service = GameReviewService()
            with patch("game_review_remote.try_remote_review",
                       return_value={"moves": [], "review_compute": "remote"}) as worker:
                self.assertEqual(service.analyse(["e4"])["review_compute"], "remote")
                self.assertEqual(service.analyse(["e4"])["review_compute"], "remote")
                worker.assert_called_once()
                engine.assert_not_called()

    def test_remote_failure_uses_local_engine_then_caches_fallback(self):
        with patch.object(chess_play, "analyse_game_moves", return_value={"moves": []}) as engine:
            service = GameReviewService()
            with patch("game_review_remote.try_remote_review", return_value=None) as worker:
                self.assertEqual(service.analyse(["e4"]), {"moves": []})
                self.assertEqual(service.analyse(["e4"]), {"moves": []})
                worker.assert_called_once()
                engine.assert_called_once()


if __name__ == "__main__":
    unittest.main()
