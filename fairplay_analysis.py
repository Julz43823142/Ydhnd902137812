"""Reproducible two-pass evidence screening; no case storage or punishment hooks.

The thresholds rank reviews. They are not empirically calibrated cheating rates.
All engine decisions use the subject's POV; every signal has minimum coverage.
"""
import math
import os
import statistics as stats
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import chess
import chess.engine
import chess_play

from fairplay_config import CONFIG, VERSION, ReviewConfig
from fairplay_timing import cadence, clock_values, trivial_delay_metrics, trivial_delay_summary
from fairplay_baseline import engine_data, timing_profile
from fairplay_clusters import (clamp, evidence, find_clusters, regime_changes,
                               select_deep_games, confirm_cluster, summary, buckets)
from fairplay_history import probe_decisions, historical_candidates, session_history
from fairplay_data import (DeadlineReached, GameSample, PubAPI, ReviewError,
                           ScanDeadline, check_deadline, collect_games, finite_number, username, primary_limit)


def median(values, default=None):
    return stats.median(values) if values else default


def percentile(values, fraction=.90):
    if not values:return None
    ordered = sorted(values)
    return ordered[min(len(ordered)-1, math.ceil(fraction*len(ordered))-1)]


def ratio(hits, count):
    return hits/count if count else None


def score_cp(line, color):
    score = line.get('score')
    if score is None:raise ReviewError('Stockfish returned incomplete evaluation data.')
    value = score.pov(color).score(mate_score=10_000)
    if value is None:raise ReviewError('Stockfish returned an invalid evaluation.')
    return max(-10_000, min(10_000, int(value)))


def engine_metrics(decision, lines, actual_line, color, config=CONFIG):
    lines = [line for line in lines if line.get('pv')]
    if not lines:raise ReviewError('Stockfish returned no candidate moves.')
    values = [score_cp(line, color) for line in lines]
    candidates = [line['pv'][0].uci() for line in lines]
    if decision.move in candidates:
        actual = values[candidates.index(decision.move)]
    else:
        actual = score_cp(actual_line, color)
    gap = max(0, values[0]-values[1]) if len(values)>1 else None
    spread = max(0, values[0]-values[-1]) if len(values)>2 else None
    inconsistent = actual > values[0]+config.inconsistent_eval_cp
    # Root-restricted alternatives can search deeper than a MultiPV line. A
    # contradictory better score is search uncertainty, never a zero-loss hit.
    # Only genuine forced/trivial decisions and openings are excluded. Decisive
    # won/lost positions are weak evidence: best-move CPL there is misleading.
    useful = (decision.useful and not decision.forced and not decision.trivial_kind
              and decision.legal>1 and decision.phase!='opening' and abs(values[0]) <= 600 and not inconsistent)
    critical = (useful and decision.legal >= config.critical_legal and gap is not None
                and spread is not None and gap >= config.critical_gap
                and spread >= config.critical_spread)
    # A scaled evaluation-loss index accounts for the evaluation context. It
    # is not a player cheating probability or a fitted win-probability model.
    def scaled(cp):return 1/(1+math.exp(-.00368208*cp))
    return {'before_cp': values[0], 'actual_cp': actual, 'best': candidates[0],
            'search_inconsistent':inconsistent,
            'near_best':not inconsistent and values[0]-actual<=config.equivalent_cp,
            'equivalent_candidates':sum(values[0]-value<=config.equivalent_cp for value in values),
            'scaled_loss':max(0.0,scaled(values[0])-scaled(actual)),
            'top1': decision.move == candidates[0], 'top3': decision.move in candidates[:3],
            'cpl': min(1000, max(0, values[0]-actual)), 'gap': gap, 'spread': spread,
            'useful': useful, 'critical': bool(critical),
            'unique': bool(critical and gap >= config.unique_gap),
            'critical_kind': ('tactical' if decision.capture or decision.gives_check or decision.check else 'quiet') if critical else None,
            'weight': (min(1.0, .3 + decision.legal/30) * (.85 if decision.capture or decision.gives_check else 1.0)
                       * (1.0 if abs(values[0])<=300 else .5) * min(1.0,.25+(gap or 0)/180)) if useful else 0.0}


