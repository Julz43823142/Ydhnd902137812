"""v22 five-job compute isolation and complete central evidence regressions.

Synthetic accounts/PGNs only; offline except the localhost file-based git
transport. No live Chess.com, Discord or GitHub dispatches.
"""
import copy
import gzip
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fairplay_config import CONFIG, VERSION
from fairplay_data import ReviewError, parse_game
from fairplay_distributed import (EncryptedGitStore, SCHEMA, WORKERS, artifact_name,
                                  deserialize_game, join, key_material, pack,
                                  request_ticket, serialize_game, shard_games,
                                  start, unpack, validate_response)
from test_fairplay import sample_row, TARGET

SECRET="only-synthetic-test-secret-do-not-use"


def sample(i=1, *, bullet=False):
    row=sample_row(i,time_class="bullet" if bullet else "blitz")
    result=parse_game(row,TARGET)
    assert result is not None
    return result


def completed(game, engine="Stockfish synthetic"):
    g=copy.deepcopy(game)
    depth=CONFIG.bullet_deep_depth if g.time_class=="bullet" else 18
    for decision in g.decisions:
        decision.metrics={
            "cpl":0,"useful":bool(decision.useful),
            "search_depth":depth,
            "search_inconsistent":False,
            "search_contract":{"engine":engine,"mode":"depth","requested":depth,
                               "multipv":CONFIG.deep_multipv,
                               "completed":True,"exact":True}}
    g.deep=True
    return g


class Sharding(unittest.TestCase):
    def test_five_shards_balanced_and_complete(self):
        games=[sample(i+1) for i in range(17)]
        # Add varying length to prove position rather than game-count balance.
        for i,g in enumerate(games):g.decisions=g.decisions[:10+i]
        distributed=shard_games(games)
        self.assertEqual(len(distributed),5)
        self.assertEqual(set(g.identity for group in distributed for g in group),
                         set(g.identity for g in games))
        self.assertEqual(len([g for group in distributed for g in group]),len(games))
        workload=[sum(len(g.decisions) for g in group) for group in distributed]
        self.assertLessEqual(max(workload)-min(workload),max(len(g.decisions) for g in games))
        self.assertEqual([g.identity for group in shard_games(games)
                          for g in group],
                         [g.identity for group in distributed for g in group])

    def test_unchanged_real_game_dataclasses_are_roundtripped(self):
        game=sample()
        restored=deserialize_game(serialize_game(game))
        self.assertEqual(restored.identity,game.identity)
        self.assertEqual(restored.color,game.color)
        self.assertEqual(restored.moves,game.moves)
        self.assertEqual([d.fen for d in restored.decisions],
                         [d.fen for d in game.decisions])
        self.assertEqual(len(restored.decisions),len(game.decisions))


class Encryption(unittest.TestCase):
    def test_sealed_game_records_cannot_be_read_or_tampered_publicly(self):
        original={"schema":SCHEMA,"ticket":"a"*24,
                  "games":[serialize_game(sample())]}
        sealed=pack(original,SECRET)
        self.assertNotIn(b"synthetic-account",sealed)
        self.assertNotIn(b"rnbqkbnr",sealed)
        self.assertEqual(unpack(sealed,SECRET),original)
        with self.assertRaises(ReviewError):unpack(sealed,b"different".decode())
        with self.assertRaises(ReviewError):unpack(sealed[:-4]+b"abcd",SECRET)

    def test_ticket_is_keyed_stable_and_no_username_leaks(self):
        games=[sample(i) for i in range(1,4)]
        a=request_ticket(SECRET,TARGET,games,"b"*40)
        self.assertEqual(a,request_ticket(SECRET,TARGET,games,"b"*40))
        self.assertEqual(len(a),24)
        self.assertNotIn(TARGET,a)
        self.assertNotEqual(a,request_ticket("other",TARGET,games,"b"*40))
        with self.assertRaises(ReviewError):
            artifact_name("req","../../"+a)


class ResultIntegrity(unittest.TestCase):
    def test_all_positions_require_complete_expected_depth_and_exact_contract(self):
        for bullet in (False,True):
            with self.subTest(bullet=bullet):
                game=sample(10,bullet=bullet)
                good=completed(game)
                payload={"schema":SCHEMA,"ticket":"a"*24,"index":0,
                         "revision":"b"*40,"engine":"Stockfish synthetic",
                         "games":[serialize_game(good)]}
                rows=validate_response([game],payload,ticket="a"*24,
                    index=0,revision="b"*40,engine="Stockfish synthetic")
                self.assertEqual(len(rows),1)
                bad=copy.deepcopy(payload)
                bad["games"][0]["decisions"][0]["metrics"]["search_depth"]=5
                with self.assertRaises(ReviewError):
                    validate_response([game],bad,ticket="a"*24,
                        index=0,revision="b"*40,engine="Stockfish synthetic")
                wrong=copy.deepcopy(payload)
                wrong["games"][0]["decisions"][0]["metrics"]["search_contract"]["multipv"]=1
                with self.assertRaises(ReviewError):
                    validate_response([game],wrong,ticket="a"*24,
                        index=0,revision="b"*40,engine="Stockfish synthetic")

    def test_missing_or_different_shard_never_passes(self):
        games=[sample(2),sample(3)]
        completed_games=[completed(g) for g in games]
        payload={"schema":SCHEMA,"ticket":"a"*24,"index":2,
                 "revision":"b"*40,"engine":"Stockfish synthetic",
                 "games":[serialize_game(g) for g in completed_games]}
        for change in ({"games":payload["games"][:1]},
                       {"index":3},{"engine":"Other"}):
            with self.subTest(change=change):
                bad={**payload,**change}
                with self.assertRaises(ReviewError):
                    validate_response(games,bad,ticket="a"*24,index=2,
                        revision="b"*40,engine="Stockfish synthetic")


