"""Real-engine equivalence/latency check on synthetic positions; no accounts."""
import copy
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chess
import chess.engine
import chess_play
from fairplay_analysis import BoundedNodeEngine, EngineScanner
from fairplay_config import CONFIG
from fairplay_data import ScanDeadline, collect_games
from scripts.smoke_stockfish_reviews import FixtureAPI, TARGET


def main():
    deadline=ScanDeadline(time.monotonic()+240)
    api=FixtureAPI(deadline)
    games,_,_=collect_games(api,TARGET,lambda _:None)
    positions=[(g,d) for g in games for d in g.decisions if d.useful][:30]
    measurements=[]
    outputs=[]
    for compact in (False,True):
        factory=None if compact else lambda:chess_play._create_stockfish_engine(
            allow_install=False,engine_class=BoundedNodeEngine)
        engine=EngineScanner(deadline,CONFIG,factory)
        rows=[]
        started=time.monotonic()
        try:
            for budget in (CONFIG.fast_nodes,CONFIG.deep_nodes):
                for game,decision in positions:
                    d=copy.deepcopy(decision)
                    engine.analyse_decision(game,d,budget)
                    rows.append(d.metrics)
        finally:engine.close()
        outputs.append(rows)
        measurements.append({'transport':'compact' if compact else 'python-chess',
            'seconds':round(time.monotonic()-started,4),'positions':len(rows)})
    assert outputs[0]==outputs[1], 'Compact transport changed fixed-node evidence'
    print(json.dumps({'equivalent':True,'measurements':measurements}))

if __name__=='__main__':main()