def summarize(game: GameSample, config=CONFIG):
    from fairplay_positions import position_context, position_summary
    position_context(game,config)
    moves = [d for d in game.decisions if d.metrics.get('useful')]
    critical = [d for d in moves if d.metrics['critical']]
    unique = [d for d in critical if d.metrics['unique']]
    losses = [d.metrics['cpl'] for d in moves]
    quiet = [d for d in moves if not (d.capture or d.check or d.gives_check)]
    compared = [d for d in moves if getattr(d,'fast_engine',{})
                and d.metrics.get('nodes',0)>d.fast_engine.get('nodes',0)]
    consecutive = run = 0
    for d in moves:
        # Count consecutive critical opportunities; a noncritical move is not
        # itself evidence but a missed critical move breaks the hit sequence.
        if d.metrics['critical']:
            run = run+1 if d.metrics['top1'] else 0
            consecutive = max(consecutive, run)
    game.metrics = {'decisions': len(moves),
                    'search_inconsistent':sum(d.metrics.get('search_inconsistent',False) for d in game.decisions),
                    'near_best':ratio(sum(d.metrics.get('near_best',d.metrics['cpl']<=config.equivalent_cp) for d in moves),len(moves)),
                    'scaled_loss':median([d.metrics['scaled_loss'] for d in moves if 'scaled_loss' in d.metrics]),
                    'quiet_top1':ratio(sum(d.metrics['top1'] for d in quiet),len(quiet)),
                    'depth_compared':len(compared),
                    'depth_best_stable':ratio(sum(d.fast_engine.get('best')==d.metrics.get('best') for d in compared),len(compared)),
                    'median_cpl': median(losses),
                    'robust_cpl': stats.mean(sorted(losses)[:max(1, math.ceil(len(losses)*.9))]) if losses else None,
                    'p90_cpl': percentile(losses), 'top1': ratio(sum(d.metrics['top1'] for d in moves), len(moves)),
                    'top3': ratio(sum(d.metrics['top3'] for d in moves), len(moves)),
                    'critical': len(critical), 'critical_top1': ratio(sum(d.metrics['top1'] for d in critical), len(critical)),
                    'critical_top3': ratio(sum(d.metrics['top3'] for d in critical), len(critical)),
                    'critical_cpl': median([d.metrics['cpl'] for d in critical]),
                    'unique': len(unique), 'unique_hits': sum(d.metrics['top1'] for d in unique),
                    'critical_sequence': consecutive,
                    'quiet_critical': sum(d.metrics.get('critical_kind')=='quiet' for d in critical),
                    'tactical_critical': sum(d.metrics.get('critical_kind')=='tactical' for d in critical),
                    'effective_decisions':sum(d.metrics.get('weight',1) for d in moves),
                    'weighted_top1': ratio(sum(d.metrics.get('weight',1)*d.metrics['top1'] for d in moves),sum(d.metrics.get('weight',1) for d in moves)),
                    'mistakes': sum(v >= config.mistake_cp for v in losses),
                    'blunders': sum(v >= config.blunder_cp for v in losses),
                    'critical_mistakes': sum(d.metrics['cpl'] >= config.mistake_cp for d in critical)}
    game.metrics.update(position_summary(game))
    game.metrics['timing'] = timing_metrics(game,config)
    return game.metrics


def correlation(xs, ys):
    if len(xs)<8:return None
    mean_x, mean_y = stats.mean(xs), stats.mean(ys)
    a, b = sum((x-mean_x)**2 for x in xs), sum((y-mean_y)**2 for y in ys)
    if a == 0 or b == 0:return None
    return sum((x-mean_x)*(y-mean_y) for x,y in zip(xs,ys))/math.sqrt(a*b)


