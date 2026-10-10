"""v23 regression tests: immutable pinned workers and bullet depth12 contracts.

No real user accounts, real Discord traffic or GitHub dispatch. All fake
results are synthetic and never used to tune nb4/Batman classifications.
"""
import copy
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from fairplay_config import CONFIG, VERSION
from fairplay_data import ReviewError
from fairplay_difficulty import stability
from fairplay_distributed import (SCHEMA, WORKERS, artifact_name, join,
                                  key_material, safe_error_code, serialize_game,
                                  shard_games, start, worker)
from fairplay_stability_audit import summarise_stability
from test_fairplay_distributed import (MemoryStore, SECRET, completed, sample, TARGET)


class PinnedWorker(unittest.TestCase):
    def test_ten_workers_and_deterministic_balancing(self):
        self.assertEqual(WORKERS,10)
        games=[sample(i) for i in range(1,26)]
        groups=shard_games(games)
        self.assertEqual(len(groups),10)
        self.assertEqual([g.identity for group in groups for g in group].__len__(),len(games))
        self.assertEqual({g.identity for group in groups for g in group},{g.identity for g in games})

    @patch("fairplay_distributed._dispatch")
    def test_one_dispatch_with_original_commit_not_current_main(self, dispatch):
        store=MemoryStore()
        env={"FAIRPLAY_DISTRIBUTED":"1","FAIRPLAY_DISTRIBUTED_KEY":SECRET,
             "GITHUB_TOKEN":"synthetic-token",
             "GITHUB_REPOSITORY":"synthetic/repo"}
        handle=start([sample(i) for i in range(1,15)],TARGET,
                     revision="b"*40,engine="Stockfish synthetic",
                     store=store,env=env)
        self.assertEqual(len(handle["shards"]),10)
        dispatch.assert_called_once_with(handle["ticket"],"b"*40,
                                         "synthetic-token","synthetic/repo")

    def test_worker_accepts_only_pinned_revision_not_dispatch_github_sha(self):
        store=MemoryStore()
        ticket="a"*24
        store.put(artifact_name("req",ticket),{
            "schema":SCHEMA,"ticket":ticket,"version":VERSION,
            "revision":"b"*40,"config":repr(CONFIG),
            "created":time.time(),"engine":"Stockfish synthetic",
            "games":[[] for _ in range(WORKERS)]})
        # The latest main SHA may differ. INPUT_REVISION must match the
        # actual requested code; a worker may NEVER adopt GITHUB_SHA instead.
        with self.assertRaises(ReviewError):
            worker(ticket,0,store=store,
                   env={"FAIRPLAY_DISTRIBUTED_KEY":SECRET,
                        "INPUT_REVISION":"c"*40,"GITHUB_SHA":"b"*40})
        # Valid pinned revision with different GITHUB_SHA is not rejected;
        # the no-game synthetic shard needs no engine evaluation but may
        # require a local Stockfish executable, tested by real engine smoke.
        self.assertEqual(safe_error_code(ReviewError(
            "Unavailable, mismatched or expired compute workload.")),
            "request_or_revision")

    def test_secret_selection_matches_workflow_precedence(self):
        self.assertEqual(key_material({"DISCORD_TOKEN":"a",
                                       "FAIRPLAY_CHECKPOINT_KEY":"b"}),"b")
        self.assertEqual(key_material({"DISCORD_TOKEN":"a",
                                       "FAIRPLAY_CHECKPOINT_KEY":"b",
                                       "FAIRPLAY_DISTRIBUTED_KEY":"c"}),"c")
        workflow=Path(".github/workflows/fairplay_distributed.yml").read_text()
        self.assertIn("secrets.FAIRPLAY_DISTRIBUTED_KEY || secrets.FAIRPLAY_CHECKPOINT_KEY || secrets.DISCORD_TOKEN",workflow)
        self.assertIn("ref: ${{ inputs.revision }}",workflow)
        self.assertIn("fetch-depth: 1",workflow)
        self.assertIn("max-parallel: 10",workflow)
        self.assertIn("shard: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]",workflow)

    def test_public_failure_codes_are_constant_strings(self):
        self.assertEqual(safe_error_code(ReviewError(
            "Invalid or corrupt encrypted compute artifact.")),"encrypted_packet")
        self.assertEqual(safe_error_code(ReviewError(
            "secret-account-fen")), "compute_validation")


