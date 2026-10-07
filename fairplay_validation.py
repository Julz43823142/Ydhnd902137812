"""Local calibration only. Production modules never import or read this file.

External labels are compared AFTER an analytical priority is frozen. No names,
reports or accusations are written/printed: output contains aggregate counts.
Use explicitly labelled trusted normal accounts; 'not banned' is not a label.
"""
import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from fairplay_data import username

LABELS = ('known_fair_play_closed', 'trusted_normal', 'positive', 'holdout_positive', 'holdout_normal')
PRIORITIES = ('LOW', 'MODERATE', 'HIGH', 'VERY HIGH', 'INSUFFICIENT DATA')
POSITIVE_LABELS = ('known_fair_play_closed', 'positive', 'holdout_positive')
NORMAL_LABELS = ('trusted_normal', 'holdout_normal')


def positive_low_bucket(result):
    """Post-hoc miss taxonomy. Never called before analytical priority freezes.

    Near-miss means the production distributed-MODERATE route is blocked by
    exactly one remaining gate. More than one blocker (or no usable candidate)
    is treated as a weak-window/multi-blocked miss. This classification is for
    aggregate validation only and never feeds thresholds or production scoring.
    """
    priority=str(getattr(result,'priority',''))
    if priority!='LOW':return None,()
    diagnostics=getattr(result,'diagnostics',{})
    gameplay=diagnostics.get('gameplay',{}) if isinstance(diagnostics,dict) else {}
    moderate=gameplay.get('distributed_moderate',{}) if isinstance(gameplay,dict) else {}
    if moderate.get('passed'):
        return 'diagnostic_inconsistency',()
    blockers=tuple(str(value) for value in moderate.get('blockers',()) if value)
    candidate_games=moderate.get('candidate_games',0)
    try:candidate_games=int(candidate_games)
    except (TypeError,ValueError):candidate_games=0
    if candidate_games>0 and len(blockers)==1:return 'near_miss',blockers
    return 'weak_window',blockers


def evaluate_cases(cases, analyze):
    counts = {label:Counter() for label in LABELS}
    failures = Counter()
    indices={label:[] for label in LABELS}
    positive_low_buckets=Counter();positive_low_blockers=Counter()
    positive_low_by_label={label:Counter() for label in POSITIVE_LABELS}
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
        diagnostics=getattr(result,'diagnostics',{})
        index=diagnostics.get('gameplay',{}).get('research_evidence_index') if isinstance(diagnostics,dict) else None
        if isinstance(index,(int,float)):indices[case['label']].append(index)
        # Labels are consulted only after the analyzer returned a frozen result.
        if case['label'] in POSITIVE_LABELS and frozen_priority=='LOW':
            bucket,blockers=positive_low_bucket(result)
            positive_low_buckets[bucket or 'unclassified']+=1
            positive_low_by_label[case['label']][bucket or 'unclassified']+=1
            positive_low_blockers.update(blockers)
    positive=sum(sum(counts[label].values()) for label in POSITIVE_LABELS)
    normal=sum(sum(counts[label].values()) for label in NORMAL_LABELS)
    high_positive=sum(counts[label]['HIGH']+counts[label]['VERY HIGH'] for label in POSITIVE_LABELS)
    moderate_positive=high_positive+sum(counts[label]['MODERATE'] for label in POSITIVE_LABELS)
    high_normal=sum(counts[label]['HIGH']+counts[label]['VERY HIGH'] for label in NORMAL_LABELS)
    moderate_normal=high_normal+sum(counts[label]['MODERATE'] for label in NORMAL_LABELS)
    high_total=high_positive+high_normal
    high_tpr=high_positive/positive if positive else None
    high_fpr=high_normal/normal if normal else None
    high_specificity=1-high_fpr if high_fpr is not None else None
    high_precision=high_positive/high_total if high_total else None
    high_accuracy=(high_positive+(normal-high_normal))/(positive+normal) if positive+normal else None
    return {'cases':{label:{p:counts[label][p] for p in PRIORITIES} for label in LABELS},
            'failed':{label:failures[label] for label in LABELS},
            'known_positive_completed':sum(counts[LABELS[0]].values()),
            'high_or_higher_recall':(
                counts[LABELS[0]]['HIGH']+counts[LABELS[0]]['VERY HIGH'])/sum(counts[LABELS[0]].values())
                if sum(counts[LABELS[0]].values()) else None,
            'moderate_or_higher_recall':(
                counts[LABELS[0]]['HIGH']+counts[LABELS[0]]['VERY HIGH']+counts[LABELS[0]]['MODERATE'])/
                sum(counts[LABELS[0]].values()) if sum(counts[LABELS[0]].values()) else None,
            'trusted_normal_completed':sum(counts[LABELS[1]].values()),
            'trusted_normal_moderate_or_higher':sum(counts[LABELS[1]][p] for p in ('MODERATE','HIGH','VERY HIGH')),
            'trusted_normal_high_or_higher':counts[LABELS[1]]['HIGH']+counts[LABELS[1]]['VERY HIGH'],
            'binary_high_cutoff':{
                'positive_completed':positive,'normal_completed':normal,
                'recall_tpr':high_tpr,'false_positive_rate':high_fpr,
                'specificity':high_specificity,'precision':high_precision,'accuracy':high_accuracy},
            'binary_moderate_cutoff':{
                'positive_completed':positive,'normal_completed':normal,
                'recall_tpr':moderate_positive/positive if positive else None,
                'false_positive_rate':moderate_normal/normal if normal else None},
            'positive_low_miss_analysis':{
                'completed':sum(positive_low_buckets.values()),
                'buckets':{name:positive_low_buckets[name] for name in ('near_miss','weak_window','diagnostic_inconsistency')},
                'by_label':{label:{name:positive_low_by_label[label][name] for name in ('near_miss','weak_window','diagnostic_inconsistency')}
                            for label in POSITIVE_LABELS},
                'distributed_moderate_blockers':dict(sorted(positive_low_blockers.items())),
                'definition':'Post-hoc only: near_miss = one remaining distributed-MODERATE blocker with a candidate; weak_window = multiple blockers or no candidate. Never used by production scoring.'},
            'ranking':{'median_research_index':{label:statistics.median(v) if v else None for label,v in indices.items()},
                       'note':'Compare development and holdout distributions before changing categories; this index is not a probability.'},
            'note':'External user labels, not certified ground truth. Failed scans excluded from denominators; insufficient data remains negative. Accuracy is reported only with recall/FPR/specificity/precision because class balance can make accuracy misleading.'}



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
    parser.add_argument('--history-games',type=int,default=200)
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