def timing_metrics(game, config=CONFIG):
    moves = [d for d in game.decisions if d.metrics.get('useful') and d.clock_reliable]
    values = [d.think for d in moves]
    clocks = clock_values(game)
    result = {'count':len(clocks), 'engine_clock_count':len(values),
              'all_valid_clocks':sum(d.clock_valid for d in game.decisions),
              'clock_comments':sum(d.clock_after is not None for d in game.decisions),
              'excluded_clocks':sum(d.clock_after is not None and not d.clock_valid for d in game.decisions),
              **cadence(clocks,config)}
    critical = [d.think for d in moves if d.metrics['critical']]
    easy = [d.think for d in moves if not d.metrics['critical']]
    result['critical_median'] = median(critical)
    result['ordinary_median'] = median(easy)
    result['complexity_response'] = correlation([d.metrics.get('gap') or 0 for d in moves], values)
    hit_times = [d.think for d in moves if d.metrics['critical'] and d.metrics['top1']]
    result['critical_cadence'] = cadence(hit_times,config)
    midpoint = len(moves)//2
    first, last = moves[:midpoint], moves[midpoint:]
    prior, later = cadence([d.think for d in first],config), cadence([d.think for d in last],config)
    def segment_data(segment):
        opportunities=[d for d in segment if d.metrics['critical']]
        return {'decisions':len(segment),'median_cpl':median([d.metrics['cpl'] for d in segment]),
                'top1':ratio(sum(d.metrics['top1'] for d in segment),len(segment)),
                'critical':len(opportunities),'critical_top1':ratio(sum(d.metrics['top1'] for d in opportunities),len(opportunities)),
                'cadence':cadence([d.think for d in segment],config)}
    result['segments']={'earlier':segment_data(first),'later':segment_data(last)}
    result['regime_shift'] = bool(len(last)>=config.min_timing_moves and later.get('elevated')
                                 and not prior.get('elevated') and prior.get('cv',0)>.5
                                 and median([d.metrics['cpl'] for d in first],0)>40
                                 and median([d.metrics['cpl'] for d in last],100)<15)
    # Bullet rounding, premoves and lag make these measures much less reliable.
    result['reliability'] = 'LOW' if game.time_class=='bullet' else 'MEDIUM' if len(values)>=15 else 'LOW'
    result['trivial_delay'] = trivial_delay_metrics(game.decisions,config)
    return result


def performance_metrics(games):
    useful=[g for g in games if g.metrics.get('decisions',0)>=CONFIG.min_game_decisions]
    shifts=regime_changes(useful)
    contrasts=[]
    control_rows=[]
    for (kind,control),group in buckets(useful).items():
        wins=[g for g in group if g.result=='Win'];losses=[g for g in group if g.result=='Loss']
        wc=median([engine_data(g)['robust_cpl'] for g in wins]);lc=median([engine_data(g)['robust_cpl'] for g in losses])
        contrast=None
        if min(len(wins),len(losses))>=8:
            wr=median([g.opponent_rating for g in wins if g.opponent_rating is not None]);lr=median([g.opponent_rating for g in losses if g.opponent_rating is not None])
            wn=median([engine_data(g)['decisions'] for g in wins]);ln=median([engine_data(g)['decisions'] for g in losses])
            comparable=(wr is not None and lr is not None and abs(wr-lr)<=250 and .5<=wn/ln<=2)
            clean=sum(engine_data(g)['robust_cpl']<=20 for g in wins)
            contrast={'win_cpl':wc,'loss_cpl':lc,'elevated':bool(comparable and wc<=15 and lc>=70 and clean>=8),
                      'clean_wins':clean,'comparable':comparable,'control':control}
            if contrast['elevated'] and kind not in contrasts:contrasts.append(kind)
        control_rows.append({'class':kind,'control':control,'games':len(group),'win_loss_contrast':contrast})
    classes={}
    for kind in ('rapid','blitz','bullet'):
        group=[g for g in useful if g.time_class==kind]
        wins=[g for g in group if g.result=='Win'];losses=[g for g in group if g.result=='Loss'];draws=[g for g in group if g.result=='Draw']
        bycontrol=[r for r in control_rows if r['class']==kind]
        change=next((r for r in shifts if r['class']==kind),None)
        dominant=max(bycontrol,key=lambda r:r['games'],default=None)
        rolling=[g for g in group if dominant and g.time_control==dominant['control']]
        classes[kind]={'games':len(group),'wins':len(wins),'losses':len(losses),'draws':len(draws),
                       'win_cpl':median([engine_data(g)['robust_cpl'] for g in wins]),'loss_cpl':median([engine_data(g)['robust_cpl'] for g in losses]),
                       'draw_cpl':median([engine_data(g)['robust_cpl'] for g in draws]),'shift':change,
                       'win_loss_contrast':next((r['win_loss_contrast'] for r in bycontrol if r['win_loss_contrast']),None),
                       'rolling_control':dominant['control'] if dominant else None,
                       'rolling_cpl':[median([engine_data(g)['robust_cpl'] for g in rolling[i:i+10]]) for i in range(max(0,len(rolling)-9))]}
    return {'classes':classes,'shifts':shifts,'contrasts':contrasts,'controls':control_rows}


