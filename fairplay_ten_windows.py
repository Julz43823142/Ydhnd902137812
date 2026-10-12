"""Accuracy-triggered ten-game deep review with independent gameplay HIGH gate.

Screen every overlapping ten-game window of the latest 250 rated OR unrated
blitz/rapid games available in the 500-game primary sample. Keep the two
populations separate; a low rated baseline cannot be transferred to unrated. Accuracy only chooses complete
windows for deeper work. HIGH requires paired Stockfish and Maia move evidence;
Accuracy, Elo, win streaks and clock patterns never directly promote priority.
"""
from __future__ import annotations

import math
import statistics

from fairplay_config import CONFIG
from fairplay_streak import _accuracy
from fairplay_confirmation import paired_quality_confirmation
from fairplay_human import period_summary
from fairplay_burst import _verified_entry
from fairplay_policy import summarize as policy_summarize

WINDOW=10
SCOPE=250
THRESHOLD=85.0
MIN_REPORTED=8  # Selection only; never impute unpublished Accuracy
RATING_GAP=250
MAX_DEEP=300
SHIFT_MIN_REFERENCE=10
SHIFT_MIN_MEDIAN=80.0
SHIFT_MIN_GAIN=15.0
SHIFT_MAD_MULTIPLIER=3.0


def _accuracy_shift(known, reference):
    """Discovery only: a robust, non-overlapping published-score shift.

    Missing scores remain missing. These descriptive thresholds allocate
    review work; they are not calibrated significance or priority thresholds.
    """
    if len(known)<MIN_REPORTED or len(reference)<SHIFT_MIN_REFERENCE:
        return False, None, None
    baseline=statistics.median(reference)
    mad=statistics.median(abs(v-baseline) for v in reference)
    recent=statistics.median(known)
    return (recent>=SHIFT_MIN_MEDIAN
            and recent-baseline>=max(SHIFT_MIN_GAIN,SHIFT_MAD_MULTIPLIER*mad)), baseline, mad


