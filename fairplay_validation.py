"""Local calibration only. Production modules never import or read this file.

External labels are compared AFTER an analytical priority is frozen. No names,
reports or accusations are written/printed: output contains aggregate counts.
Use explicitly labelled trusted normal accounts; 'not banned' is not a label.
"""
import argparse
import json
import math
import os
from contextlib import contextmanager
import statistics
from collections import Counter
from pathlib import Path
from fairplay_data import username, ReviewError

LABELS = ('known_fair_play_closed', 'trusted_normal', 'positive', 'holdout_positive', 'holdout_normal')
PRIORITIES = ('LOW', 'MODERATE', 'HIGH', 'VERY HIGH', 'INSUFFICIENT DATA')
POSITIVE_LABELS = ('known_fair_play_closed', 'positive', 'holdout_positive')
NORMAL_LABELS = ('trusted_normal', 'holdout_normal')


def analytical_priority(result):
    """Configured worklist promotion is not a detector validation success."""
    diagnostics=getattr(result,'diagnostics',{})
    depth=(diagnostics.get('v51_shadow',{}).get('review_priority',{})
           if isinstance(diagnostics,dict) else {})
    return str(depth.get('gameplay_priority',getattr(result,'priority','')))


def ranking_summary(indices):
    """Pairwise ordering on each partition; never tune against pooled holdout.

    AUC is the fraction of positive/normal score pairs ordered correctly, with
    half credit for ties. It is descriptive on this supplied cohort, not a
    misconduct probability or evidence of population accuracy. Missing scores
    remain missing rather than becoming artificial zeroes.
    """
    output={}
    for partition,positive_labels,normal_labels in (
            ('development',('known_fair_play_closed','positive'),('trusted_normal',)),
            ('holdout',('holdout_positive',),('holdout_normal',))):
        positive=[v for label in positive_labels for v in indices[label]]
        normal=[v for label in normal_labels for v in indices[label]]
        pairs=len(positive)*len(normal)
        wins=sum(p>n for p in positive for n in normal)
        ties=sum(p==n for p in positive for n in normal)
        output[partition]={'positive_scores':len(positive),'normal_scores':len(normal),
                           'pairs':pairs,'correctly_ordered_pairs':wins,'tied_pairs':ties,
                           'pairwise_auc':(wins+.5*ties)/pairs if pairs else None}
    return output


def positive_low_bucket(result):
    """Post-hoc miss taxonomy. Never called before analytical priority freezes.

    Near-miss means the production distributed-MODERATE route is blocked by
    exactly one remaining gate. More than one blocker (or no usable candidate)
    is treated as a weak-window/multi-blocked miss. This classification is for
    aggregate validation only and never feeds thresholds or production scoring.
    """
    priority=analytical_priority(result)
    if priority!='LOW':return None,()
    diagnostics=getattr(result,'diagnostics',{})
    depth=(diagnostics.get('v51_shadow',{}).get('review_priority',{})
           if isinstance(diagnostics,dict) else {})
    if depth:
        blocks=[row['closest_fixed_block'] for row in depth.get('classes',{}).values()
                if row.get('closest_fixed_block')]
        closest=min(blocks,key=lambda row:(len(row.get('blockers',())),
                    -row.get('summary',{}).get('information',0)),default=None)
        if not closest:
            return 'weak_window',('no complete fixed gameplay block',)
        blockers=tuple(closest.get('blockers',()))
        if closest.get('passed'):
            return 'diagnostic_inconsistency',blockers
        return ('near_miss' if len(blockers)==1 else 'weak_window'),blockers
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


def validated_cases(cases, partition='all'):
    """Validate the entire private cohort BEFORE invoking any analysis.

    Repeated players, including case variants, must not cross development and
    holdout or receive extra weight through repeated scans.
    """
    if partition not in ('all', 'development', 'holdout'):
        raise ValueError('Invalid validation partition.')
    if not isinstance(cases, list):raise ValueError('Expected a case list.')
    seen=set();selected=[]
    for case in cases:
        if not isinstance(case,dict) or case.get('label') not in LABELS:
            raise ValueError('Use an explicit validation label.')
        try:target=username(case.get('username',''))
        except ReviewError as error:raise ValueError('Invalid validation username.') from error
        if target in seen:raise ValueError('Duplicate player in validation cohort.')
        seen.add(target)
        holdout=case['label'].startswith('holdout_')
        if partition=='all' or (partition=='holdout')==holdout:
            selected.append({'username':target,'label':case['label']})
    if not selected:raise ValueError('No cases in the requested partition.')
    return selected