def neutral_profile_context(profile):
    """External closure/ban labels are ground truth, never analytical input.

    Use an allowlist instead of interpreting or even reading `status`. Keep
    this boundary for direct score_review callers as well as the API pipeline.
    """
    return {key:profile[key] for key in ('joined','title') if key in profile}


def context_metrics(games, profile):
    profile = neutral_profile_context(profile)
    joined = finite_number(profile.get('joined'))
    age = max(0, int((time.time()-joined)/86400)) if joined and 0<joined<=time.time() else None
    by_class = {}
    for kind in ('rapid','blitz','bullet'):
        group = [g for g in games if g.time_class==kind]
        rated = [g for g in group if g.rated is True and g.rating is not None and g.opponent_rating is not None]
        expected = [1/(1+10**((g.opponent_rating-g.rating)/400)) for g in rated]
        actual = [g.score for g in rated]
        ratings = [g.rating for g in group if g.rated is True and g.rating is not None]
        variance = sum(p*(1-p) for p in expected)
        excess = (sum(actual)-sum(expected))/math.sqrt(variance) if len(rated)>=20 and variance>0 else None
        by_class[kind] = {'games':len(group),'rated_games':len(rated),'expected':sum(expected),
                          'actual':sum(actual),'excess_z':excess,'rating_gain':ratings[-1]-ratings[0] if len(ratings)>=10 else None,
                          'rating_volatility':stats.pstdev(ratings) if len(ratings)>=10 else None,
                          'rolling_excess':[sum(actual[i:i+10])-sum(expected[i:i+10]) for i in range(max(0,len(rated)-9))]}
        streak = best = 0
        for game in group:
            streak = streak+1 if game.result=='Win' else 0
            best = max(best,streak)
        by_class[kind]['longest_win_streak'] = best
        by_class[kind]['score_vs_200_stronger'] = sum(g.score for g in rated if g.opponent_rating>=g.rating+200)
        by_class[kind]['games_vs_200_stronger'] = sum(g.opponent_rating>=g.rating+200 for g in rated)
    return {'age_days':age,'title':str(profile.get('title',''))[:8],'classes':by_class}