def discover(games, *, scope=SCOPE, include_recent_unrated=False,
             include_recent_rated=False):
    """Predeclare candidates using public game metadata, never Stockfish hits.

    A sliding window must be consecutive in its *original* exact-control
    chronology. Fewer than eight comparable opponents permits investigation
    but never the ten-game HIGH route; preserve that distinction in the audit.
    Unknown Chess.com Accuracy is missing, never an inferred zero.
    """
    unique={}
    for g in games:
        if (isinstance(getattr(g,'rated',None),bool)
                and not getattr(g,'probe_only',False)
                and isinstance(getattr(g,'identity',None),str)
                and isinstance(getattr(g,'ended',None),(int,float))
                and math.isfinite(g.ended) and g.ended>0
                and getattr(g,'time_class',None) in ('blitz','rapid')
                and bool(getattr(g,'time_control',''))):
            unique[g.identity]=g
    ordered=sorted(unique.values(),key=lambda g:(g.ended,g.identity))
    # These are the newest fifty games of either rating status, including
    # bullet, which the mixed-status production plan reserves for full-depth
    # review. An unrated window here can be examined even when the public
    # API omits Chess.com Accuracy entirely.
    recent_live=sorted(
        (g for g in games if isinstance(getattr(g,'rated',None),bool)
         and not getattr(g,'probe_only',False)
         and isinstance(getattr(g,'ended',None),(int,float))
         and math.isfinite(g.ended)),
        key=lambda g:(g.ended,g.identity))[-50:]
    recent_any_ids={g.identity for g in recent_live}
    # Latest rated games already have mandatory full-depth selection.
    # Missing Chess.com Accuracy cannot veto independent gameplay testing.
    rated_all=sorted((g for g in games if getattr(g,'rated',None) is True
                      and not getattr(g,'probe_only',False)
                      and isinstance(getattr(g,'ended',None),(int,float))
                      and math.isfinite(g.ended)),
                     key=lambda g:(g.ended,g.identity))
    recent_rated_ids={g.identity for g in rated_all[-50:]}
    eligible=ordered[-scope:]
    groups={}
    for g in eligible:
        groups.setdefault((g.rated,g.time_class,g.time_control),[]).append(g)
    windows=[]
    peer_eligible_windows=0
    insufficient_peer_windows=0
    missing_accuracy_windows=0
    candidates=[]
    selected=set()
    for (rated,kind,control),group in sorted(groups.items()):
        group.sort(key=lambda g:(g.ended,g.identity))
        for start in range(len(group)-WINDOW+1):
            segment=group[start:start+WINDOW]
            # Other time controls can interleave. A skipped *same-control*
            # game cannot be hidden by collection caps or missing metadata.
            consecutive=all(
                isinstance(a.control_index,int) and not isinstance(a.control_index,bool)
                and isinstance(b.control_index,int) and not isinstance(b.control_index,bool)
                and b.control_index==a.control_index+1 and b.ended>=a.ended
                for a,b in zip(segment,segment[1:]))
            if not consecutive:
                continue
            windows.append(1)
            accuracies=[_accuracy(g) for g in segment]
            known=[a for a in accuracies if a is not None]
            def peer(g):
                return (isinstance(g.rating,int) and not isinstance(g.rating,bool)
                    and isinstance(g.opponent_rating,int) and not isinstance(g.opponent_rating,bool)
                    and 100<=g.rating<=4000 and 100<=g.opponent_rating<=4000
                    and abs(g.rating-g.opponent_rating)<=RATING_GAP)
            peer_count=sum(peer(g) for g in segment)
            # Public archives often omit Accuracy even if Chess.com displayed
            # it to a signed-in reviewer. Eight or nine published scores may
            # select ALL ten games for independent full-depth analysis. No
            # missing score is assigned a value or enters the average.
            peer_eligible=peer_count>=8
            if not peer_eligible:insufficient_peer_windows+=1
            else:peer_eligible_windows+=1
            mean=statistics.mean(known) if known else None
            score_selected=(len(known)>=MIN_REPORTED and mean>=THRESHOLD)
            # If published Accuracy is missing, do not declare the player
            # innocent on that basis. Newest unrated games are already
            # depth-reserved independently of Accuracy; test their paired
            # Stockfish+Maia gameplay without inventing an Accuracy value.
            recent_unrated=(include_recent_unrated and rated is False and
                            all(g.identity in recent_any_ids for g in segment))
            recent_rated=(include_recent_rated and rated is True
                          and all(g.identity in recent_rated_ids for g in segment))
            own_ratings=[g.rating for g in segment if peer(g)]
            opponent_ratings=[g.opponent_rating for g in segment if peer(g)]
            own_reference=statistics.median(own_ratings) if own_ratings else None
            opponent_reference=statistics.median(opponent_ratings) if opponent_ratings else None
            older=[g for g in ordered if g.ended<segment[0].ended
                   and (g.rated,g.time_class,g.time_control)==(rated,kind,control)
                   and peer(g) and own_reference is not None
                   and abs(g.rating-own_reference)<=RATING_GAP
                   and abs(g.opponent_rating-opponent_reference)<=RATING_GAP]
            reference=[_accuracy(g) for g in older[-40:]]
            reference=[v for v in reference if v is not None]
            baseline=statistics.mean(reference) if len(reference)>=10 else None
            shift_selected,reference_median,reference_mad=_accuracy_shift(known,reference)
            # Relative discovery still needs comparable opponents. A jump in
            # easy wins against much weaker opponents is not a personal shift.
            shift_selected=shift_selected and peer_eligible
            if not score_selected and not shift_selected and not recent_unrated and not recent_rated:
                if peer_eligible:missing_accuracy_windows+=1
                continue
            # Selection allocates analysis. Opponent-matched evidence remains
            # necessary for this specialized priority route.
            row={'ids':tuple(g.identity for g in segment),
                 'rated':rated,'time_class':kind,'time_control':control,
                 'start':int(segment[0].ended),'end':int(segment[-1].ended),
                 'mean_accuracy':round(mean,2) if mean is not None else None,
                 'selection_reason':('published_accuracy' if score_selected
                                     else 'personal_accuracy_shift' if shift_selected
                                     else 'recent_rated_deep' if recent_rated
                                     else 'recent_unrated_deep'),
                 'peer_games':peer_count,
                 'priority_eligible':peer_eligible,
                 'selection_only_blockers':([] if peer_eligible else
                     ['Fewer than eight comparable opponents; investigated for context only.']),
                 'published_accuracy_games':len(known),
                 'missing_accuracy_games':WINDOW-len(known),
                 'prior_accuracy':round(baseline,2) if baseline is not None else None,
                 'prior_games':len(reference),
                 'prior_median_accuracy':reference_median,
                 'prior_accuracy_mad':reference_mad,
                 'personal_accuracy_shift_selected':bool(shift_selected),
                 'accuracy_shift':round(mean-baseline,2)
                    if mean is not None and baseline is not None else None}
            candidates.append(row)
            selected.update(row['ids'])
    return {'schema':'fairplay-ten-window-discovery-v1',
            'rated_blitz_rapid_games_in_scope':sum(g.rated is True for g in eligible),
            'unrated_blitz_rapid_games_in_scope':sum(g.rated is False for g in eligible),
            'total_blitz_rapid_games_in_scope':len(eligible),
            'window_games':WINDOW,'scope_limit':scope,
            'windows_examined':len(windows),
            'windows_with_at_least_eight_peers':peer_eligible_windows,
            'windows_blocked_by_opponent_gap':insufficient_peer_windows,
            'peer_eligible_windows_missing_accuracy_trigger':missing_accuracy_windows,
            'accuracy_candidates':sum(
                row['selection_reason']=='published_accuracy' for row in candidates),
            'personal_accuracy_shift_windows':sum(
                row['selection_reason']=='personal_accuracy_shift' for row in candidates),
            'context_only_windows':sum(not row['priority_eligible'] for row in candidates),
            'recent_rated_fallback_windows':sum(
                row['selection_reason']=='recent_rated_deep' for row in candidates),
            'recent_unrated_fallback_windows':sum(
                row['selection_reason']=='recent_unrated_deep' for row in candidates),
            'total_candidates':len(candidates),
            'selected_ids':selected,'candidates':candidates,
            'selection_note':(
                'At least eight published Accuracy scores averaging 85+ '
                'select all ten rated OR unrated games for full-depth review. Missing scores '
                'are not estimated and Accuracy never sets HIGH. The newest '
                'fifty rated games supply same-control windows even without Accuracy. '
                'Large robust personal Accuracy shifts can also select review. '
                'Windows without eight comparable opponents are context-only. Unrated '
                'windows in the newest fifty games are also investigated '
                'using real engine and Maia evidence if scores are missing.')}


