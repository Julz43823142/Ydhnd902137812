"""Local calibration only. Production modules never import or read this file.

External labels are compared AFTER an analytical priority is frozen. No names,
reports or accusations are written/printed: output contains aggregate counts.
Use explicitly labelled trusted normal accounts; 'not banned' is not a label.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from fairplay_data import username

LABELS = ('known_fair_play_closed', 'trusted_normal')
PRIORITIES = ('LOW', 'MODERATE', 'HIGH', 'VERY HIGH', 'INSUFFICIENT DATA')


def evaluate_cases(cases, analyze):
    counts = {label:Counter() for label in LABELS}
    failures = Counter()
    for case in cases:
        if not isinstance(case,dict) or case.get('label') not in LABELS:
            raise ValueError('Use explicit known_fair_play_closed or trusted_normal labels.')
        # The analyzer receives ONLY the target, never a label or the case dict.
        try:
            result = analyze(username(case.get('username','')))
            frozen_priority = str(result.priority)
            if frozen_priority not in PRIORITIES:raise ValueError('Invalid analytical priority.')
        except Exception:
            failures[case['label']] += 1
            continue  # no private target/error detail reaches stdout/public logs
        counts[case['label']][frozen_priority] += 1
    positive = sum(counts[LABELS[0]].values())
    high = counts[LABELS[0]]['HIGH']+counts[LABELS[0]]['VERY HIGH']
    moderate = high+counts[LABELS[0]]['MODERATE']
    return {'cases':{label:{p:counts[label][p] for p in PRIORITIES} for label in LABELS},
            'failed':{label:failures[label] for label in LABELS},
            'known_positive_completed':positive,
            'high_or_higher_recall':high/positive if positive else None,
            'moderate_or_higher_recall':moderate/positive if positive else None,
            'trusted_normal_high_or_higher':counts[LABELS[1]]['HIGH']+counts[LABELS[1]]['VERY HIGH'],
            'note':'External user labels, not certified ground truth. Failed scans excluded from recall denominators; insufficient data included. No empirical calibration claim.'}



def private_input_path(path):
    """Inside this public checkout, inputs must use explicitly ignored paths."""
    path=Path(path).resolve();root=Path(__file__).resolve().parent
    if root in path.parents or path==root:
        relative=path.relative_to(root)
        allowed=('fairplay_validation.local' in relative.parts
                 or path.name=='validation_cases.local.json'
                 or (path.name.startswith('fairplay_validation') and path.name.endswith('.local.json')))
        if not allowed:raise ValueError('Private validation inputs must use ignored local paths or live outside the checkout.')
    return path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases',type=Path,help='Ignored validation_cases.local.json; never commit cases.')
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--offline-dir',type=Path,help='Private ignored directory with per-account JSON {profile, games}.')
    source.add_argument('--live',action='store_true',help='Explicitly permit serial PubAPI scans. No Discord activity.')
    parser.add_argument('--history-games',type=int,default=500)
    args=parser.parse_args()
    from dataclasses import replace
    from fairplay_analysis import review
    from fairplay_config import CONFIG
    config=replace(CONFIG,history_games=max(1,min(500,args.history_games)))
    def analyze(target):
        kwargs={}
        if args.offline_dir:
            payload=json.loads((args.offline_dir/(target+'.json')).read_text())
            class OfflineAPI:
                def __init__(self,deadline):self.deadline=deadline
                def get(self,name,suffix='',**kw):
                    if not suffix:return payload['profile']
                    if suffix.endswith('/archives'):return {'archives':[f'https://api.chess.com/pub/player/{name}/games/2026/10']}
                    return {'games':payload['games']}
                def close(self):pass
            kwargs['api_factory']=OfflineAPI
        return review(target,lambda stage:None,config,**kwargs)
    try:
        private_input_path(args.cases)
        if args.offline_dir:private_input_path(args.offline_dir)
        cases=json.loads(args.cases.read_text())
        if not isinstance(cases,list):raise ValueError('Expected case list.')
        summary=evaluate_cases(cases,analyze)
    except (ValueError,OSError,TypeError):
        parser.exit(2,'Validation input could not be read. No case details were logged.\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