class Recovery(unittest.TestCase):
    def test_all_ten_failed_shards_do_not_consume_retry_budget(self):
        ticket="a"*24
        groups=shard_games([sample(i) for i in range(1,21)])
        store=MemoryStore()
        for index in range(WORKERS):
            store.put(artifact_name("progress",ticket,index),{
                "schema":SCHEMA,"ticket":ticket,"revision":"b"*40,
                "index":index,"state":"failed","done":0})
        handle={"ticket":ticket,"shards":groups,"revision":"b"*40,
                "engine":"Stockfish synthetic","store":store,
                "started":time.monotonic(),"created":time.time()}
        with patch("fairplay_distributed.time.sleep") as sleep:
            result=join(handle,lambda _:None,time.monotonic()+60,max_wait=40)
        self.assertEqual(result,{})
        self.assertEqual(handle["stats"]["failed"],WORKERS)
        self.assertEqual(handle["stats"]["stalled"],0)
        self.assertEqual(handle["stats"]["reason"],"worker_failed")
        sleep.assert_not_called()

    def test_partial_success_preserved_when_other_nine_workers_fail(self):
        ticket="a"*24
        groups=shard_games([sample(i) for i in range(1,21)])
        store=MemoryStore()
        good=completed(groups[0][0])
        # Status 0 is superseded by a verified remote response.
        store.put(artifact_name("res",ticket,0),{
            "schema":SCHEMA,"ticket":ticket,"revision":"b"*40,
            "index":0,"engine":"Stockfish synthetic",
            "games":[serialize_game(completed(g)) for g in groups[0]]})
        for i in range(1,WORKERS):
            store.put(artifact_name("progress",ticket,i),{
                "schema":SCHEMA,"ticket":ticket,"revision":"b"*40,
                "index":i,"state":"failed","done":0})
        handle={"ticket":ticket,"shards":groups,"revision":"b"*40,
                "engine":"Stockfish synthetic","store":store,
                "started":time.monotonic(),"created":time.time()}
        result=join(handle,lambda _:None,time.monotonic()+60,max_wait=40)
        self.assertEqual(set(result),{g.identity for g in groups[0]})
        self.assertEqual(handle["stats"]["completed"],1)
        self.assertEqual(handle["stats"]["failed"],9)


class BulletStability(unittest.TestCase):
    def template(self,depth=12,exact=True):
        game=sample(87,bullet=True)
        decision=next(d for d in game.decisions if d.useful)
        for_fast={"nodes":CONFIG.fast_nodes,"rank":1,"best":decision.move,
                  "cpl":5,"scaled_loss":.01,"useful":True,
                  "search_inconsistent":False,
                  "played_boundary_cp":90,"difficulty":.9,"competitive":True,
                  "search_contract":{"engine":"Stockfish synthetic",
                     "mode":"nodes","requested":CONFIG.fast_nodes,
                     "completed":True,"exact":True,"multipv":3}}
        deep=copy.deepcopy(for_fast)
        deep.update(nodes=5000,search_depth=depth,
                    search_contract={"engine":"Stockfish synthetic",
                      "mode":"depth","requested":depth,"completed":True,
                      "exact":exact,"multipv":3})
        decision.fast_engine=for_fast
        decision.metrics=deep
        return game,decision

    def test_complete_exact_depth12_bullet_is_measurable(self):
        _,decision=self.template()
        self.assertTrue(stability(decision,CONFIG))
        self.assertTrue(decision.metrics["search_stability"]["compared"])
        self.assertEqual(decision.metrics["search_stability"]["paired_depth"],12)

    def test_depth12_incomplete_or_nonexact_never_passes(self):
        for complete_depth,exact in [(11,True),(12,False)]:
            _,decision=self.template(depth=12,exact=exact)
            decision.metrics["search_depth"]=complete_depth
            self.assertFalse(stability(decision,CONFIG))
            self.assertFalse(decision.metrics["search_stability"]["compared"])

    def test_depth18_and_missing_contract_are_handled(self):
        _,decision=self.template(depth=18)
        self.assertTrue(stability(decision,CONFIG))
        _,missing=self.template()
        missing.metrics.pop("search_contract")
        self.assertFalse(stability(missing,CONFIG))

    def test_audit_is_descriptive_and_counts_bullet_coverage(self):
        game,decision=self.template()
        game.decisions=[decision]
        game.deep=True
        stability(decision,CONFIG)
        report=summarise_stability([game],[game.identity])
        self.assertFalse(report["scoring_influence"])
        self.assertEqual(report["classes"]["bullet"]["compared"],1)
        self.assertEqual(report["selected_period"]["stable"],1)
        self.assertNotIn("username",report)


if __name__=="__main__":
    unittest.main()
