"""Live Method 3: uncalibrated Elo-results triage, no engine/model execution.

Official Accuracy is context against strictly earlier matched personal games,
not a population norm. Result HIGH is moderator workload priority, not gameplay
confirmation. Labels and account status never enter this computation.
"""
from dataclasses import replace
import time

from fairplay_config import CONFIG
from fairplay_data import PubAPI, ReviewError, ScanDeadline, check_deadline, collect_games, username
from fairplay_outcome_study import evaluate, SCENARIOS, DEVELOPMENT_SCENARIOS, CONFIG as STUDY_CONFIG

VERSION='fairplay-v59-method3-history-1000'
LIMIT=1000
PRIORITY_GAMES=200
STUDY=replace(STUDY_CONFIG,history_limit=LIMIT,widths=(10,20,50,100,200))


def review(target, progress, config=CONFIG, *, api_factory=PubAPI, cancel=None,
           checkpoint=None, **unused):
    from fairplay_analysis import ReviewResult
    start=time.monotonic()
    deadline=ScanDeadline(start+300,cancel)
    target=username(target)
    api=api_factory(deadline)
    try:
        progress('Fetching profile…')
        profile=api.get(target,profile=True)
        if not isinstance(profile,dict) or username(profile.get('username',''))!=target:
            raise ReviewError('The public profile cannot be verified.')
        collection=replace(config,max_games=LIMIT,history_games=LIMIT,primary_engine_games=LIMIT)
        games,skipped,partial=collect_games(api,target,progress,collection,
            include_unrated=False,include_latest_fifty_short=True,
            include_recent_all_short_count=LIMIT,time_classes={'rapid','blitz','bullet'})
        check_deadline(deadline)
        games=sorted((g for g in games if g.rated is True),key=lambda g:(g.ended,g.identity))[-LIMIT:]
        if not games:raise ReviewError('No eligible rated live games were found.')
        progress('Method 3: comparing Elo expectations…')
        # No cross-account private ledger, invented Accuracy or validation cohort.
        player={'player_key':'subject','games':[{
            'identity':g.identity,'ended':g.ended,'rated':g.rated,'time_class':g.time_class,
            'time_control':g.time_control,'player_elo':g.rating,'opponent_elo':g.opponent_rating,
            'score':g.score,'official_accuracy':g.accuracy,'estimated_accuracy':None} for g in games]}
        study=evaluate([player],source='official_accuracy',config=STUDY,
            scenarios=SCENARIOS+DEVELOPMENT_SCENARIOS,candidate_tail=PRIORITY_GAMES)
        subject=study['players'][0]
        middle=subject['ablations']['results_only']['development_middle']
        accuracy_support=subject['ablations']['accuracy_only']['balanced']
        priority=('HIGH' if middle['priority']=='HIGH' else 'MODERATE' if accuracy_support['priority']=='HIGH'
                  else middle['priority'])
        chosen=accuracy_support['selected'] if priority=='MODERATE' else middle['selected']
        confidence=('LOW' if partial or len(games)<30 else 'HIGH' if len(games)>=100 else 'MEDIUM')
        reasons=[('A chronological rated period scored substantially above its Elo-point expectation. '
                  'This experimental results-only flag is not engine or Maia confirmation.') if priority=='HIGH' else
                 'Official Accuracy exceeded the matched earlier personal reference; results did not pass HIGH. Manual review is needed.' if priority=='MODERATE' else
                 'No period passed the configured Elo-results boundaries; this does not establish fair play.' if priority=='LOW' else
                 'Not enough comparable chronological games with valid ratings/results to assign a tier.']
        from fairplay_method3_history import changes
        history_changes=changes(games,PRIORITY_GAMES)
        record={'history_limit':LIMIT,'priority_games':min(PRIORITY_GAMES,len(games)),
                'history_changes':history_changes,'production_enabled':True,'method':'Experimental Elo-results triage; not gameplay confirmation or misconduct probability',
                'reference':'Development thresholds, not independent holdout calibration',
                'result_residual_threshold':2.4,'score_excess_threshold':.20,
                'moderate_accuracy_excess':8,'moderate_above_reference_fraction':.60,
                'selected':chosen,'subject':subject,'source':'official_accuracy',
                'engine_used':False,'maia_used':False,'timing_scoring':False,
                'accuracy_role':'Can support MODERATE; strictly earlier same-control/rating-matched personal reference only',
                'limits':'Underrating, legitimate improvement, smurfing, rating lag and selected hot streaks can qualify. Multiple windows are correlated. No VERY HIGH from results alone.'}
        # Offline evaluate's cross-player groups have no other players here. Attach
        # the earlier personal references by period, never fabricate a group norm.
        progress('Method 3: building graphs and report…')
        check_deadline(deadline)
        return ReviewResult(username=target,games=games,selected_games=len(games),skipped=skipped,
            partial=partial,engine='Not used — Method 3',totals={},classes={},performance={},context={},
            families={},priority=priority,confidence=confidence,reasons=reasons,deep_confirmed=False,
            deep_coverage={'games':0},elapsed=time.monotonic()-start,version=VERSION,
            coverage={'primary_collected':len(games),'fast_scanned':0,'deep_reviewed':0,'used':len(games),
                      'requested_primary_limit':LIMIT},diagnostics={'method3':record})
    finally:
        if callable(getattr(api,'close',None)):api.close()


def observations(games):
    # Reuse only the descriptive renderer input, never _accuracy/engine metrics.
    from statistics import median
    games=sorted(games,key=lambda g:(g.ended,g.identity))
    recent_ids={g.identity for g in games[-PRIORITY_GAMES:]}
    grouped={kind:[] for kind in ('rapid','blitz','bullet')}
    for game in sorted(games,key=lambda g:(g.ended,g.identity)):
        times=[d.think for d in game.decisions if d.clock_valid and d.think is not None and d.think>=0]
        group=grouped[game.time_class]
        group.append({'game':len(group)+1,'control':game.time_control,'rated':game.rated,
            'official_accuracy':game.accuracy,'estimated_accuracy':None,'opponent_elo':game.opponent_rating,
            'priority_sample':game.identity in recent_ids,
            'player_elo':game.rating,'median_think_seconds':median(times) if times else None,
            'clock_moves':len(times),'moves':len(game.decisions)})
    return grouped
