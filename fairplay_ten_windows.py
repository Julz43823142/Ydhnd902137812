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
            # All ten valid Accuracy values avoid cherry-picking the few
            # Chess.com reports which happen to be available.
            if len(known)!=WINDOW or peer_count<8:
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
            'selection_note':'Chess.com Accuracy selects full reviews but does not score cheating.'}


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
    """Every player move has exact requested-depth Stockfish and valid Maia."""
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
                and contract.get('exact') is True
                and m.get('search_depth',0)>=18
                and valid_policy(d,d.human_policy)):
            return False
    return True


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


def integrate(result, games, discovery, config=CONFIG):
    """Independent review classification; no Accuracy/Elo route to HIGH."""
    index={g.identity:g for g in games}
    windows=[]
    qualified=0
    incomplete=0
    for candidate in discovery['candidates']:
        rows=[index.get(ident) for ident in candidate['ids']]
        if not all(g is not None and _complete_game(g,config) for g in rows):
            incomplete+=1
            windows.append({'mean_accuracy':candidate['mean_accuracy'],
                'full_depth_and_maia':False,'engine_pass':False,
                'maia_pass':False,'qualified':False})
            continue
        engine,engine_evidence=_engine_proof(rows,config)
        maia,maia_evidence=_maia_proof(rows)
        qualified+=int(engine and maia)
        clock_samples=sum(bool(getattr(d,'clock_valid',False))
                          for game in rows for d in game.decisions)
        total_clocks=sum(len(game.decisions) for game in rows)
        windows.append({'mean_accuracy':candidate['mean_accuracy'],
            'full_depth_and_maia':True,'engine_pass':engine,
            'maia_pass':maia,'qualified':bool(engine and maia),
            'engine':engine_evidence,'maia':maia_evidence,
            'reliable_clock_samples':clock_samples,
            'player_moves_without_reliable_clock':total_clocks-clock_samples,
            'earlier_mean_accuracy':candidate['prior_accuracy'],
            'accuracy_shift':candidate['accuracy_shift']})
    complete=(not result.partial
              and result.coverage.get('primary_engine_complete',False)
              and result.coverage.get('required_deep_complete',False))
    enabled=not {'human','neural','difficulty','accuracy_window_high'} & set(config.disabled_features)
    passed=bool(qualified and complete and enabled and not incomplete)
    audit={'schema':discovery['schema'],
           'rated_blitz_rapid_games_in_scope':discovery['rated_blitz_rapid_games_in_scope'],
           'windows_examined':discovery['windows_examined'],
           'accuracy_candidates':len(discovery['candidates']),
           'fully_verified_candidates':len(windows)-incomplete,
           'unverified_candidates':incomplete,'confirmed_engine_maia_windows':qualified,
           'selected_games':len(discovery['selected_ids']),
           'high_path_passed':passed,'window_details':windows[:25],
           'selection_uses_accuracy':True,'priority_uses_accuracy':False,
           'timing_note':('Game end-time chronology and the validity/coverage of PGN '
                          'remaining-clock samples are checked. Absolute '
                          'per-move timestamps are not available and are '
                          'never invented.'),
           'scope_note':('Up to 250 rated blitz/rapid games in the latest 500-game '
                         'primary scope; ten consecutive same-control games, '
                         'at least eight peer-rated opponents and all ten '
                         'official Accuracy reports.'),
           'calibration_note':('HIGH is an investigative priority. False-positive '
                               'rates are not established without labelled '
                               'fair-player and confirmed-engine holdouts.')}
    result.diagnostics['ten_accuracy_windows']=audit
    result.diagnostics.setdefault('high_paths',{})['Ten-game paired Stockfish + Maia HIGH']={
        'passed':passed,'blockers':(
            [] if passed else
            ['No ten-game block has both full-depth Stockfish and complete '
             'Maia game-capped evidence, or mandatory coverage is incomplete.'])}
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