def extend_plan(plan, discovery, latest_fifty_ids, *, max_deep=MAX_DEEP):
    """Prioritize every flagged window + latest50, retain optional controls.

    The union of 250 analyzed blitz/rapid games and newest 50 any-class games
    fits within 300 full-depth slots. If it cannot, refuse the review rather
    than silently dropping an Accuracy-selected period.
    """
    from fairplay_v21 import ReviewPlan
    available={g.identity:g for g in plan.primary}
    mandatory=set(discovery['selected_ids'])|set(latest_fifty_ids)
    if not mandatory.issubset(available) or len(mandatory)>max_deep:
        raise ValueError('Selected ten-game periods exceed the bounded full-depth review capacity.')
    original=list(plan.deep)
    chosen=set(mandatory)
    for game in reversed(original):
        if len(chosen)>=max_deep:break
        chosen.add(game.identity)
    # A duplicated game must not consume two slots or allow a misleading
    # completed-game count.
    core=tuple(g for g in plan.core if g.identity in chosen)
    tail=tuple(g for g in plan.recent_tail if g.identity in chosen)
    core_ids={g.identity for g in (*core,*tail)}
    reserve=tuple(g for g in plan.primary if g.identity in chosen-core_ids)
    result=ReviewPlan(plan.primary,core,reserve,plan.metadata_games,
                      plan.primary_limit,tail)
    if {g.identity for g in result.deep}!=chosen:
        raise ValueError('Full-depth plan did not preserve selected windows.')
    return result


