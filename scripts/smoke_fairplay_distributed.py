"""Real pinned Stockfish 19 smoke for the v22 encrypted remote worker path.

Synthetic PGN only. Memory transport makes it impossible to dispatch
GitHub workflows, fetch real accounts or publish Discord reports.
"""
import copy
import os
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from fairplay_analysis import EngineScanner
from fairplay_config import CONFIG, VERSION
from fairplay_data import parse_game
from fairplay_distributed import SCHEMA, artifact_name, serialize_game, validate_response, worker
from scripts.smoke_stockfish_reviews import fixture, TARGET


class InMemoryTransport:
    def __init__(self):
        self.values={}
    def put(self,name,value):
        self.values[name]=copy.deepcopy(value)
        return True
    def read_many(self,names):
        return {name:copy.deepcopy(self.values[name]) for name in names if name in self.values}


def main():
    game=parse_game(fixture(),TARGET)
    if game is None:
        raise RuntimeError("Synthetic fixture invalid.")
    # One complete, genuine synthetic candidate decision exercises Stockfish
    # remote depth18 without the 70-position CI runtime of a full game.
    usable=next((d for d in game.decisions if d.useful),None)
    if usable is None:
        raise RuntimeError("Synthetic fixture lacks a useful decision.")
    game.decisions=[usable]
    scanner=EngineScanner(time.monotonic()+120,CONFIG)
    try:engine=scanner.name
    finally:scanner.close()
    ticket="a"*24
    revision="b"*40
    store=InMemoryTransport()
    store.put(artifact_name("req",ticket),{
        "schema":SCHEMA,"ticket":ticket,"version":VERSION,
        "revision":revision,"config":repr(CONFIG),"engine":engine,
        "created":time.time(),"games":[[serialize_game(game)],[],[],[],[]]})
    previous=os.environ.get("GITHUB_SHA")
    try:
        os.environ["GITHUB_SHA"]=revision
        outcome=worker(ticket,0,store=store,
                       env={"FAIRPLAY_DISTRIBUTED_KEY":"synthetic-local-secret",
                            "GITHUB_SHA":revision})
    finally:
        if previous is None:os.environ.pop("GITHUB_SHA",None)
        else:os.environ["GITHUB_SHA"]=previous
    response=store.read_many([artifact_name("res",ticket,0)])[
        artifact_name("res",ticket,0)]
    checked=validate_response([game],response,ticket=ticket,index=0,
                              revision=revision,engine=engine)
    if not checked[0].deep or outcome["positions"]!=1:
        raise RuntimeError("Real compute shard did not return verified deep results.")
    print("V22_REAL_COMPUTE_SMOKE passed: 1 synthetic decision, verified PV3 depth18.",
          flush=True)


if __name__=="__main__":
    main()
