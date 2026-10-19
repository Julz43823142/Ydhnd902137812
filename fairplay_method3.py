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

VERSION='fairplay-v62-history-blocks'
LIMIT=5000
BLOCK_SIZE=200
PRIORITY_GAMES=200
STUDY=replace(STUDY_CONFIG,history_limit=BLOCK_SIZE,minimum_games=5,widths=(5,10,20,50,100,200))


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
            include_recent_all_short_count=LIMIT,time_classes={'rapid','blitz','bullet'},
            progress_detail=True)
        check_deadline(deadline)
        games=sorted((g for g in games if type(g.rated) is bool),key=lambda g:(g.ended,g.identity))[-LIMIT:]
        if not games:raise ReviewError('No eligible live games were found.')
        progress('Method 3: comparing Elo expectations…')
        # Evaluate disjoint chronological 200-game blocks independently, never
        # dilute a qualifying period with older normal results. Apply an
        # additional across-block search correction to avoid inflated flags.
        from fairplay_outcome_study import _result
        blocks=[games[i:i+BLOCK_SIZE] for i in range(0,len(games),BLOCK_SIZE)]
        block_count=len(blocks)
        overall_rows=[]
        candidate_rows=[]
        window_count=0
        for index,block in enumerate(blocks):
            check_deadline(deadline)
            progress(f'Method 3: block {index+1}/{block_count}')
            player={'player_key':'subject','games':[{
                'identity':g.identity,'ended':g.ended,'rated':g.rated,'time_class':g.time_class,
                'time_control':g.time_control,'player_elo':g.rating,'opponent_elo':g.opponent_rating,
                'score':g.score,'official_accuracy':g.accuracy,'estimated_accuracy':None} for g in block]}
            overall_rows.extend(player['games'])
            study=evaluate([player],source='official_accuracy',config=STUDY,
                scenarios=SCENARIOS+DEVELOPMENT_SCENARIOS,rated=None,protect_search=True)
            sub=study['players'][0]
            periods=sub['periods']
            window_count+=len({(p['time_class'],p['first_game'],p['last_game']) for p in periods})
            candidate_rows.append((index,block,sub))
        # The within-block guard already accounts for all windows in that block.
        # Multiplying by the number of tested blocks is a conservative union
        # bound across all blocks, including short or incomplete ones.
        rank={'INSUFFICIENT DATA':0,'LOW':1,'MODERATE':2,'HIGH':3}
        candidates=[]
        for index,block,sub in candidate_rows:
            for period in sub['periods']:
                guard=period['search_guard']
                guard['adjusted_model_bound']=min(1.0,guard['adjusted_model_bound']*block_count)
                guard['searched_windows']=window_count
                guard['high_pass']=guard['short_sample_complete'] and guard['adjusted_model_bound']<=.01
                guard['moderate_pass']=guard['short_sample_complete'] and guard['adjusted_model_bound']<=.10
                acc=period['accuracy_search_guard']
                acc['adjusted_reference_model_bound']=min(1.0,acc['adjusted_reference_model_bound']*block_count)
                acc['searched_windows']=window_count
                acc['moderate_pass']=acc['adjusted_reference_model_bound']<=.10
                for checks in period['scenarios'].values():
                    checks['results_pass']=checks['results_pass'] and guard['high_pass']
                    checks['results_review_pass']=checks.get('results_review_pass',False) and guard['moderate_pass']
                    checks['accuracy_pass']=checks['accuracy_pass'] and acc['moderate_pass']
                    checks['priority']=('HIGH' if checks['results_pass'] and checks['accuracy_pass'] else
                        'MODERATE' if checks['results_review_pass'] or checks['accuracy_pass'] else
                        'LOW' if checks['results_covered'] and checks['accuracy_covered'] else 'INSUFFICIENT DATA')
                middle=period['scenarios']['development_middle']
                accuracy_checks=period['scenarios']['balanced']
                tier=('HIGH' if middle['results_pass'] else
                      'MODERATE' if middle['results_review_pass'] or accuracy_checks['accuracy_pass'] else
                      'LOW' if middle['results_covered'] or accuracy_checks['accuracy_covered'] else 'INSUFFICIENT DATA')
                basis='accuracy' if tier=='MODERATE' and not middle['results_review_pass'] else 'results'
                candidates.append((tier,basis,index,block,sub,period))
        if candidates:
            priority,basis,block_index,selected_block,subject,chosen=max(candidates,
                key=lambda x:(rank[x[0]],x[5]['games'],
                    -(x[5]['search_guard']['adjusted_model_bound'])))
        else:
            priority,basis,block_index,selected_block,subject,chosen=(
                'INSUFFICIENT DATA','results',0,games,{'overall_results':_result(overall_rows),
                'opponent_groups':[],'periods':[]},None)
        subject=dict(subject)
        subject['overall_results']=_result(overall_rows)
        subject['periods']=[period for _,_,sub in candidate_rows for period in sub['periods']]
        # Display the selected block and its local chronological window.
        # Dates and class-relative indexing must use that same block.
        if chosen:
            members=[g for g in selected_block if g.time_class==chosen['time_class']][chosen['first_game']-1:chosen['last_game']]
            chosen['first_ended']=members[0].ended;chosen['last_ended']=members[-1].ended
            chosen['history_block']=block_index+1
        rated_count=sum(g.rated is True for g in games)
        confidence=('LOW' if partial or len(games)<30 or (priority in ('HIGH','MODERATE') and chosen and chosen['games']<10)
                    else 'HIGH' if len(games)>=100 else 'MEDIUM')
        reasons=[('A chronological period scored substantially above its Elo-point expectation and passed the history-search guard. '
                  'This experimental results flag is not engine or Maia confirmation.') if priority=='HIGH' else
                 'Official Accuracy exceeded a matched earlier personal reference. Manual review is needed.' if priority=='MODERATE' and basis=='accuracy' else
                 'A chronological period exceeded the results review boundary but did not pass the stricter HIGH search guard.' if priority=='MODERATE' else
                 'No period passed the configured review boundaries; this does not establish fair play.' if priority=='LOW' else
                 'Not enough comparable chronological games with valid ratings/results to assign a tier.']
        from fairplay_method3_history import changes
        history_changes=changes(games,PRIORITY_GAMES)
        record={'history_limit':LIMIT,'priority_games':len(games),'block_size':BLOCK_SIZE,'blocks_checked':block_count,
                'history_changes':history_changes,
                'rated_games':rated_count,'unrated_games':len(games)-rated_count,
                'priority_basis':basis,'production_enabled':True,
                'period_scope':'Independent chronological 200-game blocks with across-block search correction',
                'searched_windows':window_count,
                'method':'Experimental Elo-results and published-Accuracy triage; not gameplay confirmation or misconduct probability',
                'reference':'Development thresholds, not independent holdout calibration',
                'result_residual_threshold':2.4,'score_excess_threshold':.20,
                'moderate_accuracy_excess':8,'moderate_above_reference_fraction':.60,
                'selected':chosen,'subject':subject,'source':'official_accuracy',
                'engine_used':False,'maia_used':False,'timing_scoring':False,
                'accuracy_role':'Can support MODERATE; strictly earlier same-control/rating-matched personal reference only',
                'limits':'Rated and unrated have identical priority rules and share chronological periods/references. All collected live history is searched. Daily is excluded. Ratings may be inaccurate in either mode. Underrating, legitimate improvement, smurfing, rating lag and selected hot streaks can qualify. Multiple windows are correlated. No VERY HIGH from results alone.'}
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