def _verified_window_entry(game):
    """Same strict gameplay prerequisites as the rated-only burst route.

    Unrated games are permitted explicitly, never by relabeling their rated
    status or sending them through the older rated-only HIGH routes.
    """
    return (isinstance(getattr(game,'rated',None),bool)
            and not getattr(game,'probe_only',False)
            and getattr(game,'deep',False) is True
            and getattr(game,'time_class','') in ('blitz','rapid')
            and isinstance(getattr(game,'control_index',None),int)
            and not isinstance(game.control_index,bool)
            and game.control_index>=0
            and isinstance(getattr(game,'ended',None),(int,float))
            and math.isfinite(game.ended)
            and bool(getattr(game,'time_control',''))
            and isinstance(getattr(game,'rating',None),int)
            and not isinstance(game.rating,bool) and 100<=game.rating<=4000
            and isinstance(getattr(game,'opponent_rating',None),int)
            and not isinstance(game.opponent_rating,bool)
            and 100<=game.opponent_rating<=4000
            and 'human' in (getattr(game,'fast_metrics',None) or {})
            and 'human' in (getattr(game,'metrics',None) or {}))


def _complete_game(game, config):
    """Every move reached depth 18 and Maia; only exact evidence may score.

    MultiPV/root disagreement can mark an *otherwise depth-complete* move as
    search-inconsistent. The scorer excludes those moves from competitive
    opportunities. Do not let one ignored forced/opening/inexact position
    veto the entire ten-game episode, but never count it as anomaly proof.
    """
    from fairplay_maia import valid_policy
    if not _verified_window_entry(game) or not game.decisions:
        return False
    for d in game.decisions:
        m=d.metrics or {}
        contract=m.get('search_contract') or {}
        if not (contract.get('mode')=='depth'
                and contract.get('requested')==18
                and contract.get('multipv')==config.deep_multipv
                and contract.get('completed') is True
                and m.get('search_depth',0)>=18
                and valid_policy(d,d.human_policy)):
            return False
        if contract.get('exact') is not True:
            # Accept only a *declared* paired-score inconsistency, not a
            # missing/corrupt exactness flag. It must have been excluded
            # upstream from every positive-evidence counter.
            if (m.get('search_inconsistent') is not True
                    or any(m.get(key) for key in (
                        'useful','human_opportunity','high_information','critical'))):
                return False
    return True


def _window_evidence_gaps(games, config):
    """Aggregate diagnostic counts; never expose FENs, moves or identities."""
    from fairplay_maia import valid_policy
    missing_games=sum(g is None or not _verified_window_entry(g)
                      or not getattr(g,'decisions',None) for g in games)
    inexact_positions=0
    missing_policies=0
    for game in games:
        if game is None:
            continue
        for decision in getattr(game,'decisions',()):
            contract=decision.metrics.get('search_contract') or {}
            if not (contract.get('mode')=='depth'
                    and contract.get('requested')==18
                    and contract.get('multipv')==config.deep_multipv
                    and contract.get('completed') is True
                    and contract.get('exact') is True
                    and decision.metrics.get('search_depth',0)>=18):
                inexact_positions+=1
            if not valid_policy(decision,decision.human_policy):
                missing_policies+=1
    return {'missing_or_unverified_games':missing_games,
            'nonexact_stockfish_positions':inexact_positions,
            'missing_maia_policies':missing_policies}


