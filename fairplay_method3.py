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

VERSION='fairplay-v64-search-reliability'
LIMIT=5000
BLOCK_SIZE=200
PRIORITY_GAMES=200
STUDY=replace(STUDY_CONFIG,history_limit=2*BLOCK_SIZE,minimum_games=5,widths=(5,10,20,50,100,200))


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
        # Evaluate disjoint chronological 200-game blocks independently, never
        # dilute a qualifying period with older normal results. Apply an
        # additional across-block search correction to avoid inflated flags.
        from fairplay_outcome_study import _result
        blocks=[games[i:i+BLOCK_SIZE] for i in range(0,len(games),BLOCK_SIZE)]
        block_count=len(blocks)
        # A streak split by an arbitrary 200-game boundary should not be
        # invisible. Search a short, overlapping bridge around each boundary,
        # with a full preceding block available as Accuracy reference.
        BRIDGE_RADIUS=50
        segments=[]
        for index,block in enumerate(blocks):
            start=index*BLOCK_SIZE
            context_start=max(0,start-BLOCK_SIZE)
            segments.append((index,block,games[context_start:start+len(block)],
                             context_start,False))
        for boundary in range(BLOCK_SIZE,len(games),BLOCK_SIZE):
            start=boundary-BRIDGE_RADIUS
            end=min(len(games),boundary+BRIDGE_RADIUS)
            context_start=max(0,start-BLOCK_SIZE)
            segments.append((boundary//BLOCK_SIZE-1,games[start:end],
                             games[context_start:end],context_start,True))
        overall_rows=[]
        candidate_rows=[]
        positions={g.identity:i for i,g in enumerate(games)}
        physical_windows=set()
        for segment_index,(index,block,context,context_start,is_bridge) in enumerate(segments):
            check_deadline(deadline)
            progress((f'Method 3: boundary {segment_index-block_count+1}/{len(segments)-block_count}'
                      if is_bridge else f'Method 3: block {index+1}/{block_count}'))
            player={'player_key':'subject','games':[{
                'identity':g.identity,'ended':g.ended,'rated':g.rated,'time_class':g.time_class,
                'time_control':g.time_control,'player_elo':g.rating,'opponent_elo':g.opponent_rating,
                'score':g.score,'official_accuracy':g.accuracy,'estimated_accuracy':None} for g in context]}
            if not is_bridge:
                overall_rows.extend(player['games'][-len(block):])
            study=evaluate([player],source='official_accuracy',config=STUDY,
                scenarios=SCENARIOS+DEVELOPMENT_SCENARIOS,rated=None,
                candidate_tail=len(block) if len(block)>=STUDY.minimum_games else None,
                protect_search=True)
            sub=study['players'][0]
            # Count unique PHYSICAL windows globally, not duplicated references
            # or the same interval discovered in both a block and a bridge.
            by_class={kind:[g for g in context if g.time_class==kind]
                      for kind in ('rapid','blitz','bullet')}
            if is_bridge:
                boundary=(index+1)*BLOCK_SIZE
                sub['periods']=[p for p in sub['periods']
                    if positions[by_class[p['time_class']][p['first_game']-1].identity]
                       < boundary <=
                       positions[by_class[p['time_class']][p['last_game']-1].identity]]
            for period in sub['periods']:
                members=by_class[period['time_class']]
                physical_windows.add((period['time_class'],
                    members[period['first_game']-1].identity,
                    members[period['last_game']-1].identity))
            candidate_rows.append((index,block,context,sub,context_start,is_bridge,by_class))
        window_count=len(physical_windows)
        # Each within-segment Chernoff/reference guard has already been
        # Bonferroni-adjusted for THAT segment's physical windows. Convert it
        # to the exact, deduplicated TOTAL windows across all segments.
        # Multiplication by the block count is not sufficient when mixed time
        # controls make segments contain very different numbers of windows.
        from statistics import median
        from fairplay_method3_reliability import (
            rating_support, opponent_support, independent_results_tier)
        rated_by_class={kind:[g.rating for g in games
            if g.rated is True and g.time_class==kind
            and isinstance(g.rating,(int,float)) and not isinstance(g.rating,bool)
            and g.rating>0] for kind in ('rapid','blitz','bullet')}
        bullet_ratings=rated_by_class['bullet']
        other_ratings=rated_by_class['rapid']+rated_by_class['blitz']
        latest_bullet=next((g for g in reversed(games) if g.time_class=='bullet'),None)
        bullet_support=(rating_support(games,[latest_bullet])
                        if latest_bullet is not None else None)
        rating_reliability={
            'sparse_bullet_rating':bool(bullet_support and bullet_support['unreliable']),
            'bullet_rated_games':len(bullet_ratings),
            'other_control_rated_games':len(other_ratings),
            'bullet_median_elo':median(bullet_ratings) if bullet_ratings else None,
            'other_control_median_elo':median(other_ratings) if other_ratings else None,
            'latest_bullet_support':bullet_support,
            'rule':'Prior-year per-period rated-game support; never replace a time-control Elo '
                   'with ratings from another control. Long sparse-rating streaks require '
                   'manual review rather than a results-only HIGH.'}
        rank={'INSUFFICIENT DATA':0,'LOW':1,'MODERATE':2,'HIGH':3,'VERY HIGH':4}
        candidates=[]
        corrected_result_guards=set()
        rating_cache={}
        opponent_cache={}
        for index,block,context,sub,context_start,is_bridge,by_class in candidate_rows:
            for period in sub['periods']:
                guard=period['search_guard']
                if id(guard) not in corrected_result_guards:
                    local_count=max(1,guard.get('searched_windows',1))
                    guard['adjusted_model_bound']=min(
                        1.0,guard['adjusted_model_bound']*window_count/local_count)
                    corrected_result_guards.add(id(guard))
                guard['searched_windows']=window_count
                guard['high_pass']=guard['short_sample_complete'] and guard['adjusted_model_bound']<=.01
                guard['moderate_pass']=guard['short_sample_complete'] and guard['adjusted_model_bound']<=.10
                acc=period['accuracy_search_guard']
                local_count=max(1,acc.get('searched_windows',1))
                acc['adjusted_reference_model_bound']=min(
                    1.0,acc['adjusted_reference_model_bound']*window_count/local_count)
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
                very_high=(middle['results_pass'] and accuracy_checks['accuracy_pass']
                           and period['games']>=20
                           and guard['adjusted_model_bound']<=.001
                           and acc['adjusted_reference_model_bound']<=.01)
                tier=('VERY HIGH' if very_high else
                      'HIGH' if middle['results_pass'] else
                      'MODERATE' if middle['results_review_pass'] or accuracy_checks['accuracy_pass'] else
                      'LOW' if middle['results_covered'] or accuracy_checks['accuracy_covered'] else 'INSUFFICIENT DATA')
                members=by_class[period['time_class']][period['first_game']-1:period['last_game']]
                rating_key=(period['time_class'],members[-1].identity)
                if rating_key not in rating_cache:
                    rating_cache[rating_key]=rating_support(games,members)
                opponent_key=(period['time_class'],members[0].identity,members[-1].identity)
                if opponent_key not in opponent_cache:
                    opponent_cache[opponent_key]=opponent_support(members,history=games)
                rating=rating_cache[rating_key]
                opponents=opponent_cache[opponent_key]
                tier,limits=independent_results_tier(
                    tier,games=period['games'],rating=rating,opponents=opponents,
                    accuracy_confirmed=accuracy_checks['accuracy_pass'])
                period['rating_support']=rating
                period['opponent_support']=opponents
                if limits:
                    period['classification_guards']=limits
                    period['rating_reliability_downgrade']='unreliable_time_control_rating' in limits
                basis=('accuracy' if tier=='MODERATE' and not middle['results_review_pass']
                       and not limits else 'results')
                candidates.append((tier,basis,index,block,context,sub,period,context_start,is_bridge))
        if candidates:
            # Strong HIGH evidence wins; when nothing qualifies, display the
            # widest covered LOW period rather than an arbitrary five-game slice.
            def candidate_key(row):
                tier, period = row[0], row[6]
                bound=period['search_guard']['adjusted_model_bound']
                accuracy_bound=period['accuracy_search_guard']['adjusted_reference_model_bound']
                secondary=(period['games'] if tier in ('LOW','INSUFFICIENT DATA') else
                           -bound if tier in ('HIGH','VERY HIGH') else -min(bound,accuracy_bound))
                return (rank[tier],secondary,period['games'])
            priority,basis,block_index,selected_block,selected_context,subject,chosen,selected_context_start,is_bridge=max(candidates,
                key=candidate_key)
        else:
            priority,basis,block_index,selected_block,selected_context,subject,chosen,selected_context_start,is_bridge=(
                'INSUFFICIENT DATA','results',0,games,games,{'overall_results':_result(overall_rows),
                'opponent_groups':[],'periods':[]},None,0,False)
        subject=dict(subject)
        subject['overall_results']=_result(overall_rows)
        # Keep only the selected block's evidence in the Discord payload.
        # All blocks were scored above; serializing every candidate window
        # would make a 5000-game result needlessly large.
        subject['periods']=subject['periods'][:]
        # Display the selected block and its local chronological window.
        # Dates and class-relative indexing must use that same block.
        if chosen:
            chosen=dict(chosen)
            members=[g for g in selected_context if g.time_class==chosen['time_class']][chosen['first_game']-1:chosen['last_game']]
            chosen['first_ended']=members[0].ended;chosen['last_ended']=members[-1].ended
            # Class-relative indices in a single block need the offset of all
            # older games of that time class for the whole-history display.
            class_offset=sum(g.time_class==chosen['time_class'] for g in games[:selected_context_start])
            chosen['first_game']+=class_offset
            chosen['last_game']+=class_offset
            chosen['history_block']=block_index+1
            chosen['history_segment']='boundary' if is_bridge else 'block'
        rated_count=sum(g.rated is True for g in games)
        subject['rated_games']=rated_count
        subject['unrated_games']=len(games)-rated_count
        confidence=('LOW' if partial or len(games)<30 or (chosen and chosen.get('classification_guards'))
                    or (priority in ('HIGH','MODERATE') and chosen and chosen['games']<10)
                    else 'HIGH' if len(games)>=100 else 'MEDIUM')
        reasons=[('The time-control Elo has too few recent rated anchors and differs substantially from other controls. Results alone cannot justify HIGH here.' if chosen and chosen.get('rating_reliability_downgrade') else
                 'Most games in the period were played against one or two opponents. Their outcomes cannot justify independent-results HIGH without further review.' if chosen and 'repeated_opponent_concentration' in chosen.get('classification_guards',()) else
                 ('An exceptional results period also passed a stricter matched historical Accuracy check. This is a review priority, not proof of misconduct.') if priority=='VERY HIGH' else
                 ('A chronological period scored substantially above its Elo-point expectation and passed the history-search guard. '
                  'This experimental results flag is not engine or Maia confirmation.') if priority=='HIGH' else
                 'Official Accuracy exceeded a matched earlier personal reference. Manual review is needed.' if priority=='MODERATE' and basis=='accuracy' else
                 'A chronological period exceeded the results review boundary but did not pass the stricter HIGH search guard.' if priority=='MODERATE' else
                 'No period passed the configured review boundaries; this does not establish fair play.' if priority=='LOW' else
                 'Not enough comparable chronological games with valid ratings/results to assign a tier.')]
        from fairplay_method3_history import changes
        history_changes=changes(games,PRIORITY_GAMES)
        record={'history_limit':LIMIT,'priority_games':len(games),'block_size':BLOCK_SIZE,'blocks_checked':block_count,
                'boundary_bridges_checked':len(segments)-block_count,'segments_checked':len(segments),
                'history_changes':history_changes,'rating_reliability':rating_reliability,
                'rated_games':rated_count,'unrated_games':len(games)-rated_count,
                'priority_basis':basis,'production_enabled':True,
                'period_scope':'Chronological 200-game blocks plus overlapping boundary bridges; globally deduplicated physical-window search correction',
                'searched_windows':window_count,
                'method':'Experimental Elo-results and published-Accuracy triage; not gameplay confirmation or misconduct probability',
                'reference':'Development thresholds, not independent holdout calibration',
                'result_residual_threshold':2.4,'score_excess_threshold':.20,
                'moderate_accuracy_excess':8,'moderate_above_reference_fraction':.60,
                'selected':chosen,'subject':subject,'source':'official_accuracy',
                'engine_used':False,'maia_used':False,'timing_scoring':False,
                'accuracy_role':'Can support MODERATE; strictly earlier same-control/rating-matched personal reference only',
                'limits':'Rated and unrated have identical priority rules and share chronological periods/references. All collected live history is searched. Daily is excluded. Ratings may be inaccurate in either mode. Underrating, legitimate improvement, smurfing, rating lag and selected hot streaks can qualify. Multiple windows are correlated. VERY HIGH requires both stringent results and matched earlier Accuracy checks; neither establishes misconduct.'}
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
