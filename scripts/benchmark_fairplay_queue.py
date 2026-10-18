"""Measure scheduling overhead and optionally verify real-engine equivalence.

Synthetic skew demonstrates the old batch barrier, not account-review speed.
The optional native test keeps production depth/MultiPV, clears position reuse,
and compares every decision metric. No account data or network is needed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
from pathlib import Path
from statistics import median
import sys
import time
from types import SimpleNamespace as NS

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chess.engine
from fairplay_config import CONFIG
from fairplay_v51_shadow import _scan_position_queue


def batched(executor,pool,positions,limit):
    for start in range(0,len(positions),limit):
        pending=[]
        for game,decision in positions[start:start+limit]:
            budget=chess.engine.Limit(depth=CONFIG.bullet_deep_depth
                                     if game.time_class=='bullet' else 18)
            pending.append(executor.submit(pool.run_decision,game,decision,budget,float('inf')))
        for future in as_completed(pending):future.result()


def timed(pool,positions,workers,limit,rolling):
    began=time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        if rolling:
            _scan_position_queue(executor,pool,positions,CONFIG,float('inf'),
                lambda *args:None,lambda:None,lambda stage:None,limit=limit)
        else:batched(executor,pool,positions,limit)
    return time.monotonic()-began


def synthetic(rounds):
    positions=[(NS(time_class='bullet'),NS(ply=i)) for i in range(384)]
    def run(game,decision,*args):time.sleep(.15 if decision.ply%128==0 else .0005)
    pool=NS(run_decision=run)
    samples={'batched':[],'rolling':[]}
    for iteration in range(rounds):
        for name in (('batched','rolling') if iteration%2==0 else ('rolling','batched')):
            samples[name].append(timed(pool,positions,4,128,name=='rolling'))
    old,new=median(samples['batched']),median(samples['rolling'])
    return {'positions':384,'workers':4,'pending_limit':128,'samples_seconds':samples,
        'median_batched_seconds':old,'median_rolling_seconds':new,
        'reduction_percent':100*(1-new/old),
        'limitation':'Synthetic skewed work, not a measured account-review speedup.'}


def native():
    from fairplay_analysis import SharedEnginePool
    from fairplay_data import parse_game
    from scripts.smoke_stockfish_reviews import TARGET, fixture
    game=parse_game(fixture(),TARGET)
    candidates=[d for d in game.decisions if d.useful]
    positions=[]
    for index in range(12):
        g=deepcopy(game);d=deepcopy(candidates[round(index*(len(candidates)-1)/11)])
        d.metrics={};d.fast_engine={}
        g.identity=f'synthetic-queue-{index}'
        g.time_class='blitz' if index%4==0 else 'bullet'
        g.decisions=[d];positions.append((g,d))
    pool=SharedEnginePool(CONFIG,size=2)
    pool.position_cache=None  # Both schedules must execute cold, exact searches.
    try:
        samples={'batched':[],'rolling':[]}
        baseline=None
        for order in (('batched','rolling'),('rolling','batched')):
            for name in order:
                rows=deepcopy(positions)
                samples[name].append(timed(pool,rows,2,4,name=='rolling'))
                metrics=[d.metrics for _,d in rows]
                if baseline is None:baseline=metrics
                assert baseline==metrics, 'Scheduling changed native engine evidence'
        old,new=median(samples['batched']),median(samples['rolling'])
        return {'positions':len(positions),'workers':2,'pending_limit':4,
            'samples_seconds':samples,'batched_seconds':old,'rolling_seconds':new,
            'reduction_percent':100*(1-new/old),
            'identical_metrics':True,'engine':pool.name,'new_searches':
                sum(s.profile['multipv_searches']+s.profile['root_searches'] for s in pool.scanners),
            'limitation':'Small synthetic native fixture, not a complete account runtime guarantee.'}
    finally:pool.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-engine',action='store_true')
    parser.add_argument('--rounds',type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.rounds<=20:parser.error('--rounds must be 1–20')
    result={'synthetic':synthetic(args.rounds)}
    if args.real_engine:result['native']=native()
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