def aggregate(games):
    decisions = [d for g in games for d in g.decisions if d.metrics.get('useful')]
    critical = [d for d in decisions if d.metrics['critical']]
    unique = [d for d in critical if d.metrics['unique']]
    return {'games':len(games),'decisions':len(decisions),'critical':len(critical),
            'median_cpl':median([d.metrics['cpl'] for d in decisions]),
            'p90_cpl':percentile([d.metrics['cpl'] for d in decisions]),
            'top1':ratio(sum(d.metrics['top1'] for d in decisions),len(decisions)),
            'top3':ratio(sum(d.metrics['top3'] for d in decisions),len(decisions)),
            'critical_top1':ratio(sum(d.metrics['top1'] for d in critical),len(critical)),
            'critical_top3':ratio(sum(d.metrics['top3'] for d in critical),len(critical)),
            'critical_cpl':median([d.metrics['cpl'] for d in critical]),
            'unique':len(unique),'unique_hits':sum(d.metrics['top1'] for d in unique),
            'mistakes':sum(d.metrics['cpl']>=CONFIG.mistake_cp for d in decisions),
            'blunders':sum(d.metrics['cpl']>=CONFIG.blunder_cp for d in decisions),
            'critical_mistakes':sum(d.metrics['cpl']>=CONFIG.mistake_cp for d in critical),
            'search_inconsistent':sum(g.metrics.get('search_inconsistent',0) for g in games),
            'near_best':ratio(sum(d.metrics.get('near_best',d.metrics['cpl']<=CONFIG.equivalent_cp) for d in decisions),len(decisions)),
            'scaled_loss':median([d.metrics['scaled_loss'] for d in decisions if 'scaled_loss' in d.metrics]),
            'quiet_critical':sum(d.metrics.get('critical_kind')=='quiet' for d in critical),
            'tactical_critical':sum(d.metrics.get('critical_kind')=='tactical' for d in critical)}


@dataclass
class ReviewResult:
    username: str
    games: list[GameSample]
    selected_games: int
    skipped: dict
    partial: bool
    engine: str
    totals: dict
    classes: dict
    performance: dict
    context: dict
    families: dict
    priority: str
    confidence: str
    reasons: list[str]
    deep_confirmed: bool
    deep_coverage: dict
    elapsed: float
    version: str = VERSION
    timing: dict = field(default_factory=dict)
    clusters: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)


def family_label(value):
    return 'Very High' if value>=.85 else 'High' if value>=.70 else 'Elevated' if value>=.5 else 'Slightly Elevated' if value>=.25 else 'Not elevated'


from fairplay_scoring import priority_model, score_review


class BoundedNodeEngine(chess.engine.SimpleEngine):
    def _timeout_for(self,limit):
        # SimpleEngine returns None for node-only limits, silently disabling its
        # timeout. Keep the search budget deterministic, but bound transport work.
        return self.timeout


class EngineScanner:
    """One independent low-priority Stockfish process per scan; no shared lock."""
    def __init__(self, deadline, config=CONFIG, factory=None):
        self.deadline, self.config = deadline, config
        self.engine = (factory or (lambda:chess_play._create_stockfish_engine(allow_install=False,engine_class=BoundedNodeEngine)))()
        self.engine.timeout = config.engine_timeout
        try:
            options = self.engine.options
            settings = {}
            if 'UCI_LimitStrength' in options:settings['UCI_LimitStrength'] = False
            if 'Threads' in options:settings['Threads'] = 1
            if 'Hash' in options:settings['Hash'] = config.hash_mb
            self.engine.configure(settings)
            self.name = str(self.engine.id.get('name','Stockfish'))[:80]
        except Exception:
            self.close()
            raise
        try:
            os.setpriority(os.PRIO_PROCESS,self.engine.transport.get_pid(),10)
        except (AttributeError, OSError):pass

    def close(self):
        try:self.engine.quit()
        except Exception:
            try:self.engine.close()
            except Exception:pass

    def analyse(self, game, nodes):
        for decision in game.decisions:
            check_deadline(self.deadline)
            if not decision.useful:continue
            if nodes==self.config.deep_nodes and not decision.metrics.get('useful',True) and abs(decision.metrics.get('before_cp',0))>=800:
                continue  # unambiguously decisive fast positions offer negligible evidence
            board = chess.Board(decision.fen)
            # Clear hash between positions so order/cache warmth does not change
            # fixed-node candidate rankings. Threads=1 avoids search races.
            if 'Clear Hash' in self.engine.options:self.engine.configure({'Clear Hash':None})
            richer = (decision.metrics.get('critical') or (decision.metrics.get('gap') is not None
                       and decision.metrics['gap']<25 and decision.metrics.get('cpl',100)<=25))
            multipv=self.config.deep_multipv if nodes==self.config.deep_nodes and richer else 3
            lines = self.engine.analyse(board,chess.engine.Limit(nodes=nodes),multipv=multipv)
            check_deadline(self.deadline)
            if isinstance(lines,dict):lines = [lines]
            move = chess.Move.from_uci(decision.move)
            actual = {}
            if not any(line.get('pv') and line['pv'][0]==move for line in lines):
                if 'Clear Hash' in self.engine.options:self.engine.configure({'Clear Hash':None})
                # Restrict the root to the actual move: same POV/budget, avoiding
                # after-move horizon differences being mistaken for CPL.
                actual = self.engine.analyse(board,chess.engine.Limit(nodes=nodes),root_moves=[move])
            decision.metrics = engine_metrics(decision,lines,actual,game.color,self.config)
            decision.metrics['nodes']=nodes
            if nodes==self.config.fast_nodes:decision.fast_engine=decision.metrics.copy()
        summarize(game,self.config)
        if nodes == self.config.fast_nodes:
            for decision in game.decisions:
                if decision.metrics:decision.fast_engine=decision.metrics.copy()
            game.fast_metrics = {k:v for k,v in game.metrics.items() if k!='timing'}