@contextmanager
def local_production_profile(profile='production-local'):
    """CLI-only production selection/search settings, without remote dispatch.

    Production-local explicitly selects the deployed depth-only entry,
    rather than assuming fairplay_analysis.review observes FAIRPLAY_V51.
    Legacy production remains an explicit comparison profile. Removing ALL
    dispatch credentials requires the existing exact local compute fallback.
    Never call this context manager from the running Discord process.
    """
    if profile not in ('production-local','candidate-local','legacy-production-local','screening','method3-local'):raise ValueError('Invalid analysis profile.')
    # A Method 3 benchmark must not silently inherit depth/dispatch flags.
    # Method 3 uses only public historical PGNs and is deliberately engine-free.
    full=profile not in ('screening','method3-local')
    flags={'GITHUB_ACTIONS':'true' if full else 'false',
           'FAIRPLAY_METHOD3':'1' if profile=='method3-local' else '0',
           'FAIRPLAY_V51':'1' if profile in ('production-local','candidate-local') else '0',
           **{key:'1' if full else '0' for key in (
               'FAIRPLAY_FULL_DEPTH18','FAIRPLAY_V21','FAIRPLAY_DISTRIBUTED','FAIRPLAY_REQUIRE_MAIA')}}
    credentials=('GITHUB_TOKEN','GH_TOKEN','DISCORD_TOKEN',
                 'FAIRPLAY_DISTRIBUTED_KEY','FAIRPLAY_CHECKPOINT_KEY')
    keys=(*flags,*credentials)
    previous={key:os.environ.get(key) for key in keys}
    try:
        for key in credentials:os.environ.pop(key,None)
        os.environ.update(flags)
        yield
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def analysis_config(profile, history_games=None):
    from dataclasses import replace
    from fairplay_config import CONFIG
    if profile in ('production-local','candidate-local','legacy-production-local','method3-local'):
        if history_games is not None and history_games!=CONFIG.history_games:
            raise ValueError('Production profile cannot override the history quota.')
        return CONFIG  # equality is required by production v21 entry guards
    if profile!='screening':raise ValueError('Invalid analysis profile.')
    return replace(CONFIG,history_games=max(1,min(500,200 if history_games is None else history_games)))


def analyze_target(target, profile, config, **kwargs):
    """Match the deployed entry point explicitly; labels are never arguments.

    fairplay_analysis.review is v50 and does not delegate to the depth-only
    switch. Previously the CLI could claim production equivalence while testing v50.
    """
    if profile=='method3-local':
        from fairplay_method3 import review
    elif profile in ('production-local','candidate-local'):
        from fairplay_v51_shadow import review
        if profile == 'candidate-local':
            kwargs['candidate'] = True
    elif profile in ('legacy-production-local', 'screening'):
        from fairplay_analysis import review
    else:
        raise ValueError('Invalid analysis profile.')
    return review(target, lambda stage: None, config, **kwargs)


