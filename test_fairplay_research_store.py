"""Offline regression tests: private per-review persistence and 200-account resume."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fairplay_research_store import ResearchRecordStore, ResearchGitFailure, _check


def next_state(previous, i):
    return {**previous, "cursor": i,
            "rows": previous["rows"] + [{"account": f"private-user-{i}", "group": "closed"}],
            "evidence": previous["evidence"] + [f"private-evidence-{i}" * 30]}


class ResearchRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"FAIRPLAY_CHECKPOINT_KEY": "private-test-secret",
                                           "GITHUB_ACTIONS": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_local_resume_from_35_and_complete_200(self):
        path = Path(self.tmp.name) / "records"
        store = ResearchRecordStore(remote="", path=path)
        state = {"cursor": 0, "rows": [], "evidence": [], "digest": "digest",
                 "archive_version": 3, "finished": False, "started_at": 10}
        self.assertTrue(store.save_research(state))
        for i in range(1, 36):
            state = next_state(state, i)
            self.assertTrue(store.save_research(state))
        resumed = ResearchRecordStore(remote="", path=path)
        self.assertEqual(resumed.research_state()["cursor"], 35)
        self.assertEqual(resumed.research_state()["evidence"][-1], state["evidence"][-1])
        for i in range(36, 201):
            state = next_state(state, i)
            self.assertTrue(resumed.save_research(state))
        state = {**state, "finished": True}
        self.assertTrue(resumed.save_research(state))
        final = ResearchRecordStore(remote="", path=path)
        self.assertEqual(final.research_state(), state)
        self.assertEqual(len(list(path.glob("*.enc"))), 202)
        for file in path.glob("*.enc"):
            raw = file.read_bytes()
            self.assertNotIn(b"private-user", raw)
            self.assertNotIn(b"private-evidence", raw)
        self.assertFalse(final.save_research({**state, "cursor": 199}))

    def test_remote_push_failure_does_not_advance_and_retry_resumes(self):
        bare = Path(self.tmp.name) / "remote.git"
        checkout = Path(self.tmp.name) / "checkout"
        def git(*args, cwd=None):
            return subprocess.run(["git", *args], cwd=cwd, check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git("init", "--bare", str(bare))
        checkout.mkdir()
        git("init", cwd=checkout)
        git("remote", "add", "origin", str(bare), cwd=checkout)
        original = os.getcwd()
        try:
            os.chdir(checkout)
            state = {"cursor": 0, "rows": [], "evidence": [], "digest": "test-digest",
                     "archive_version": 3, "finished": False, "started_at": 1}
            first = ResearchRecordStore(remote="origin")
            self.assertTrue(first.save_research(state))
            initial = first.head
            next_one = next_state(state, 1)
            from fairplay_research_store import _git as real_git
            def fail_push(args, **kwargs):
                if args[0] == "push":
                    return subprocess.CompletedProcess(args, 1, b"", b"private-stderr")
                return real_git(args, **kwargs)
            with patch("fairplay_research_store._git", side_effect=fail_push):
                self.assertFalse(first.save_research(next_one))
            self.assertEqual(first.research_state()["cursor"], 0)
            self.assertEqual(first.head, initial)
            self.assertTrue(first.save_research(next_one))
            resumed = ResearchRecordStore(remote="origin")
            self.assertEqual(resumed.research_state(), next_one)
            self.assertTrue(resumed.save_research(next_state(next_one, 2)))
            self.assertEqual(ResearchRecordStore(remote="origin").research_state()["cursor"], 2)
        finally:
            os.chdir(original)

    def test_git_error_is_diagnostic_without_leaking_stderr(self):
        failed = subprocess.CompletedProcess(
            ["git", "push"], 128, b"", b"secret-token-private-account")
        with self.assertRaises(ResearchGitFailure) as caught:
            _check(failed, "push")
        self.assertEqual(caught.exception.step, "push")
        self.assertEqual(caught.exception.code, 128)
        self.assertIn("push failed (exit 128)", str(caught.exception))
        self.assertNotIn("secret-token-private-account", str(caught.exception))

    def test_remote_history_replays_all_200_reviews_after_restart(self):
        bare = Path(self.tmp.name) / "all-200.git"
        checkout = Path(self.tmp.name) / "worker"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        checkout.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(bare)],
                       cwd=checkout, check=True)
        previous_directory = os.getcwd()
        try:
            os.chdir(checkout)
            store = ResearchRecordStore(remote="origin")
            state = {"cursor": 0, "rows": [], "evidence": [], "digest": "digest",
                     "archive_version": 3, "finished": False, "started_at": 10}
            self.assertTrue(store.save_research(state))
            for i in range(1, 36):
                state = next_state(state, i)
                self.assertTrue(store.save_research(state))
            # Simulate an entirely fresh GitHub Actions worker after 35 reviews.
            store = ResearchRecordStore(remote="origin")
            self.assertEqual(store.research_state()["cursor"], 35)
            for i in range(36, 201):
                state = next_state(state, i)
                self.assertTrue(store.save_research(state), i)
            state = {**state, "finished": True}
            self.assertTrue(store.save_research(state))
            restored = ResearchRecordStore(remote="origin")
            self.assertEqual(restored.research_state(), state)
            history = subprocess.run(
                ["git", "rev-list", "--count", restored.head], check=True,
                stdout=subprocess.PIPE).stdout.strip()
            self.assertEqual(history, b"202")
            self.assertFalse(restored.save_research({**state, "cursor": 199}))
        finally:
            os.chdir(previous_directory)

    def test_invalid_transition_and_corrupted_record_fail_closed(self):
        path = Path(self.tmp.name) / "records"
        store = ResearchRecordStore(remote="", path=path)
        init = {"cursor": 0, "rows": [], "evidence": [], "digest": "a",
                "archive_version": 3, "finished": False}
        self.assertTrue(store.save_research(init))
        self.assertFalse(store.save_research({**init, "cursor": 2}))
        self.assertFalse(store.save_research({**init, "digest": "b", "cursor": 1,
                                              "rows": [{}], "evidence": ["one"]}))
        (path / "000.enc").write_bytes(b"plaintext")
        with self.assertRaises(ValueError):
            ResearchRecordStore(remote="", path=path)


if __name__ == "__main__":
    unittest.main()
