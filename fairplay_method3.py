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

VERSION='fairplay-v69-history-context'
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
        from fairplay_method3_coverage import ChronologyCoverage
        collection_status=getattr(api,'fairplay_collection_coverage',None) or {}
        chronology=ChronologyCoverage(collection_status)
        # Evaluate class-local chronological 200-game blocks independently, never
        # dilute a qualifying period with older normal results. Apply an
        # additional across-block search correction to avoid inflated flags.
        from fairplay_outcome_study import _result
        # Split by time class BEFORE batching. Otherwise intervening Blitz
        # games can hide even a twenty-game Rapid run from every batch.
        from fairplay_method3_windows import segments as build_segments
        segments,block_count=build_segments(games,BLOCK_SIZE)
        overall_rows=[]
        candidate_rows=[]
        positions={g.identity:i for i,g in enumerate(games)}
        physical_windows=set()
        for segment_index,segment in enumerate(segments):
            index,block,context,context_start=(segment.block_index,segment.candidates,
                                              segment.context,segment.context_start)
            is_bridge=segment.boundary is not None
            check_deadline(deadline)
            progress((f'Method 3: boundary {segment_index-block_count+1}/{len(segments)-block_count}'
                      if is_bridge else f'Method 3: block {index+1}/{block_count}'))
            player={'player_key':'subject','games':[{
                'identity':g.identity,'ended':g.ended,'rated':g.rated,'time_class':g.time_class,
                'time_control':g.time_control,'player_elo':g.rating,'opponent_elo':g.opponent_rating,
                'score':g.score,'official_accuracy':g.accuracy,'estimated_accuracy':None} for g in context]}
            if not is_bridge:
                overall_rows.extend(player['games'][-len(block):])
                # Fewer than five new games cannot define a standalone
                # qualifying block. Without this check, candidate_tail=None
                # silently re-scans the older context as if it were new data.
                if len(block)<STUDY.minimum_games:
                    continue
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
                boundary=segment.boundary
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
        # The review history is already chronological. Reuse the first
        # 150 valid rated games per control for the account-opening guard.
        opening_rated_by_class={kind:[g for g in games
            if g.rated is True and g.time_class==kind
            and isinstance(g.rating,(int,float)) and not isinstance(g.rating,bool)
            and g.rating>0][:150] for kind in ('rapid','blitz','bullet')}
        rated_by_class={kind:[g.rating for g in games
            if g.rated is True and g.time_class==kind
            and isinstance(g.rating,(int,float)) and not isinstance(g.rating,bool)
            and g.rating>0] for kind in ('rapid','blitz','bullet')}
        bullet_ratings=rated_by_class['bullet']
        other_ratings=rated_by_class['rapid']+rated_by_class['blitz']
        latest_bullet=next((g for g in reversed(games) if g.time_class=='bullet'),None)
        bullet_support=(rating_support(games,[latest_bullet],account_joined=profile.get('joined'),
                                       early_rated=opening_rated_by_class['bullet'])
                        if latest_bullet is not None else None)
        rating_reliability={
            'sparse_bullet_rating':bool(bullet_support and bullet_support['unreliable']),
            'bullet_rated_games':len(bullet_ratings),
            'other_control_rated_games':len(other_ratings),
            'bullet_median_elo':median(bullet_ratings) if bullet_ratings else None,
            'other_control_median_elo':median(other_ratings) if other_ratings else None,
            'latest_bullet_support':bullet_support,
            'rule':'Period-end rated-game support; zero rated anchors make the time-control '
                   'Elo expectation unverified even if no alternate-control rating exists. '
                   'Never replace a time-control Elo with ratings from another control.'}
        rank={'INSUFFICIENT DATA':0,'LOW':1,'MODERATE':2,'HIGH':3,'VERY HIGH':4}
        candidates=[]
        guarded_periods={}
        corrected_result_guards=set()
        rating_cache={}
        opponent_cache={}
        for index,block,context,sub,context_start,is_bridge,by_class in candidate_rows:
            for period in sub['periods']:
                guard=period['search_guard']
                if id(guard) not in corrected_result_guards:
                    local_count=max(1,guard.get('searched_windows',1))
                    raw=guard.get('raw_model_bound')
                    guard['adjusted_model_bound']=min(
                        1.0,raw*window_count if raw is not None else
                        guard['adjusted_model_bound']*window_count/local_count)
                    corrected_result_guards.add(id(guard))
                guard['searched_windows']=window_count
                guard['high_pass']=guard['short_sample_complete'] and guard['adjusted_model_bound']<=.01
                guard['moderate_pass']=guard['short_sample_complete'] and guard['adjusted_model_bound']<=.10
                acc=period['accuracy_search_guard']
                local_count=max(1,acc.get('searched_windows',1))
                raw=acc.get('raw_reference_model_bound')
                acc['adjusted_reference_model_bound']=min(
                    1.0,raw*window_count if raw is not None else
                    acc['adjusted_reference_model_bound']*window_count/local_count)
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
                    rating_cache[rating_key]=rating_support(games,members,account_joined=profile.get('joined'),
                        early_rated=opening_rated_by_class[period['time_class']])
                opponent_key=(period['time_class'],members[0].identity,members[-1].identity)
                if opponent_key not in opponent_cache:
                    opponent_cache[opponent_key]=opponent_support(members,history=games)
                rating=rating_cache[rating_key]
                opponents=opponent_cache[opponent_key]
                tier,limits=independent_results_tier(
                    tier,games=period['games'],rating=rating,opponents=opponents,
                    accuracy_confirmed=accuracy_checks['accuracy_pass'])
                period['chronology_coverage']=chronology.period(members)
                if not period['chronology_coverage']['complete']:
                    limits=tuple(limits)+('incomplete_period_chronology',)
                    if tier in ('HIGH','VERY HIGH'):tier='MODERATE'
                period['rating_support']=rating
                period['opponent_support']=opponents
                period['effective_priority']=tier
                if limits:
                    key=(period['time_class'],members[0].identity,members[-1].identity)
                    guarded_periods.setdefault(key,set()).update(limits)
                    period['classification_guards']=limits
                    period['rating_reliability_downgrade']=any(
                        reason in limits for reason in (
                            'unreliable_time_control_rating',
                            'unverified_time_control_rating'))
                    period['rating_unverified_downgrade']='unverified_time_control_rating' in limits
                basis=('accuracy' if tier=='MODERATE' and not middle['results_review_pass']
                       and not limits else 'results')
                candidates.append((tier,basis,index,block,context,sub,period,context_start,is_bridge))
        # Show only disjoint, qualifying physical periods in the charts.
        # Preserve all LOW/insufficient candidates for diagnostic selection,
        # but never color them as suspicious. Deduplicate block/bridge views.
        qualifying={}
        for tier,_,_,_,context,_,period,_,_ in candidates:
            if tier not in ('MODERATE','HIGH','VERY HIGH'):continue
            members=[g for g in context if g.time_class==period['time_class']]
            first,last=members[period['first_game']-1],members[period['last_game']-1]
            key=(period['time_class'],first.identity,last.identity)
            row={'time_class':period['time_class'],'first_ended':first.ended,
                 'last_ended':last.ended,'priority':tier,'games':period['games']}
            previous=qualifying.get(key)
            if previous is None or rank[tier]>rank[previous['priority']]:
                qualifying[key]=row
        marked=[]
        # Higher priority first; ties prefer larger windows. Never double-shade
        # the same games even if many overlapping search windows qualified.
        for item in sorted(qualifying.values(),key=lambda p:(-rank[p['priority']],-p['games'],p['first_ended'])):
            if any(p['time_class']==item['time_class'] and
                   p['first_ended']<=item['last_ended'] and item['first_ended']<=p['last_ended']
                   for p in marked):continue
            marked.append(item)
        marked.sort(key=lambda p:(p['time_class'],p['first_ended']))
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
        # Displayed class/opponent totals cover the WHOLE collected sample,
        # not just the context of the strongest class-local execution batch.
        from fairplay_outcome_study import _opponent_groups
        subject['class_results']={kind:_result([g for g in overall_rows if g['time_class']==kind])
                                 for kind in ('rapid','blitz','bullet')}
        subject['opponent_groups']=_opponent_groups(overall_rows,{},'official_accuracy',STUDY,{})
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
        from fairplay_method3_accuracy_frequency import historical_signal
        frequency_signals=historical_signal(games)
        # The frequency signal is supporting review triage only: it can
        # promote LOW to MODERATE, never HIGH or VERY HIGH.
        if priority=='LOW' and frequency_signals:
            priority='MODERATE'
            basis='accuracy_frequency'
        selected_period_priority=priority if basis!='accuracy_frequency' else 'LOW'
        rated_count=sum(g.rated is True for g in games)
        valid_accuracy=sum(isinstance(g.accuracy,(int,float)) and not isinstance(g.accuracy,bool)
                           and 0 <= g.accuracy <= 100 for g in games)
        known_opponents=sum(bool(getattr(g,'opponent_username',None)) for g in games)
        known_ratings=sum(isinstance(g.rating,(int,float)) and g.rating>0
                          and isinstance(g.opponent_rating,(int,float)) and g.opponent_rating>0
                          for g in games)
        data_quality={'history_games':len(games),'valid_rating_pairs':known_ratings,
                      'published_accuracy_games':valid_accuracy,
                      'identified_opponents':known_opponents,
                      'skipped_reasons':dict(skipped),
                      'archive_coverage':dict(collection_status),
                      'collection_partial':bool(partial),
                      'note':'Observed-data denominators, not independent evidence.'}
        subject['rated_games']=rated_count
        subject['unrated_games']=len(games)-rated_count
        confidence=('LOW' if partial or len(games)<30 or (chosen and chosen.get('classification_guards'))
                    or (priority in ('HIGH','MODERATE') and chosen and chosen['games']<10)
                    else 'HIGH' if len(games)>=100 else 'MEDIUM')
        reasons=[('This time control has no rated-game anchors in the previous year. Its Elo expectation cannot independently justify HIGH or VERY HIGH.' if chosen and chosen.get('rating_unverified_downgrade') else
                 'Some eligible games inside this period could not be read. The remaining games cannot establish an uninterrupted results sequence; HIGH is capped at MODERATE.' if chosen and 'incomplete_period_chronology' in chosen.get('classification_guards',()) else
                 'Opponent identities are missing in too much of this period to verify diverse opposition. HIGH is capped at MODERATE until that coverage is resolved.' if chosen and 'unverified_opponent_identities' in chosen.get('classification_guards',()) else
                 'The time-control Elo has too few recent rated anchors and differs substantially from other controls. Accuracy alone cannot repair the Elo expectation; HIGH/VERY HIGH is capped.' if chosen and chosen.get('rating_reliability_downgrade') else
                 'The selected period falls in a verified new-account rating calibration ramp; the Elo expected-score baseline was moving rapidly. HIGH is capped at MODERATE without gameplay confirmation.' if chosen and 'new_account_rating_ramp' in chosen.get('classification_guards',()) else
                 'Most games in the period were played against one or two opponents. Even corroborating Accuracy cannot make these outcomes independent; HIGH/VERY HIGH is capped pending human review.' if chosen and 'repeated_opponent_concentration' in chosen.get('classification_guards',()) else
                 ('An exceptional results period also passed a stricter matched historical Accuracy check. This is a review priority, not proof of misconduct.') if priority=='VERY HIGH' else
                 ('A chronological period scored substantially above its Elo-point expectation and passed the history-search guard. '
                  'This experimental results flag is not engine or Maia confirmation.') if priority=='HIGH' else
                 'Official Accuracy exceeded a matched earlier personal reference. Manual review is needed.' if priority=='MODERATE' and basis=='accuracy' else
                 'A chronological period exceeded the results review boundary but did not pass the stricter HIGH search guard.' if priority=='MODERATE' else
                 'No period passed the configured review boundaries; this does not establish fair play.' if priority=='LOW' else
                 'Not enough comparable chronological games with valid ratings/results to assign a tier.')]
        if basis=='accuracy_frequency':
            reasons=['A recent high-Accuracy frequency increase passed a conservative earlier-history comparison against similar-Elo opponents. Exploratory review signal, not proof of misconduct.']
        from fairplay_method3_history import changes
        from fairplay_method3_context import context as period_context
        from fairplay_method3_accuracy_frequency import summarize as accuracy_frequency
        frequency_diagnostics=accuracy_frequency(games)
        history_changes=changes(games,PRIORITY_GAMES)
        selected_context=period_context(games,chosen)
        from collections import Counter
        guard_counts=Counter(reason for reasons in guarded_periods.values() for reason in reasons)
        record={'history_limit':LIMIT,'priority_games':len(games),'block_size':BLOCK_SIZE,'blocks_checked':block_count,
                'period_guard_counts':dict(guard_counts),
                'boundary_bridges_checked':len(segments)-block_count,'segments_checked':len(segments),
                'history_changes':history_changes,'period_context':selected_context,
                'accuracy_frequency':frequency_diagnostics,
                'accuracy_frequency_signals':frequency_signals,
                'rating_reliability':rating_reliability,
                'data_quality':data_quality,
                'rated_games':rated_count,'unrated_games':len(games)-rated_count,
                'priority_basis':basis,'production_enabled':True,
                'period_scope':'Per-time-class chronological 200-game blocks plus 100-game-per-side boundary bridges; globally deduplicated physical-window search correction',
                'searched_windows':window_count,
                'method':'Experimental Elo-results and published-Accuracy triage; not gameplay confirmation or misconduct probability',
                'reference':'Development thresholds, not independent holdout calibration',
                'result_residual_threshold':2.4,'score_excess_threshold':.20,
                'moderate_accuracy_excess':8,'moderate_above_reference_fraction':.60,
                'selected':chosen,'selected_period_priority':selected_period_priority,
                'marked_periods':marked,'subject':subject,'source':'official_accuracy',
                'engine_used':False,'maia_used':False,'timing_scoring':False,
                'accuracy_role':'High Accuracy frequency can promote LOW to MODERATE after conservative same-control earlier-history checks; never promotes HIGH',
                'limits':'Rated and unrated share chronological periods/references, but their Elo reliability is separately audited. Zero rated anchors, cross-control discrepancies and verified new-account rating ramps cap results-backed HIGH/VERY HIGH. Repeated opponents cap these tiers even if Accuracy corroborates. Daily is excluded; only collected live history is searched. Underrating, legitimate improvement, smurfing, rating lag, correlated games, missing archives and selected hot streaks can qualify. VERY HIGH requires both results and matched earlier Accuracy but never proves misconduct.'}
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