def evaluate_cases(cases, analyze):
    cases=validated_cases(cases)
    counts = {label:Counter() for label in LABELS}
    worklist_counts = {label:Counter() for label in LABELS}
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
            frozen_priority = analytical_priority(result)
            frozen_worklist = str(result.priority)
            if frozen_priority not in PRIORITIES or frozen_worklist not in PRIORITIES:
                raise ValueError('Invalid review priority.')
        except Exception:
            failures[case['label']] += 1
            continue  # no private target/error detail reaches stdout/public logs
        counts[case['label']][frozen_priority] += 1
        worklist_counts[case['label']][frozen_worklist] += 1
        diagnostics=getattr(result,'diagnostics',{})
        index=diagnostics.get('gameplay',{}).get('research_evidence_index') if isinstance(diagnostics,dict) else None
        if (isinstance(index,(int,float)) and not isinstance(index,bool)
                and math.isfinite(index)):
            indices[case['label']].append(index)
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
    completed=positive+normal
    abstentions=sum(counts[label]['INSUFFICIENT DATA'] for label in LABELS)
    positive_abstained=sum(counts[label]['INSUFFICIENT DATA'] for label in POSITIVE_LABELS)
    normal_abstained=sum(counts[label]['INSUFFICIENT DATA'] for label in NORMAL_LABELS)
    positive_decided=positive-positive_abstained
    normal_decided=normal-normal_abstained
    positive_requested=sum(case['label'] in POSITIVE_LABELS for case in cases)
    normal_requested=sum(case['label'] in NORMAL_LABELS for case in cases)
    return {'coverage':{'requested':len(cases),'completed':completed,
                        'failed':sum(failures.values()),'insufficient_data':abstentions,
                        'completion_fraction':completed/len(cases)},
            'cases':{label:{p:counts[label][p] for p in PRIORITIES} for label in LABELS},
            'worklist_cases':{label:{p:worklist_counts[label][p] for p in PRIORITIES} for label in LABELS},
            'priority_metric_basis':'Gameplay assessment; configured Accuracy worklist promotions are reported separately and never counted as improved detection.',
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
            'decided_high_cutoff':{
                'positive_decided':positive_decided,'normal_decided':normal_decided,
                'positive_abstained':positive_abstained,'normal_abstained':normal_abstained,
                'recall_tpr':high_positive/positive_decided if positive_decided else None,
                'false_positive_rate':high_normal/normal_decided if normal_decided else None,
                'accuracy':(high_positive+normal_decided-high_normal)/(positive_decided+normal_decided)
                    if positive_decided+normal_decided else None},
            'end_to_end_high_cutoff':{
                'positive_requested':positive_requested,'normal_requested':normal_requested,
                'positive_failed':sum(failures[label] for label in POSITIVE_LABELS),
                'normal_failed':sum(failures[label] for label in NORMAL_LABELS),
                'positive_high_fraction':high_positive/positive_requested if positive_requested else None,
                'normal_high_fraction':high_normal/normal_requested if normal_requested else None},
            'positive_low_miss_analysis':{
                'completed':sum(positive_low_buckets.values()),
                'buckets':{name:positive_low_buckets[name] for name in ('near_miss','weak_window','diagnostic_inconsistency')},
                'by_label':{label:{name:positive_low_by_label[label][name] for name in ('near_miss','weak_window','diagnostic_inconsistency')}
                            for label in POSITIVE_LABELS},
                'distributed_moderate_blockers':dict(sorted(positive_low_blockers.items())),
                'definition':'Post-hoc only: near_miss = one remaining distributed-MODERATE blocker with a candidate; weak_window = multiple blockers or no candidate. Never used by production scoring.'},
            'ranking':{'median_research_index':{label:statistics.median(v) if v else None for label,v in indices.items()},
                       'partitions':ranking_summary(indices),
                       'note':'Higher research index means more review evidence, not probability. Development and holdout are separate; missing/invalid indices do not become zeroes. Small cohort AUC is descriptive, not general accuracy.'},
            'note':'External user labels, not certified ground truth. Legacy binary cutoffs exclude failed scans and treat insufficient data as non-HIGH. Decided metrics exclude abstentions; end-to-end fractions retain every requested case. Interpret all metrics with coverage and class balance, never accuracy alone.'}



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
    parser.add_argument('--history-games',type=int,default=None)
    parser.add_argument('--profile',choices=('screening','production-local','candidate-local','legacy-production-local','method3-local'),default='screening',
                        help='Production-local: depth-only route. Method3-local: exact results/Accuracy-only live route, no engine. Candidate-local: unvalidated research scoring. Legacy-production-local: v50. Screening: fixed-node detector. Exact depth scans may take hours.')
    parser.add_argument('--partition',choices=('all','development','holdout'),default='development')
    args=parser.parse_args()
    try:config=analysis_config(args.profile,args.history_games)
    except ValueError:parser.exit(2,'Invalid analysis profile or history quota.\n')
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
        return analyze_target(target,args.profile,config,**kwargs)
    try:
        private_input_path(args.cases)
        if args.offline_dir:private_input_path(args.offline_dir)
        cases=json.loads(args.cases.read_text())
        cases=validated_cases(cases,args.partition)
        with local_production_profile(args.profile):summary=evaluate_cases(cases,analyze)
        summary['validation_profile']={'name':args.profile,'partition':args.partition,
            'matches_deployed_selection_and_budgets':args.profile in ('production-local','method3-local'),
            'route_scope':('method3_results_accuracy_no_engine' if args.profile=='method3-local' else
                           'depth_engine_or_legacy'),
            'entry_point':('fairplay_method3.review' if args.profile=='method3-local' else
                           'fairplay_v51_shadow.review' if args.profile in ('production-local','candidate-local')
                           else 'fairplay_analysis.review'),
            'remote_dispatch_allowed':False,
            'note':'User-provided account labels are not game-level proof. Ten accounts do not establish general accuracy.'}
    except (ValueError,OSError,TypeError):
        parser.exit(2,'Validation input could not be read. No case details were logged.\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