class MemoryStore:
    def __init__(self):
        self.data={}
        self.deleted=[]
    def put(self,name,payload):
        self.data[name]=copy.deepcopy(payload)
        return True
    def read_many(self,names):
        return {n:copy.deepcopy(self.data[n]) for n in names if n in self.data}
    def remove(self,names):
        self.deleted.extend(names)
        for n in names:self.data.pop(n,None)


class Coordinator(unittest.TestCase):
    @patch("fairplay_distributed._dispatch")
    def test_dispatch_one_workflow_not_five_and_reuses_resumable_ticket(self,dispatch):
        store=MemoryStore()
        games=[sample(i) for i in range(1,12)]
        env={"FAIRPLAY_DISTRIBUTED":"1","FAIRPLAY_DISTRIBUTED_KEY":SECRET,
             "GITHUB_TOKEN":"synthetic-actions-token",
             "GITHUB_REPOSITORY":"syntheticowner/syntheticrepo"}
        handle=start(games,TARGET,revision="b"*40,engine="Stockfish synthetic",
                     store=store,env=env)
        self.assertIsNotNone(handle)
        self.assertEqual(len(handle["shards"]),5)
        self.assertEqual(dispatch.call_count,1)
        self.assertEqual(len(store.data),1)
        payload=next(iter(store.data.values()))
        self.assertEqual(len(payload["games"]),5)
        # A planned runner handoff must not dispatch the same job again.
        restarted=start(games,TARGET,revision="b"*40,engine="Stockfish synthetic",
                        store=store,env=env)
        self.assertEqual(restarted["ticket"],handle["ticket"])
        self.assertEqual(dispatch.call_count,1)

    def test_barrier_reassembles_all_shards_without_issuing_worker_priority(self):
        games=[sample(i) for i in range(1,8)]
        shards=shard_games(games)
        ticket="a"*24
        store=MemoryStore()
        for index,group in enumerate(shards):
            store.put(artifact_name("res",ticket,index),
                {"schema":SCHEMA,"ticket":ticket,"index":index,
                 "revision":"b"*40,"engine":"Stockfish synthetic",
                 "games":[serialize_game(completed(g)) for g in group]})
        handle={"ticket":ticket,"shards":shards,"revision":"b"*40,
                "engine":"Stockfish synthetic","store":store,
                "started":time.monotonic(),"created":time.time()}
        events=[]
        output=join(handle,events.append,time.monotonic()+60,max_wait=30)
        self.assertEqual(len(output),len(games))
        self.assertEqual(set(output),{g.identity for g in games})
        self.assertEqual(len(store.deleted),11)
        self.assertNotIn("priority",str(output))
        self.assertTrue(events)
        self.assertIn("positions",events[-1])

    def test_dispatch_without_secrets_returns_safe_local_fallback(self):
        self.assertIsNone(start([sample()],TARGET,revision="b"*40,
            engine="Stockfish synthetic",
            env={"FAIRPLAY_DISTRIBUTED":"1",
                 "GITHUB_REPOSITORY":"syntheticowner/syntheticrepo"}))


class IsolatedGitRef(unittest.TestCase):
    def test_encrypted_compare_and_swap_preserves_other_worker_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            remote=Path(temporary)/"remote.git"
            working=Path(temporary)/"working"
            subprocess.run(["git","init","--bare","-q",str(remote)],check=True)
            subprocess.run(["git","init","-q",str(working)],check=True)
            subprocess.run(["git","-C",str(working),"remote","add","origin",str(remote)],
                           check=True)
            original=os.getcwd()
            try:
                os.chdir(working)
                store=EncryptedGitStore(SECRET,remote="origin")
                ticket="a"*24
                names=[artifact_name("req",ticket),
                       artifact_name("progress",ticket,0),
                       artifact_name("res",ticket,0)]
                for i,name in enumerate(names):
                    store.put(name,{"schema":SCHEMA,"counter":i,
                                    "game_id":TARGET})
                self.assertEqual(set(store.read_many(names)),set(names))
                content=subprocess.run(["git","show","refs/remotes/origin/fairplay-distributed-work:"+
                                         names[0]],check=True,capture_output=True).stdout
                self.assertNotIn(TARGET.encode(),content)
                self.assertNotIn(b'"game_id"',content)
                store.remove(names[:2])
                self.assertEqual(set(store.read_many(names)),{names[2]})
            finally:
                os.chdir(original)


if __name__=="__main__":
    unittest.main()