# Bounded process-local successful game cache, not an account/case ledger.
_game_cache = OrderedDict()


def review(target: str, progress: Callable, config=CONFIG, *, api_factory=PubAPI, engine_factory=None, cancel=None):
    import copy
    started = time.monotonic()
    deadline = ScanDeadline(started+config.deadline_seconds,cancel)
    target = username(target)
    for key,cached in list(_game_cache.items()):
        if started-cached[0]>=3600:_game_cache.pop(key,None)
    api = api_factory(deadline)
    scanner = None
    try:
        progress('Fetching profile…')
        profile = api.get(target,profile=True)
        if profile is None:raise ReviewError('No public profile is available.')
        if not isinstance(profile.get('username'),str):raise ReviewError('Chess.com returned incomplete profile data.')
        canonical = username(profile['username'])
        if canonical!=target:raise ReviewError('The public profile does not match the requested account.')
        profile = neutral_profile_context(profile)
        history, skipped, archive_partial = collect_games(api,canonical,progress,config)
        # Enforce eligibility even when a collector adapter is used.
        history=sorted([g for g in history if g.rated is True],key=lambda g:(g.ended,g.identity))
        if not history:raise ReviewError('No eligible rated standard live games with enough meaningful moves were found.')
        check_deadline(deadline)
        try:scanner = EngineScanner(deadline,config,engine_factory)
        except Exception as error:raise ReviewError('Stockfish is unavailable. Engine screening could not be performed; no review priority was assigned.') from error
        for group in buckets(history).values():
            for index,game in enumerate(group):game.control_index=index
        limit = primary_limit(config)
        primary = history[-limit:]
        analyzed,probes = [],{}
        partial = False
        def fast_scan(game):
            check_deadline(deadline)
            key=(game.identity,game.color,scanner.name,VERSION,config)
            cached=_game_cache.get(key)
            if cached and time.monotonic()-cached[0]<3600:
                game.decisions=copy.deepcopy(cached[1]);game.deep=cached[2]
                game.fast_metrics=copy.deepcopy(cached[3]);summarize(game,config)
            else:scanner.analyse(game,config.fast_nodes)
            analyzed.append(game)
        # Full primary fast pass ALWAYS precedes historical probes/deep searches.
        for index,game in enumerate(reversed(primary)):
            progress(f'Fast engine scan: {index} / {len(primary)}')
            try:fast_scan(game)
            except DeadlineReached:partial=True;break
        primary_complete=len(analyzed)==len(primary)
        progress(f'Fast engine scan: {len(analyzed)} / {len(primary)}')
        historical_targets=[]
        probe_complete=True
        if primary_complete and len(history)>len(primary):
            primary_ids={g.identity for g in primary}
            older=[g for g in history if g.identity not in primary_ids]
            probe_end=min(float(deadline),time.monotonic()+config.deadline_seconds*config.historical_probe_budget_fraction)
            for index,game in enumerate(older):
                if time.monotonic()>=probe_end:probe_complete=False;break
                progress(f'Lightweight history screen: {index} / {len(older)}')
                probe=copy.deepcopy(game);probe.decisions=probe_decisions(probe,config)
                try:
                    scanner.analyse(probe,config.historical_probe_nodes)
                    probes[game.identity]=probe.metrics
                except DeadlineReached:probe_complete=False;break
            progress('Identifying high-signal historical periods…')
            historical_targets=historical_candidates(older,probes,config)
            for index,game in enumerate(historical_targets):
                # Preserve ample runtime for the expensive confirmatory pass.
                if float(deadline)-time.monotonic()<config.deadline_seconds*.15:
                    probe_complete=False;break
                progress(f'Targeted historical engine scan: {index} / {len(historical_targets)}')
                try:fast_scan(game)
                except DeadlineReached:probe_complete=False;break
        analyzed.sort(key=lambda g:(g.ended,g.identity))
        progress('Identifying high-signal clusters…')
        candidates=select_deep_games(analyzed,config)
        deep_incomplete=False
        for index,game in enumerate(candidates):
            progress(f'Deep-reviewing high-signal periods: {index} / {len(candidates)}')
            try:
                confirmed=copy.deepcopy(game)
                scanner.analyse(confirmed,config.deep_nodes)
                game.decisions,game.metrics,game.deep=confirmed.decisions,confirmed.metrics,True
            except DeadlineReached:
                deep_incomplete=True;break
        for game in analyzed:
            if game.deep:
                key=(game.identity,game.color,scanner.name,VERSION,config)
                _game_cache[key]=(time.monotonic(),copy.deepcopy(game.decisions),True,copy.deepcopy(game.fast_metrics))
                _game_cache.move_to_end(key)
        while len(_game_cache)>200:_game_cache.popitem(last=False)
        progress('Comparing personal timing baselines…')
        result=score_review(canonical,analyzed,len(history),skipped,partial or archive_partial or deep_incomplete,
                            scanner.name,profile,time.monotonic()-started,config)
        result.coverage.update(primary_collected=len(primary),primary_fast_scanned=min(len(analyzed),len(primary)) if not primary_complete else len(primary),
                               history_probed=len(probes),history_fast_scanned=sum(g not in primary for g in analyzed),
                               history_probe_complete=probe_complete,deep_incomplete=deep_incomplete,
                               archive_partial=archive_partial,primary_complete=primary_complete,
                               rated_primary=sum(g.rated is True for g in primary),casual_primary=sum(g.rated is False for g in primary),
                               unknown_primary=sum(g.rated is None for g in primary))
        result.history={'collected':len(history),'historical_candidates':len(historical_targets),
                        'sessions':session_history(history,config),'engine_selected':len(analyzed),'context':context_metrics(history,profile),
                        'timing_baselines':[{'class':kind,'control':control,
                            'profile':timing_profile(group,config)}
                            for (kind,control),group in buckets(history).items()]}
        progress('Building report…')
        result.elapsed=time.monotonic()-started
        return result
    except (chess.engine.EngineError,TimeoutError) as error:
        raise ReviewError('Stockfish stopped responding. The scan was stopped safely; please try again later.') from error
    except DeadlineReached:
        raise ReviewError('The review reached its runtime limit before meaningful engine data was available. Try again later.')
    finally:
        api.close()
        if scanner is not None:scanner.close()


def review_interest(game):
    m = game.metrics
    return ((m.get('critical_top1') or 0)*min(m.get('critical',0),15)*4
            + (m.get('top1') or 0)*10 - min(m.get('robust_cpl') or 0,100)/10
            + 5*int(m.get('timing',{}).get('elevated',False)))
