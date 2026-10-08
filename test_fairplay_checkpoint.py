"""Regression coverage for encrypted resume and elapsed-time worker interruptions."""
import concurrent.futures
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import chess.engine

from fairplay_analysis import review, run_position_batch
from fairplay_checkpoint import CheckpointStore
from fairplay_config import CONFIG, VERSION
from fairplay_data import ScanDeadline


def game_with_positions(count=2):
    rows = [
        types.SimpleNamespace(
            ply=i + 1, fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            move="e2e4", metrics={}, fast_engine={}, useful=True
        ) for i in range(count)
    ]
    return types.SimpleNamespace(identity="synthetic-id", color=True,
                                 ended=123456, decisions=rows)


def metrics(phase, *, depth=18):
    result = {"search_contract": {
        "completed": True, "mode": "depth" if phase == "deep" else "nodes",
        "requested": depth if phase == "deep" else CONFIG.fast_nodes,
    }}
    if phase == "deep":
        result["search_depth"] = depth
    return result


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "DISCORD_TOKEN": "synthetic-only-secret-not-real",
            "FAIRPLAY_CHECKPOINT_FILE": str(Path(self.temp.name) / "resume.enc"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = str(Path(self.temp.name) / "resume.enc")

    def store(self):
        return CheckpointStore(path=self.path, remote="")

    def bind(self, store):
        store.note("privateaccount", token="synthetic-token", message_id=1234)
        store.bind("privateaccount", engine="Stockfish 19", version=VERSION,
                   config=CONFIG, full_depth=True, maia="maia-sha")

    def test_encrypted_resume_skips_completed_positions_across_restart(self):
        a = self.store()
        self.bind(a)
        original = game_with_positions()
        original.decisions[0].metrics = metrics("fast")
        self.assertTrue(a.record("privateaccount", original, original.decisions[0], "fast"))
        self.assertTrue(a.flush())
        ciphertext = Path(self.path).read_bytes()
        self.assertNotIn(b"privateaccount", ciphertext)
        self.assertNotIn(b"synthetic-id", ciphertext)
        self.assertNotIn(b"e2e4", ciphertext)

        b = self.store()
        self.assertEqual(b.pending()[0]["message_id"], 1234)
        new = game_with_positions()
        class Engine:
            count = 0
            def run_decision(self, game, decision, nodes, deadline):
                self.count += 1
                decision.metrics = metrics("fast")
        engine = Engine()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            finished, interrupted = run_position_batch(
                executor, engine,
                [(new, item) for item in new.decisions],
                CONFIG.fast_nodes, ScanDeadline(time.monotonic()+60),
                lambda _: None, "Fast", checkpoint=b, target="privateaccount",
                phase="fast", checkpoint_config=CONFIG, checkpoint_full_depth=True)
        self.assertFalse(interrupted)
        self.assertEqual(finished, {id(new)})
        self.assertEqual(engine.count, 1, "restored position must not be searched twice")
        self.assertTrue(b.restore("privateaccount",new,new.decisions[1],"fast",CONFIG,True))

    def test_depth18_and_identity_contract(self):
        store = self.store()
        self.bind(store)
        game = game_with_positions(1)
        d = game.decisions[0]
        d.metrics = metrics("deep")
        store.record("privateaccount",game,d,"deep")
        self.assertTrue(store.restore("privateaccount", game, d, "deep", CONFIG, True))
        bad = game_with_positions(1)
        bad.decisions[0].fen = "new-board-state"
        self.assertFalse(store.restore("privateaccount",bad,bad.decisions[0],"deep",CONFIG,True))
        store.bind("privateaccount", engine="Stockfish 20", version=VERSION,
                   config=CONFIG, full_depth=True, maia="maia-sha")
        self.assertFalse(store.restore("privateaccount",game,d,"deep",CONFIG,True))
        d.metrics = metrics("deep",depth=17)
        self.assertTrue(store.record("privateaccount",game,d,"deep"))
        self.assertFalse(store.restore("privateaccount",game,d,"deep",CONFIG,True))

    def test_no_full_depth_hour_deadline(self):
        sentinel = RuntimeError("captured")
        captured = []
        def api_factory(deadline):
            captured.append(deadline)
            raise sentinel
        with patch.dict(os.environ, {"FAIRPLAY_FULL_DEPTH18":"1",
                                     "FAIRPLAY_FULL_SCAN_DEADLINE_SECONDS":"0"}):
            with self.assertRaisesRegex(RuntimeError, "captured"):
                review("safeaccount", lambda _:None, api_factory=api_factory)
        self.assertEqual(float(captured[0]), float("inf"))
        with patch.dict(os.environ, {"FAIRPLAY_FULL_DEPTH18":"1",
                                     "FAIRPLAY_FULL_SCAN_DEADLINE_SECONDS":"14400"}):
            with self.assertRaisesRegex(RuntimeError, "captured"):
                review("safeaccount", lambda _:None, api_factory=api_factory)
        self.assertAlmostEqual(captured[1]-time.monotonic(), 14400, delta=5)

    def test_maia_predictions_resume_without_second_inference(self):
        import fairplay_maia
        first = self.store()
        self.bind(first)
        game = game_with_positions(1)
        game.human_reference = {}
        seen = []
        def predict(batch):
            seen.append(len(batch))
            return [{"e2e4": 1.0} for _ in batch]
        def select(games, *, full_coverage=False):
            return [(games[0], games[0].decisions[0], {"position": 1})]
        def refresh(game):
            game.human_reference = {"positions": 1}
        with patch.object(fairplay_maia,"selection",side_effect=select), \
             patch.object(fairplay_maia,"valid_policy",return_value=True), \
             patch.object(fairplay_maia,"refresh_game",side_effect=refresh):
            fairplay_maia._cache.clear()
            first_result = fairplay_maia.annotate_history(
                [game],predictor=predict,checkpoint=first,target="privateaccount")
            self.assertTrue(first_result["available"])
            self.assertEqual(seen,[1])
            first.flush()
            second = self.store()
            new = game_with_positions(1)
            new.human_reference = {}
            fairplay_maia._cache.clear()
            def should_not_recompute(_):
                self.fail("Maia inference must use the durable model-matched snapshot")
            second_result = fairplay_maia.annotate_history(
                [new],predictor=should_not_recompute,
                checkpoint=second,target="privateaccount")
            self.assertTrue(second_result["available"])
            self.assertEqual(new.decisions[0].human_policy,{"e2e4":1.0})
            fairplay_maia._cache.clear()

    def test_counterfactual_reuses_completed_search(self):
        import fairplay_policy
        first=self.store()
        self.bind(first)
        game=game_with_positions(1)
        game.decisions[0].human_policy={"e2e4":1.0}
        seen=[]
        def search(scanner, decision, nodes):
            seen.append(nodes)
            return {"nodes":nodes,"scores":{},"search_contract":{
                "mode":"nodes","requested":nodes,"completed":True,"exact":True}}
        with patch.object(fairplay_policy,"search_alternatives",side_effect=search), \
             patch("fairplay_maia.refresh_game"), \
             patch("fairplay_maia.policy_evidence",return_value={}):
            one=fairplay_policy.complete([game],CONFIG.fast_nodes,
                ScanDeadline(time.monotonic()+60),scanner=object(),
                fast=True,checkpoint=first,target="privateaccount")
            self.assertTrue(one["complete"])
            first.flush()
            new=game_with_positions(1)
            new.decisions[0].human_policy={"e2e4":1.0}
            second=self.store()
            two=fairplay_policy.complete([new],CONFIG.fast_nodes,
                ScanDeadline(time.monotonic()+60),scanner=object(),
                fast=True,checkpoint=second,target="privateaccount")
            self.assertTrue(two["complete"])
            self.assertEqual(len(seen),1)

    def test_remote_branch_survives_ephemeral_worker(self):
        bare = Path(self.temp.name) / "remote.git"
        work = Path(self.temp.name) / "runner"
        def git(*args, cwd=None):
            return subprocess.run(["git",*args], cwd=cwd, check=True,
                                  capture_output=True, text=True)
        git("init","--bare",str(bare))
        work.mkdir()
        git("init",cwd=work)
        git("config","user.name","Test Robot",cwd=work)
        git("config","user.email","robot@example.invalid",cwd=work)
        git("remote","add","origin",str(bare),cwd=work)
        old = os.getcwd()
        try:
            os.chdir(work)
            first = CheckpointStore(path=self.path,remote="origin")
            self.bind(first)
            game = game_with_positions(1)
            game.decisions[0].metrics = metrics("deep")
            first.record("privateaccount",game,game.decisions[0],"deep")
            self.assertTrue(first.flush(), "checkpoint must be durable in Git")
            Path(self.path).unlink()
            # Simulate an entirely fresh Actions checkout.
            second = CheckpointStore(path=str(work/"new-runner.enc"),remote="origin")
            self.assertEqual(len(second.pending()),1)
            fresh = game_with_positions(1)
            self.assertTrue(second.restore("privateaccount",fresh,fresh.decisions[0],
                                           "deep",CONFIG,True))
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
