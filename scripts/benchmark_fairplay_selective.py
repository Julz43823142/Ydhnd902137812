"""Specified synthetic policy stress test, NOT measured real-world detection.

No network, models, player accounts or production state. A toy human policy
provides the null distribution; selective cases replace three of 24 decisions.
The old comparator is the learned-policy contributor gate, not all SharkBot
priority routes. Independent seeds exercise known structural behavior only.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_fairplay_v50 import synthetic_game,episode_result
from fairplay_policy import game_summary


def benchmark(samples=20):
    output={}
    for mode in ('human','elite','rare_bad','selective','one_game'):
        moderate=high=old_candidates=0
        for seed in range(1000,1000+samples):
            games=[synthetic_game(i,'selective' if mode=='one_game' and i==9 else
                                  'human' if mode=='one_game' else mode,seed=seed) for i in range(10)]
            old_candidates+=sum(game_summary(g)['contributor'] for g in games)>=6
            priority=episode_result(games).priority
            moderate+=priority=='MODERATE';high+=priority in ('HIGH','VERY HIGH')
        output[mode]={'samples':samples,'old_policy_six_contributor_samples':old_candidates,
                      'new_episode_moderate_samples':moderate,'high_created':high}
    if any(row['high_created'] for row in output.values()):
        raise AssertionError('The episode route exceeded its MODERATE cap.')
    if output['selective']['new_episode_moderate_samples']!=samples or any(
            output[k]['new_episode_moderate_samples'] for k in ('human','elite','rare_bad','one_game')):
        raise AssertionError('Specified toy policy stress contract failed.')
    return {'schema':'sharkbot-synthetic-selective-stress-v1','results':output,
            'real_world_accuracy_measured':False,
            'limitation':'Specified synthetic policies and fake exact engine values. This does not run the full SharkBot classifier or establish live detection/false-positive rates.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.samples<=100:parser.error('samples must be 1..100')
    print(json.dumps(benchmark(args.samples),indent=2))