def _engine_proof(games, config):
    deep=period_summary(games,config)
    fast=period_summary(games,config,fast=True)
    confirmation=paired_quality_confirmation(deep,0.85,coverage_floor=0.95)
    halves=[period_summary(games[i:i+5],config) for i in (0,5)]
    gates={
        'At least 20 difficult opportunities':deep['opportunities']>=20,
        'At least 14 high-information hits':deep['hits']>=14,
        'Hits in at least seven games':deep['hit_games']>=7,
        'At least four quiet high-information hits':deep['quiet_hits']>=4,
        'Bounded information at least 0.20':deep['information']>=0.20,
        'Signed quality excess at least 0.11':deep['quality_excess']>=0.11,
        'Evidence in both chronological halves':all(
            h['opportunities']>=8 and h['hits']>=5 and h['hit_games']>=3 for h in halves),
        'Fast reference has 20 opportunities and 12 hits':fast['opportunities']>=20 and fast['hits']>=12,
        'Deep retains at least 80% of fast hits':deep['hits']>=0.8*fast['hits'],
        'Paired search coverage':confirmation['coverage_passed'],
        'Paired search agreement':confirmation['fraction_passed'],
        'At least 12 paired confirmed hits':confirmation['hits']>=12,
    }
    return all(gates.values()),{'opportunities':deep['opportunities'],
        'hits':deep['hits'],'hit_games':deep['hit_games'],
        'quiet_hits':deep['quiet_hits'],
        'signed_quality_excess':deep['quality_excess'],
        'blockers':[name for name,passed in gates.items() if not passed],
        'paired_agreement':round(confirmation['fraction'],4)}


def _maia_proof(games):
    # All positions are inferred; only difficult competitive choices
    # contribute to a Maia discrepancy. Misses remain in game denominators.
    deep=policy_summarize(games)
    fast=policy_summarize(games,fast=True)
    gates={
        'At least six Maia contributor games':deep['contributors']>=6,
        'At least 5.5 contributor equivalents':deep['contributor_weight']>=5.5,
        'At least 20 game-capped comparisons':deep['effective_positions']>=20,
        'Signed Maia quality excess at least 0.15':deep['signed_excess']>=0.15,
        'Maia information at least 0.10':deep['information']>=0.10,
        'At least 70% stable comparisons':deep['stable']>=0.70*deep['positions'],
        'At least 24 comparable positions':deep['positions']>=24,
        'Deep retains 75% of fast signed Maia excess':deep['signed_excess']>=0.75*fast['signed_excess'],
    }
    return all(gates.values()),{'contributors':deep['contributors'],
        'positions':deep['positions'],
        'blockers':[name for name,passed in gates.items() if not passed],
        'signed_excess':round(deep['signed_excess'],4),
        'information':round(deep['information'],4)}


def clock_window_evidence(games):
    """Count every player move; timing is inferred from PGN clocks, not timestamps."""
    decisions=[d for game in games for d in game.decisions]
    available=[d for d in decisions if isinstance(getattr(d,'think',None),(int,float))
               and not isinstance(d.think,bool)
               and math.isfinite(d.think) and d.think>=0]
    reliable=[d for d in available if getattr(d,'clock_valid',False)]
    difficult=[d.think for d in reliable if d.metrics.get('human_opportunity')]
    anomalous=[d.think for d in reliable if d.metrics.get('high_information')]
    other=[d.think for d in reliable if not d.metrics.get('high_information')]
    def median(values):
        return round(statistics.median(values),2) if values else None
    return {
        'player_moves':len(decisions),
        'clock_deltas_inferred':len(available),
        'reliable_clocks':len(reliable),
        'missing_clock_deltas':len(decisions)-len(available),
        'unreliable_clock_deltas':len(available)-len(reliable),
        'median_seconds':median([d.think for d in reliable]),
        'median_difficult_seconds':median(difficult),
        'median_engine_anomaly_seconds':median(anomalous),
        'median_other_seconds':median(other),
        'difficult_moves_with_clocks':len(difficult),
        'engine_anomalies_with_clocks':len(anomalous),
        'source':'PGN remaining clocks plus increment, not literal move timestamps',
        'scoring_influence':False,
    }


