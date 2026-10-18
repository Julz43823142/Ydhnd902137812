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

VERSION='fairplay-v60-method3-unrated-context'
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
            include_unrated=True,include_latest_fifty_short=True,
            include_recent_all_short_count=LIMIT,time_classes={'rapid','blitz','bullet'})
        check_deadline(deadline)
        games=sorted((g for g in games if type(g.rated) is bool),key=lambda g:(g.ended,g.identity))[-LIMIT:]
        if not games:raise ReviewError('No eligible live games were found.')
        progress('Method 3: comparing Elo expectations…')
        # No cross-account private ledger, invented Accuracy or validation cohort.
        player={'player_key':'subject','games':[{
            'identity':g.identity,'ended':g.ended,'rated':g.rated,'time_class':g.time_class,
            'time_control':g.time_control,'player_elo':g.rating,'opponent_elo':g.opponent_rating,
            'score':g.score,'official_accuracy':g.accuracy,'estimated_accuracy':None} for g in games]}
        def cohort(rated):
            study=evaluate([player],source='official_accuracy',config=STUDY,
                scenarios=SCENARIOS+DEVELOPMENT_SCENARIOS,candidate_tail=PRIORITY_GAMES,rated=rated)
            subject=study['players'][0]
            middle=subject['ablations']['results_only']['development_middle']
            accuracy=subject['ablations']['accuracy_only']['balanced']
            priority=('HIGH' if middle['priority']=='HIGH' else 'MODERATE' if accuracy['priority']=='HIGH'
                      else middle['priority'])
            selected=accuracy['selected'] if priority=='MODERATE' else middle['selected']
            return subject,priority,selected
        subject,priority,chosen=cohort(True)
        unrated_subject,unrated_priority,unrated_selected=cohort(False)
        # Unrated ratings do not update with these games. An Elo discrepancy can
        # justify a closer look, but cannot independently produce HIGH.
        basis='rated_accuracy' if priority=='MODERATE' else 'rated_results'
        if priority in ('LOW','INSUFFICIENT DATA') and unrated_priority in ('HIGH','MODERATE'):
            priority='MODERATE';chosen=unrated_selected;basis='unrated_context'
        elif priority=='INSUFFICIENT DATA' and unrated_priority=='LOW':
            priority='LOW';chosen=unrated_selected;basis='unrated_context'
        rated_count=sum(g.rated is True for g in games)
        confidence=('LOW' if partial or rated_count<30 or basis=='unrated_context'
                    else 'HIGH' if rated_count>=100 else 'MEDIUM')
        reasons=[('A chronological rated period scored substantially above its Elo-point expectation. '
                  'This experimental results-only flag is not engine or Maia confirmation.') if priority=='HIGH' else
                 'An unrated period exceeded a results or matched personal Accuracy boundary. Unrated ratings may be stale; this supports a closer look, not HIGH.' if priority=='MODERATE' and basis=='unrated_context' else
                 'Official Accuracy exceeded the matched earlier personal reference; results did not pass HIGH. Manual review is needed.' if priority=='MODERATE' else
                 'No period passed the configured review boundaries; this does not establish fair play.' if priority=='LOW' else
                 'Not enough comparable chronological games with valid ratings/results to assign a tier.']
        from fairplay_method3_history import changes
        history_changes=changes([g for g in games if g.rated is True],PRIORITY_GAMES)
        record={'history_limit':LIMIT,'priority_games':min(PRIORITY_GAMES,rated_count),
                'history_changes':history_changes,
                'rated_games':rated_count,'unrated_games':len(games)-rated_count,
                'unrated_subject':unrated_subject,
                'unrated_priority':'MODERATE' if unrated_priority=='HIGH' else unrated_priority,
                'priority_basis':basis,'unrated_selected':unrated_selected,
                'unrated_history_changes':changes([g for g in games if g.rated is False],PRIORITY_GAMES),'production_enabled':True,'method':'Experimental Elo-results and published-Accuracy triage; not gameplay confirmation or misconduct probability',
                'reference':'Development thresholds, not independent holdout calibration',
                'result_residual_threshold':2.4,'score_excess_threshold':.20,
                'moderate_accuracy_excess':8,'moderate_above_reference_fraction':.60,
                'selected':chosen,'subject':subject,'source':'official_accuracy',
                'engine_used':False,'maia_used':False,'timing_scoring':False,
                'accuracy_role':'Can support MODERATE; strictly earlier same-control/rating-matched personal reference only',
                'limits':'Rated/unrated are compared separately. Unrated ratings may be stale and cannot independently produce HIGH. Daily is excluded. Underrating, legitimate improvement, smurfing, rating lag and selected hot streaks can qualify. Multiple windows are correlated. No VERY HIGH from results alone.'}
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
