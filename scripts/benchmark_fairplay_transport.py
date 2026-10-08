"""Real-engine equivalence/latency check on synthetic positions; no accounts."""
import copy
import json
import os
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


class CoherentReference(BoundedNodeEngine):
    # Independent python-chess protocol parser; select the same complete round.
    # CI has an outer job deadline; production retains its bounded transport.
    def analyse(self,board,limit,*,multipv=None,root_moves=None):
        from fairplay_engine import CoherentCandidates
        count=min(multipv or 1,len(root_moves) if root_moves is not None else board.legal_moves.count())
        collector=CoherentCandidates(count)
        with self.analysis(board,limit,multipv=multipv,root_moves=root_moves) as stream:
            for info in stream:collector.add(info.get('multipv',1),info)
        values=collector.result()
        return values if multipv is not None else values[0]


def main():
    deadline=ScanDeadline(time.monotonic()+240)
    api=FixtureAPI(deadline)
    games,_,_=collect_games(api,TARGET,lambda _:None)
    positions=[(g,d) for g in games for d in g.decisions if d.useful][:30]
    measurements=[]
    outputs=[]
    for compact in (False,True):
        baseline_path=os.environ.get('BASELINE_STOCKFISH_PATH')
        factory=(None if compact else
            (lambda:CoherentReference.popen_uci(baseline_path,timeout=15)) if baseline_path else
            (lambda:chess_play._create_stockfish_engine(allow_install=False,engine_class=CoherentReference)))
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
            'seconds':round(time.monotonic()-started,4),'positions':len(rows),'profile_guided':bool(compact and baseline_path)})
    assert outputs[0]==outputs[1], 'Complete-round parsers disagree on fixed-node evidence'
    print(json.dumps({'equivalent':True,'measurements':measurements}))

if __name__=='__main__':main()