def integrate(result, games, discovery, config=CONFIG):
    """Independent review classification; no Accuracy/Elo route to HIGH."""
    index={g.identity:g for g in games}
    windows=[]
    qualified=0
    incomplete=0
    incomplete_eligible=0
    # The same game occurs in up to ten overlapping windows. Legal-policy
    # validation reconstructs every board, so do it once per game in this
    # immutable scoring pass. Never retain this cache across rescans: engine
    # contracts and policies can change even when the game ID is unchanged.
    complete_by_id={}
    gaps_by_id={}
    def game_complete(game):
        if game is None:
            return False
        if game.identity not in complete_by_id:
            complete_by_id[game.identity]=_complete_game(game,config)
        return complete_by_id[game.identity]

    def evidence_gaps(rows):
        total={}
        for game in rows:
            key=game.identity if game is not None else None
            if key not in gaps_by_id:
                gaps_by_id[key]=_window_evidence_gaps([game],config)
            for name,count in gaps_by_id[key].items():
                total[name]=total.get(name,0)+count
        return total

    for candidate in sorted(discovery['candidates'],key=lambda c:-c['end']):
        rows=[index.get(ident) for ident in candidate['ids']]
        peer_eligible=candidate.get('priority_eligible',candidate.get('peer_games',0)>=8)
        context={'selection_reason':candidate.get('selection_reason'),
                 'peer_games':candidate.get('peer_games',0),
                 'priority_eligible':peer_eligible,
                 'selection_only_blockers':candidate.get('selection_only_blockers',[])}
        if not all(game_complete(g) for g in rows):
            incomplete+=1
            incomplete_eligible+=int(peer_eligible)
            windows.append({**context,'rated':candidate['rated'],
                'mean_accuracy':candidate['mean_accuracy'],
                'published_accuracy_games':candidate['published_accuracy_games'],
                'missing_accuracy_games':candidate['missing_accuracy_games'],
                'window_end_time':candidate['end'],
                'full_depth_and_maia':False,'engine_pass':False,
                'maia_pass':False,'qualified':False,
                'evidence_gaps':evidence_gaps(rows)})
            continue
        engine,engine_evidence=_engine_proof(rows,config)
        maia,maia_evidence=_maia_proof(rows)
        qualified+=int(engine and maia and peer_eligible)
        clocks=clock_window_evidence(rows)
        windows.append({**context,'rated':candidate['rated'],
            'mean_accuracy':candidate['mean_accuracy'],
            'published_accuracy_games':candidate['published_accuracy_games'],
            'missing_accuracy_games':candidate['missing_accuracy_games'],
            'window_end_time':candidate['end'],
            'clock_audit':clocks,
            'full_depth_and_maia':True,'engine_pass':engine,
            'maia_pass':maia,'qualified':bool(engine and maia and peer_eligible),
            'engine':engine_evidence,'maia':maia_evidence,
            'reliable_clock_samples':clocks['reliable_clocks'],
            'player_moves_without_reliable_clock':(
                clocks['missing_clock_deltas']+clocks['unreliable_clock_deltas']),
            'earlier_mean_accuracy':candidate['prior_accuracy'],
            'accuracy_shift':candidate['accuracy_shift']})
    complete=(not result.partial
              and result.coverage.get('primary_engine_complete',False)
              and result.coverage.get('required_deep_complete',False))
    enabled=not {'human','neural','difficulty','accuracy_window_high'} & set(config.disabled_features)
    # Each independently confirmed ten-game window can justify HIGH even
    # when ANOTHER candidate lacks an exact search. The latter must not veto
    # genuine confirmed evidence from an unrelated window. It also must not
    # be silently treated as a negative finding.
    passed=bool(qualified and complete and enabled)
    audit={'schema':discovery['schema'],
           'rated_blitz_rapid_games_in_scope':discovery['rated_blitz_rapid_games_in_scope'],
           'unrated_blitz_rapid_games_in_scope':discovery.get('unrated_blitz_rapid_games_in_scope',0),
           'total_blitz_rapid_games_in_scope':discovery.get(
               'total_blitz_rapid_games_in_scope',
               discovery['rated_blitz_rapid_games_in_scope']),
           'windows_examined':discovery['windows_examined'],
           'accuracy_candidates':discovery['accuracy_candidates'],
           'personal_accuracy_shift_windows':discovery.get('personal_accuracy_shift_windows',0),
           'context_only_windows':discovery.get('context_only_windows',0),
           'recent_unrated_fallback_windows':discovery.get(
               'recent_unrated_fallback_windows',0),
           'recent_rated_fallback_windows':discovery.get(
               'recent_rated_fallback_windows',0),
           'windows_with_at_least_eight_peers':discovery.get(
               'windows_with_at_least_eight_peers',0),
           'windows_blocked_by_opponent_gap':discovery.get(
               'windows_blocked_by_opponent_gap',0),
           'peer_eligible_windows_missing_accuracy_trigger':discovery.get(
               'peer_eligible_windows_missing_accuracy_trigger',0),
           'total_candidates':len(discovery['candidates']),
           'fully_verified_candidates':len(windows)-incomplete,
           'unverified_candidates':incomplete,'confirmed_engine_maia_windows':qualified,
           'unverified_priority_eligible_candidates':incomplete_eligible,
           'unverified_windows_are_not_negative_evidence':True,
           'selected_games':len(discovery['selected_ids']),
           'high_path_passed':passed,'window_details':windows[:25],
           'selection_uses_accuracy':bool(discovery['accuracy_candidates']),
           'selection_uses_recent_rated_coverage':bool(
               discovery.get('recent_rated_fallback_windows')),
           'selection_uses_recent_unrated_coverage':bool(
               discovery.get('recent_unrated_fallback_windows')),
           'priority_uses_accuracy':False,
           'timing_note':('Game end-time chronology and the validity/coverage of PGN '
                          'remaining-clock samples are checked. Absolute '
                          'per-move timestamps are not available and are '
                          'never invented.'),
           'scope_note':('Up to 250 rated or unrated blitz/rapid games in the latest 500-game '
                         'primary scope; separate rated-status, exact-control windows, '
                         'at least eight peer-rated opponents for this HIGH route. Published '
                         'Accuracy can preselect older windows; the newest '
                         'rated and unrated windows can be tested using real deep '
                         'gameplay even without published Accuracy. '
                         'Missing scores are never imputed.'),
           'calibration_note':('HIGH is an investigative priority. False-positive '
                               'rates are not established without labelled '
                               'fair-player and confirmed-engine holdouts.')}
    result.diagnostics['ten_accuracy_windows']=audit
    result.diagnostics.setdefault('high_paths',{})['Ten-game paired Stockfish + Maia HIGH']={
        'passed':passed,'blockers':(
            [] if passed else
            ['No ten-game block meets all comparable-opponent, paired '
             'Stockfish/Maia and mandatory-coverage requirements.'])}
    if incomplete_eligible and not passed and result.priority in ('LOW','INSUFFICIENT DATA'):
        # A broken/uncertain candidate is NOT a completed LOW check.
        # Keep the no-verdict state visible rather than losing the whole
        # report or clearing a suspicious period by default.
        result.priority='INSUFFICIENT DATA'
        result.reasons=[
            f'{incomplete_eligible} selected ten-game window(s) lacked complete exact '
            'Stockfish and Maia evidence; no LOW conclusion can be drawn '
            'from those windows. A new or resumed scan is required.',
            'Published Accuracy is a discovery clue, not proof of cheating.']
    if passed and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
        result.priority='HIGH'
        result.deep_confirmed=True
        result.diagnostics['high_path']='Ten-game fully verified Stockfish and Maia anomaly'
        result.diagnostics['high_blocked']=[]
        result.reasons=[
            'A preselected ten-game period has repeated difficult engine '
            'anomalies and corroborating human-policy discrepancies.',
            'Every move of the period received depth-18 Stockfish and Maia; '
            'published Accuracy merely selected the games for review. '
            'HIGH calls for human inspection, not automatic punishment.']
    return result
