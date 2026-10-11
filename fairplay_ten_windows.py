"""Accuracy-triggered ten-game deep review with independent gameplay HIGH gate.

Screen every overlapping ten-game window of the latest 250 rated blitz/rapid
games available in the 500-game primary sample. Accuracy only chooses complete
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


def discover(games, *, scope=SCOPE):
    """Predeclare candidates using public rated metadata, never Stockfish hits.

    A sliding window must be consecutive in its *original* exact-control
    chronology and at least eight games must have similarly rated opponents.
    Unknown Chess.com Accuracy is missing, never an inferred zero.
    """
    unique={}
    for g in games:
        if (getattr(g,'rated',None) is True
                and not getattr(g,'probe_only',False)
                and isinstance(getattr(g,'identity',None),str)
                and isinstance(getattr(g,'ended',None),(int,float))
                and math.isfinite(g.ended) and g.ended>0
                and getattr(g,'time_class',None) in ('blitz','rapid')
                and bool(getattr(g,'time_control',''))):
            unique[g.identity]=g
    ordered=sorted(unique.values(),key=lambda g:(g.ended,g.identity))
    eligible=ordered[-scope:]
    groups={}
    for g in eligible:
        groups.setdefault((g.time_class,g.time_control),[]).append(g)
    windows=[]
    candidates=[]
    selected=set()
    for (kind,control),group in sorted(groups.items()):
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
            if len(known)<MIN_REPORTED or peer_count<8:
                continue
            mean=statistics.mean(known)
            if mean<THRESHOLD:
                continue
            older=[g for g in ordered if g.ended<segment[0].ended
                   and (g.time_class,g.time_control)==(kind,control)
                   and peer(g)]
            reference=[_accuracy(g) for g in older[-40:]]
            reference=[v for v in reference if v is not None]
            baseline=statistics.mean(reference) if len(reference)>=10 else None
            # Extra metadata context has no authority to invalidate
            # independent ten-game gameplay evidence.
            row={'ids':tuple(g.identity for g in segment),
                 'time_class':kind,'time_control':control,
                 'start':int(segment[0].ended),'end':int(segment[-1].ended),
                 'mean_accuracy':round(mean,2),'peer_games':peer_count,
                 'published_accuracy_games':len(known),
                 'missing_accuracy_games':WINDOW-len(known),
                 'prior_accuracy':round(baseline,2) if baseline is not None else None,
                 'prior_games':len(reference),
                 'accuracy_shift':round(mean-baseline,2) if baseline is not None else None}
            candidates.append(row)
            selected.update(row['ids'])
    return {'schema':'fairplay-ten-window-discovery-v1',
            'rated_blitz_rapid_games_in_scope':len(eligible),
            'window_games':WINDOW,'scope_limit':scope,
            'windows_examined':len(windows),
            'accuracy_candidates':len(candidates),
            'selected_ids':selected,'candidates':candidates,
            'selection_note':(
                'At least eight published Accuracy scores averaging 85+ '
                'select all ten games for full-depth review. Missing scores '
                'are not estimated and Accuracy never sets HIGH.')}


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


def _complete_game(game, config):
    """Every move reached depth 18 and Maia; only exact evidence may score.

    MultiPV/root disagreement can mark an *otherwise depth-complete* move as
    search-inconsistent. The scorer excludes those moves from competitive
    opportunities. Do not let one ignored forced/opening/inexact position
    veto the entire ten-game episode, but never count it as anomaly proof.
    """
    from fairplay_maia import valid_policy
    if not _verified_entry(game) or not game.decisions:
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
            # Depth was completed, but this particular position is unreliable.
            # A non-exact position can never be a scored opportunity or hit.
            if any(m.get(key) for key in (
                    'useful','human_opportunity','high_information','critical')):
                return False
    return True


def _window_evidence_gaps(games, config):
    """Aggregate diagnostic counts; never expose FENs, moves or identities."""
    from fairplay_maia import valid_policy
    missing_games=sum(g is None or not _verified_entry(g)
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
    ok=(deep['opportunities']>=20 and deep['hits']>=14
        and deep['hit_games']>=7 and deep['quiet_hits']>=4
        and deep['information']>=0.20 and deep['quality_excess']>=0.11
        and all(h['opportunities']>=8 and h['hits']>=5
                and h['hit_games']>=3 for h in halves)
        and fast['opportunities']>=20 and fast['hits']>=12
        and deep['hits']>=0.8*fast['hits']
        and confirmation['coverage_passed']
        and confirmation['fraction_passed']
        and confirmation['hits']>=12)
    return bool(ok),{'opportunities':deep['opportunities'],
        'hits':deep['hits'],'hit_games':deep['hit_games'],
        'quiet_hits':deep['quiet_hits'],
        'paired_agreement':round(confirmation['fraction'],4)}


def _maia_proof(games):
    # All positions are inferred; only difficult competitive choices
    # contribute to a Maia discrepancy. Misses remain in game denominators.
    deep=policy_summarize(games)
    fast=policy_summarize(games,fast=True)
    ok=(deep['contributors']>=6 and deep['contributor_weight']>=5.5
        and deep['effective_positions']>=20
        and deep['signed_excess']>=0.15 and deep['information']>=0.10
        and deep['stable']>=0.70*deep['positions']
        and deep['positions']>=24 and
        deep['signed_excess']>=0.75*fast['signed_excess'])
    return bool(ok),{'contributors':deep['contributors'],
        'positions':deep['positions'],
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
    for candidate in sorted(discovery['candidates'],key=lambda c:-c['end']):
        rows=[index.get(ident) for ident in candidate['ids']]
        if not all(g is not None and _complete_game(g,config) for g in rows):
            incomplete+=1
            windows.append({'mean_accuracy':candidate['mean_accuracy'],
                'published_accuracy_games':candidate['published_accuracy_games'],
                'missing_accuracy_games':candidate['missing_accuracy_games'],
                'window_end_time':candidate['end'],
                'full_depth_and_maia':False,'engine_pass':False,
                'maia_pass':False,'qualified':False,
                'evidence_gaps':_window_evidence_gaps(rows,config)})
            continue
        engine,engine_evidence=_engine_proof(rows,config)
        maia,maia_evidence=_maia_proof(rows)
        qualified+=int(engine and maia)
        clocks=clock_window_evidence(rows)
        windows.append({'mean_accuracy':candidate['mean_accuracy'],
            'published_accuracy_games':candidate['published_accuracy_games'],
            'missing_accuracy_games':candidate['missing_accuracy_games'],
            'window_end_time':candidate['end'],
            'clock_audit':clocks,
            'full_depth_and_maia':True,'engine_pass':engine,
            'maia_pass':maia,'qualified':bool(engine and maia),
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
           'windows_examined':discovery['windows_examined'],
           'accuracy_candidates':len(discovery['candidates']),
           'fully_verified_candidates':len(windows)-incomplete,
           'unverified_candidates':incomplete,'confirmed_engine_maia_windows':qualified,
           'unverified_windows_are_not_negative_evidence':True,
           'selected_games':len(discovery['selected_ids']),
           'high_path_passed':passed,'window_details':windows[:25],
           'selection_uses_accuracy':True,'priority_uses_accuracy':False,
           'timing_note':('Game end-time chronology and the validity/coverage of PGN '
                          'remaining-clock samples are checked. Absolute '
                          'per-move timestamps are not available and are '
                          'never invented.'),
           'scope_note':('Up to 250 rated blitz/rapid games in the latest 500-game '
                         'primary scope; ten consecutive same-control games, '
                         'at least eight peer-rated opponents and at least eight '
                         'published Accuracy reports. Missing scores are never imputed.'),
           'calibration_note':('HIGH is an investigative priority. False-positive '
                               'rates are not established without labelled '
                               'fair-player and confirmed-engine holdouts.')}
    result.diagnostics['ten_accuracy_windows']=audit
    result.diagnostics.setdefault('high_paths',{})['Ten-game paired Stockfish + Maia HIGH']={
        'passed':passed,'blockers':(
            [] if passed else
            ['No ten-game block has both full-depth Stockfish and complete '
             'Maia game-capped evidence, or mandatory coverage is incomplete.'])}
    if incomplete and not passed and result.priority in ('LOW','INSUFFICIENT DATA'):
        # A broken/uncertain candidate is NOT a completed LOW check.
        # Keep the no-verdict state visible rather than losing the whole
        # report or clearing a suspicious period by default.
        result.priority='INSUFFICIENT DATA'
        result.reasons=[
            f'{incomplete} selected ten-game window(s) lacked complete exact '
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
