"""Reproducible two-pass evidence screening; no case storage or punishment hooks.

The thresholds rank reviews. They are not empirically calibrated cheating rates.
All engine decisions use the subject's POV; every signal has minimum coverage.
"""
import math
from copy import deepcopy
import os
import queue
import statistics as stats
import time
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError, wait, FIRST_COMPLETED
from dataclasses import dataclass, field, replace
from typing import Callable

import chess
import chess.engine
import chess_play

from fairplay_config import CONFIG, VERSION, ReviewConfig
from fairplay_timing import cadence, clock_values, trivial_delay_metrics, trivial_delay_summary
from fairplay_baseline import engine_data, timing_profile
from fairplay_clusters import (clamp, evidence, find_clusters, regime_changes,
                               select_deep_games, confirm_cluster, summary, buckets, comparison_control)
from fairplay_history import session_history
from fairplay_data import (DeadlineReached, GameSample, PubAPI, ReviewError,
                           ScanDeadline, check_deadline, collect_games, finite_number, username, primary_limit, collection_limit)


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
    inconsistent = (actual > values[0]+config.inconsistent_eval_cp
                    or any(a<b for a,b in zip(values,values[1:]))
                    or any(line.get('lowerbound') or line.get('upperbound') for line in lines)
                    or (actual_line or {}).get('lowerbound') or (actual_line or {}).get('upperbound'))
    inconsistent=bool(inconsistent)
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
            'rank':candidates.index(decision.move)+1 if decision.move in candidates else None,
            'candidate_count':len(candidates),'candidate_cp':values,'candidates':candidates,
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
    compared = [d for d in moves if getattr(d,'fast_engine',{}) and (
                d.metrics.get('nodes',0)>d.fast_engine.get('nodes',0)
                or (d.metrics.get('search_contract',{}).get('mode')=='depth'
                    and d.metrics.get('search_contract',{}).get('completed') is True
                    and d.metrics.get('search_depth',0)>=
                        d.metrics.get('search_contract',{}).get('requested',float('inf'))))]
    # A completed fixed-depth analysis need not consume more nodes than its
    # preceding fixed-node screening. Count actual depth comparisons instead
    # of silently reporting zero paired positions.
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
    from fairplay_human import annotate_game
    annotate_game(game,config)
    if any(d.human_policy for d in game.decisions):
        __import__('fairplay_maia').refresh_game(game)
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
        rolling=[g for g in group if dominant and comparison_control(g)==dominant['control']]
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
    timeline: list[GameSample] = field(default_factory=list)


def family_label(value):
    return 'Very High' if value>=.85 else 'High' if value>=.70 else 'Elevated' if value>=.5 else 'Slightly Elevated' if value>=.25 else 'Not elevated'


from fairplay_scoring import priority_model, score_review


class BoundedNodeEngine(chess.engine.SimpleEngine):
    def _timeout_for(self,limit):
        # SimpleEngine returns None for node-only limits, silently disabling its
        # timeout. Keep the search budget deterministic, but bound transport work.
        return self.timeout


def scan_engine_timeout(limit, config=CONFIG, *, retry=False):
    """Bound searches; give a genuinely timed-out search a larger retry budget.

    Retrying depth-18 MultiPV with exactly the same wall-clock limit can
    deterministically fail on a legitimately expensive position.
    """
    if limit.depth is not None:
        key,default,minimum,maximum='FAIRPLAY_DEPTH18_ENGINE_TIMEOUT_SECONDS',1200,60,7200
    elif limit.nodes == config.deep_nodes:
        key,default,minimum,maximum='FAIRPLAY_DEEP_ENGINE_TIMEOUT_SECONDS',180,30,3600
    else:
        key,default,minimum,maximum='FAIRPLAY_FAST_ENGINE_TIMEOUT_SECONDS',max(30,config.engine_timeout),10,300
    try:
        requested=int(os.getenv(key,str(default)))
    except ValueError:
        requested=default
    seconds=max(minimum,min(maximum,requested))
    # The normal attempt retains its original budget. Only a retry after a
    # TimeoutError gets more time, bounded by the existing phase maximum.
    return min(maximum,seconds*3) if retry else seconds


class EngineScanner:
    """One independent fixed-node Stockfish process per pool worker."""
    def __init__(self, deadline, config=CONFIG, factory=None):
        self.deadline, self.config = deadline, config
        self.last_search = {}  # diagnostic category/budget only; never FEN/user
        self.retry_after_timeout = False  # scoped to a single pool action
        self.profile = {'multipv_seconds':0.0, 'root_seconds':0.0, 'multipv_searches':0, 'root_searches':0,
                        'fast_multipv_seconds':0.0, 'fast_root_seconds':0.0, 'deep_multipv_seconds':0.0, 'deep_root_seconds':0.0}
        self.engine = (factory or (lambda:chess_play._create_stockfish_engine(allow_install=False,engine_class=(__import__('fairplay_engine').CompactNodeEngine if os.name=='posix' else BoundedNodeEngine))))()
        self.engine.timeout = max(10,config.engine_timeout)  # phase-specific timeout set per position
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
        # Fixed nodes and one search thread preserve reproducibility. Yield
        # CPU scheduling priority to interactive bot work under contention.
        try:
            os.setpriority(os.PRIO_PROCESS,self.engine.transport.get_pid(),5)
        except (AttributeError, OSError):pass

    def close(self):
        try:self.engine.quit()
        except Exception:
            try:self.engine.close()
            except Exception:pass

    def analyse_decision(self, game, decision, nodes, *, fast_multipv_override=None):
        check_deadline(self.deadline)
        # Full-depth mode measures every played subject move, including book/
        # forced moves. Existing scoring still excludes non-evidential moves.
        if not decision.useful and not (isinstance(nodes,chess.engine.Limit) and nodes.depth is not None):return False
        depth_search=isinstance(nodes,chess.engine.Limit) and nodes.depth is not None
        if nodes==self.config.deep_nodes and not decision.metrics.get('useful',True) and abs(decision.metrics.get('before_cp',0))>=800:
            return False  # unambiguously decisive fast positions offer negligible evidence
        board = chess.Board(decision.fen)
        # Clear hash between positions so order/cache warmth does not change
        # fixed-node candidate rankings. Threads=1 avoids search races.
        if 'Clear Hash' in self.engine.options:self.engine.configure({'Clear Hash':None})
        richer = (decision.metrics.get('critical') or (decision.metrics.get('gap') is not None
                   and decision.metrics['gap']<25 and decision.metrics.get('cpl',100)<=25))
        multipv=(self.config.deep_multipv if depth_search or nodes==self.config.deep_nodes
                 else (fast_multipv_override if fast_multipv_override is not None
                       else self.config.fast_multipv))
        limit=nodes if depth_search else chess.engine.Limit(nodes=nodes)
        # The worker owns this engine exclusively for both candidate and root searches.
        self.engine.timeout=scan_engine_timeout(
            limit,self.config,retry=self.retry_after_timeout)
        self.last_search={'phase':'deep-candidates' if depth_search or nodes==self.config.deep_nodes else 'fast-candidates',
                          'budget':nodes.depth if depth_search else nodes,
                          'multipv':multipv,'started':time.monotonic(),
                          'timeout_seconds':self.engine.timeout}
        search_started=time.monotonic()
        lines = self.engine.analyse(board,limit,multipv=multipv)
        spent=time.monotonic()-search_started
        self.profile['multipv_seconds']+=spent;self.profile['multipv_searches']+=1
        self.profile['deep_multipv_seconds' if depth_search or nodes==self.config.deep_nodes else 'fast_multipv_seconds']+=spent
        check_deadline(self.deadline)
        if isinstance(lines,dict):lines = [lines]
        if depth_search and (not lines or any(
                int(line.get('depth',0) or 0)<nodes.depth for line in lines)):
            raise chess.engine.EngineError('Depth-18 search ended without full exact depth coverage')
        move = chess.Move.from_uci(decision.move)
        actual = {}
        if not any(line.get('pv') and line['pv'][0]==move for line in lines):
            if 'Clear Hash' in self.engine.options:self.engine.configure({'Clear Hash':None})
            # Restrict the root to the actual move: same POV/budget, avoiding
            # after-move horizon differences being mistaken for CPL.
            self.last_search={'phase':'deep-played-root' if depth_search or nodes==self.config.deep_nodes else 'fast-played-root',
                              'budget':nodes.depth if depth_search else nodes,
                              'multipv':1,'started':time.monotonic(),
                              'timeout_seconds':self.engine.timeout}
            search_started=time.monotonic()
            actual = self.engine.analyse(board,limit,root_moves=[move])
            if depth_search and int(actual.get('depth',0) or 0)<nodes.depth:
                raise chess.engine.EngineError('Played move did not reach required depth 18')
            spent=time.monotonic()-search_started
            self.profile['root_seconds']+=spent;self.profile['root_searches']+=1
            self.profile['deep_root_seconds' if depth_search or nodes==self.config.deep_nodes else 'fast_root_seconds']+=spent
        decision.metrics = engine_metrics(decision,lines,actual,game.color,self.config)
        decision.metrics['search_contract']={
            'engine':self.name,
            'mode':'depth' if depth_search else 'nodes',
            'requested':nodes.depth if depth_search else nodes,
            'multipv':multipv,
            'completed':True,
            'exact':not decision.metrics.get('search_inconsistent',False),
        }
        decision.metrics['nodes']=(max(int(x.get('nodes',0) or 0) for x in lines) if depth_search else nodes)
        if depth_search:
            decision.metrics['search_depth']=min(int(x.get('depth',0) or 0) for x in lines)
        if nodes==self.config.fast_nodes:decision.fast_engine=deepcopy(decision.metrics)
        return True

    def analyse(self, game, nodes):
        for decision in game.decisions:self.analyse_decision(game,decision,nodes)
        summarize(game,self.config)
        if nodes == self.config.fast_nodes:
            for decision in game.decisions:
                if decision.metrics:decision.fast_engine=deepcopy(decision.metrics)
            game.fast_metrics = {k:v for k,v in game.metrics.items() if k!='timing'}


# Bounded process-local successful game cache, not an account/case ledger.
# Engine tasks may access it concurrently, so every structural read/write is
# protected. Cached payloads are immutable-by-convention deep copies.
_game_cache = OrderedDict()
_game_cache_lock = threading.RLock()
_GAME_CACHE_TTL = 3 * 3600
_GAME_CACHE_MAX = 320
_ENGINE_POOL_MAX = 16
_POSITION_CACHE_MAX = 2048


class ExactPositionCache:
    """Bounded volatile cache of completed, exact position evidence only."""
    def __init__(self, capacity=_POSITION_CACHE_MAX):
        self.capacity=max(1,capacity)
        self.entries=OrderedDict()
        self.lock=threading.RLock()
        self.hits=0
        self.misses=0

    def get(self,key):
        with self.lock:
            if key not in self.entries:
                self.misses+=1
                return None
            self.entries.move_to_end(key)
            self.hits+=1
            return deepcopy(self.entries[key])

    def put(self,key,metrics):
        contract=metrics.get('search_contract',{}) if metrics else {}
        if (not metrics or metrics.get('search_inconsistent')
                or contract.get('completed') is not True
                or contract.get('exact') is not True):
            return
        with self.lock:
            self.entries[key]=deepcopy(metrics)
            self.entries.move_to_end(key)
            while len(self.entries)>self.capacity:self.entries.popitem(last=False)

    def snapshot(self):
        with self.lock:return {'hits':self.hits,'misses':self.misses,'entries':len(self.entries)}


def position_cache_key(game,decision,nodes,config=CONFIG):
    """All engine search and position-context inputs affecting decision metrics."""
    depth=nodes.depth if isinstance(nodes,chess.engine.Limit) else None
    budget=(('depth',depth) if depth is not None else
            ('nodes',nodes.nodes if isinstance(nodes,chess.engine.Limit) else nodes))
    multipv=(config.deep_multipv if depth is not None or budget[1]==config.deep_nodes
             else config.fast_multipv)
    return (decision.fen,decision.move,game.color,budget,multipv,
            decision.phase,decision.legal,decision.useful,decision.forced,
            decision.trivial_kind,decision.capture,decision.gives_check,decision.check,
            decision.metrics.get('useful',True),decision.metrics.get('before_cp'))



def available_engine_cpus():
    try:count=len(os.sched_getaffinity(0))
    except (AttributeError,OSError):count=os.cpu_count() or 1
    # Respect container CPU quotas when present; sched_getaffinity alone can
    # expose all host CPUs on some platforms.
    quotas=[]
    try:
        with open('/sys/fs/cgroup/cpu.max',encoding='utf-8') as handle:
            text=handle.read().strip().split()
        if len(text)==2 and text[0]!='max':
            quotas.append(max(1,int(int(text[0])/int(text[1]))))
    except (OSError,ValueError,ZeroDivisionError):pass
    try:
        with open('/sys/fs/cgroup/cpu/cpu.cfs_quota_us',encoding='utf-8') as handle:
            quota=int(handle.read().strip())
        with open('/sys/fs/cgroup/cpu/cpu.cfs_period_us',encoding='utf-8') as handle:
            period=int(handle.read().strip())
        if quota>0 and period>0:quotas.append(max(1,int(quota/period)))
    except (OSError,ValueError,ZeroDivisionError):pass
    return max(1,min([count,*quotas])) if quotas else max(1,count)


def available_engine_memory_mb():
    values=[]
    for path in ('/sys/fs/cgroup/memory.max','/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            with open(path,encoding='utf-8') as handle:text=handle.read().strip()
            if text and text!='max':
                value=int(text)
                # Some cgroup-v1 hosts expose a huge sentinel rather than "max".
                if 0<value<1<<50:values.append(value/(1024*1024))
        except (OSError,ValueError):pass
    # Bare-metal/VM deployments may have no cgroup memory ceiling. In that
    # case physical RAM is still a real bound and should inform pool sizing.
    try:
        physical=int(os.sysconf('SC_PHYS_PAGES'))*int(os.sysconf('SC_PAGE_SIZE'))
        if 0<physical<1<<60:values.append(physical/(1024*1024))
    except (AttributeError,OSError,TypeError,ValueError):pass
    return min(values) if values else None


def shared_engine_pool_size(config=CONFIG):
    """Use available CPU aggressively while keeping bounded Stockfish memory."""
    cpus=available_engine_cpus()
    memory=available_engine_memory_mb()
    # Stockfish hash is the largest predictable allocation. Leave a substantial
    # reserve for Discord/Python/PGNs and process overhead.
    if memory is None:
        memory_cap=_ENGINE_POOL_MAX
    else:
        per_engine=max(256,config.hash_mb+192)
        memory_cap=max(1,int(max(0,memory-768)//per_engine))
    # Sixteen is useful for the 100-game fast pass while remaining bounded;
    # the deep pass itself tops out below this. CPU affinity/quota and RAM can
    # only reduce this value, never inflate it.
    return max(1,min(_ENGINE_POOL_MAX,cpus,memory_cap))


def automatic_engine_workers():
    # Compatibility/local-test helper. Production uses the shared pool below.
    return shared_engine_pool_size(CONFIG)


def engine_profile(scanners):
    totals={}
    for scanner in scanners:
        for key,value in getattr(scanner,'profile',{}).items():
            totals[key]=totals.get(key,0)+value
    return totals


class SharedEnginePool:
    """Process-wide Stockfish pool shared by all concurrent Fair Play reviews."""
    def __init__(self,config=CONFIG,*,size=None,factory=None):
        self.config=config
        self.size=max(1,int(size or shared_engine_pool_size(config)))
        self.factory=factory
        self.available=queue.LifoQueue()
        self.scanners=[]
        self.closed=False
        self.failed=threading.Event()
        self.restarts=0  # aggregate engine health, not user data
        self.last_failure=None  # last bounded engine event for private owner audit
        self.position_cache=ExactPositionCache()
        try:
            for _ in range(self.size):
                scanner=EngineScanner(None,config,factory)
                if self.scanners and scanner.name!=self.scanners[0].name:
                    scanner.close()
                    raise ReviewError('Stockfish worker versions do not match.')
                self.scanners.append(scanner);self.available.put(scanner)
        except Exception:
            self.close()
            raise
        self.name=self.scanners[0].name
        self.supports_decision_tasks=all(callable(getattr(scanner,'analyse_decision',None)) for scanner in self.scanners)

    def _run_with_scanner(self,deadline,action):
        # Retry one failed position after replacing a broken engine worker.
        # Never record incomplete searches or silently drop evidence.
        retry_after_timeout=False
        for attempt in range(2):
            while True:
                if self.closed or self.failed.is_set():
                    raise ReviewError('Stockfish pool is unavailable; please retry the review.')
                check_deadline(deadline)
                try:
                    scanner=self.available.get(timeout=.1)
                    break
                except queue.Empty:continue
            reusable=True
            try:
                scanner.deadline=deadline
                scanner.retry_after_timeout=retry_after_timeout
                return action(scanner)
            except (chess.engine.EngineError,TimeoutError,OSError) as error:
                retry_after_timeout=isinstance(error,TimeoutError)
                reusable=False
                try:scanner.close()
                except Exception:pass
                recovered=False
                try:
                    replacement=EngineScanner(None,self.config,self.factory)
                    if replacement.name!=self.name:
                        replacement.close()
                        raise ReviewError('Stockfish worker version changed.')
                    self.scanners[self.scanners.index(scanner)]=replacement
                    if self.closed:replacement.close()
                    else:self.available.put(replacement)
                    self.restarts+=1
                    recovered=not self.closed
                except Exception:
                    self.failed.set()
                # Log categories only: no username, FEN or raw exception data.
                reason=('timeout' if isinstance(error,TimeoutError) else
                        'terminated' if isinstance(error,chess.engine.EngineTerminatedError)
                        else 'uci-error')
                # Only bounded execution metadata, never user/account/FEN, is
                # allowed into production logs. This identifies which phase
                # really causes the repeated 55-minute failures.
                search=getattr(scanner,'last_search',{})
                if not isinstance(search,dict):search={}
                phase=search.get('phase','unknown')
                budget=search.get('budget','unknown')
                multipv=search.get('multipv','unknown')
                started=search.get('started')
                seconds=(round(max(0,time.monotonic()-started),1)
                         if isinstance(started,(int,float)) else 'unknown')
                timeout_seconds=search.get('timeout_seconds','unknown')
                # Structured categories only. Never retain FEN, move, target,
                # raw exception text or subprocess stderr in diagnostics.
                self.last_failure={
                    'category':reason,
                    'phase':phase if phase in (
                        'fast-candidates','fast-played-root','deep-candidates',
                        'deep-played-root','maia-counterfactual') else 'unknown',
                    'attempt':attempt+1,
                    'budget':budget if isinstance(budget,(int,float)) else 'unknown',
                    'multipv':multipv if isinstance(multipv,int) else 'unknown',
                    'elapsed_seconds':seconds,
                    'timeout_seconds':timeout_seconds if isinstance(
                        timeout_seconds,(int,float)) else 'unknown',
                    'worker_restarted':recovered,
                    'terminal':not recovered or attempt==1}
                print(f'Fair Play engine worker {reason}; '
                      f'{"restarted" if recovered else "restart-failed"} '
                      f'(attempt {attempt+1}/2; phase={phase}; '
                      f'budget={budget}; multipv={multipv}; seconds={seconds}; '
                      f'timeout_seconds={timeout_seconds})',flush=True)
                if not recovered or attempt==1:raise
            finally:
                scanner.deadline=None
                scanner.retry_after_timeout=False
                if reusable and not self.closed:self.available.put(scanner)

    def run(self,game,nodes,deadline):
        self._run_with_scanner(deadline,lambda scanner:scanner.analyse(game,nodes))
        return game

    def run_decision(self,game,decision,nodes,deadline,fast_multipv_override=None):
        # Reuse only exact same FEN, move, side, budget, MultiPV, context.
        check_deadline(deadline)
        cache=getattr(self,'position_cache',None)
        search_config=(replace(self.config,fast_multipv=fast_multipv_override)
                       if fast_multipv_override is not None else self.config)
        key=position_cache_key(game,decision,nodes,search_config) if cache is not None else None
        cached=cache.get(key) if cache is not None else None
        if cached is not None:
            decision.metrics=cached
            if nodes==self.config.fast_nodes:decision.fast_engine=deepcopy(cached)
            return True
        result=self._run_with_scanner(
            deadline,lambda scanner:(scanner.analyse_decision(game,decision,nodes)
                if fast_multipv_override is None else scanner.analyse_decision(
                    game,decision,nodes,fast_multipv_override=fast_multipv_override)))
        if result and cache is not None:cache.put(key,decision.metrics)
        return result

    def close(self):
        if self.closed:return
        self.closed=True
        for scanner in list(self.scanners):scanner.close()
        self.scanners.clear()


_shared_engine_pool=None
_shared_engine_pool_lock=threading.Lock()


def get_shared_engine_pool(config=CONFIG):
    global _shared_engine_pool
    with _shared_engine_pool_lock:
        if (_shared_engine_pool is None or _shared_engine_pool.closed
                or _shared_engine_pool.failed.is_set()):
            if _shared_engine_pool is not None:_shared_engine_pool.close()
            _shared_engine_pool=SharedEnginePool(config)
        return _shared_engine_pool


def close_shared_engine_pool():
    global _shared_engine_pool
    with _shared_engine_pool_lock:
        if _shared_engine_pool is not None:_shared_engine_pool.close()
        _shared_engine_pool=None
    __import__('fairplay_maia').close_worker()


def run_position_batch(executor, pool, work, nodes, deadline, progress, stage,
                       checkpoint=None, target=None, phase=None, checkpoint_config=CONFIG,
                       checkpoint_full_depth=False, budget_for_game=None,
                       fast_multipv_override=None):
    """Drain running tasks and preserve only fully completed games at a deadline.

    A cancelled/failed position never becomes invented engine evidence. Workers
    finish before callers summarize or cache any mutable decision objects.
    """
    expected={}
    done_by_game={}
    pending=[]
    for game,decision in work:
        key=id(game)
        expected[key]=expected.get(key,0)+1
        if checkpoint is not None and checkpoint.restore(
                target,game,decision,phase,checkpoint_config,checkpoint_full_depth):
            done_by_game[key]=done_by_game.get(key,0)+1
        else:
            pending.append((game,decision))
    # Schedule only a bounded multiple of active workers. In particular, a
    # 500-game scan must not create thousands of queued futures up front.
    max_workers=max(1,int(getattr(executor,'_max_workers',getattr(pool,'size',1))))
    capacity=min(64,max_workers*3)
    pending_iter=iter(pending)
    futures={}
    interrupted=False
    def submit_available():
        while len(futures)<capacity:
            try:game,decision=next(pending_iter)
            except StopIteration:break
            budget=budget_for_game(game) if budget_for_game else nodes
            if fast_multipv_override is None:
                future=executor.submit(pool.run_decision,game,decision,budget,deadline)
            else:
                future=executor.submit(pool.run_decision,game,decision,budget,deadline,
                                       fast_multipv_override)
            futures[future]=(game,decision)
    submit_available()
    # Avoid encrypting and rewriting the entire growing checkpoint per position.
    # Bound the undurable tail and flush completed work even when interrupted.
    buffered=0;last_flush=time.monotonic()
    total=len(work);done=sum(done_by_game.values());tick=max(1,total//100)
    if total:progress(f'{stage}: {done} / {total} positions')
    try:
        while futures:
            completed,_=wait(tuple(futures),return_when=FIRST_COMPLETED)
            for future in completed:
                game,decision=futures.pop(future)
                try:future.result()
                except (DeadlineReached,CancelledError):
                    interrupted=True
                else:
                    key=id(game)
                    done_by_game[key]=done_by_game.get(key,0)+1
                    if checkpoint is not None:
                        checkpoint.record(target,game,decision,phase,persist=False)
                        buffered+=1
                        if buffered>=64 or time.monotonic()-last_flush>=15:
                            checkpoint.flush(force=False)
                            buffered=0;last_flush=time.monotonic()
                done+=1
                if done%tick==0 or done==total:
                    progress(f'{stage}: {done} / {total} positions')
            if interrupted:
                for running in futures:running.cancel()
            else:
                submit_available()
    except BaseException:
        for outstanding in futures:outstanding.cancel()
        # Drain running work before propagating failure. Successful *exact*
        # positions must survive in the encrypted checkpoint even when
        # another engine times out, so the next scan does not repeat them.
        for outstanding,(game,decision) in futures.items():
            try:completed=outstanding.result()
            except BaseException:continue
            if completed and checkpoint is not None:
                checkpoint.record(target,game,decision,phase,persist=False)
        raise
    finally:
        if checkpoint is not None and pending:
            # Even an exact multiple of 64 must be pushed on handoff; the
            # preceding throttled flush may not have reached the remote yet.
            checkpoint.flush()
    return {key for key,count in expected.items() if done_by_game.get(key)==count},interrupted


def full_depth_budget(game, config=CONFIG):
    """Full-depth mode: bullet is depth-12 screening, rapid/blitz depth-18."""
    return chess.engine.Limit(depth=(config.bullet_deep_depth if game.time_class=='bullet' else 18))


def review(target: str, progress: Callable, config=CONFIG, *, api_factory=PubAPI, engine_factory=None,
           cancel=None, engine_workers=None, engine_pool=None, checkpoint=None):
    import copy
    started = time.monotonic()
    full_depth_mode=(os.getenv('FAIRPLAY_FULL_DEPTH18')=='1' and config==CONFIG)
    v21_mode=bool(full_depth_mode and os.getenv('FAIRPLAY_V21')=='1')
    distributed_mode=bool(v21_mode and os.getenv('FAIRPLAY_DISTRIBUTED')=='1'
                          and engine_factory is None and engine_pool is None
                          and os.getenv('GITHUB_ACTIONS')=='true')
    # Full depth 18 may legitimately take longer than four hours. A zero
    # configured ceiling means no scan-level time limit; runner rotations are
    # handled by durable checkpoints instead of returning a partial verdict.
    duration=(max(0,int(os.getenv('FAIRPLAY_FULL_SCAN_DEADLINE_SECONDS','0')))
              if full_depth_mode else config.deadline_seconds)
    deadline = ScanDeadline(started+duration if duration else float('inf'),cancel)
    target = username(target)
    with _game_cache_lock:
        for key,cached in list(_game_cache.items()):
            if started-cached[0]>=_GAME_CACHE_TTL:_game_cache.pop(key,None)
    api = api_factory(deadline)
    scanners = []
    engine_executor = None
    shared_pool = engine_pool
    scanner = None
    shared_profile_before = {}
    pool_restarts_before = 0
    position_cache_before={'hits':0,'misses':0}
    fast_position_tasks = 0
    deep_position_tasks = 0
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
        history=sorted([g for g in history if g.rated is True],key=lambda g:(g.ended,g.identity))[-collection_limit(config):]
        if not history:raise ReviewError('No eligible rated standard live games with enough meaningful moves were found.')
        check_deadline(deadline)
        use_shared=(engine_factory is None and engine_workers is None and config==CONFIG)
        try:
            if use_shared:
                shared_pool=shared_pool or get_shared_engine_pool(config)
                shared_pool.last_failure=None  # no diagnostics from a previous review
                requested_workers=shared_pool.size
                engine_name=shared_pool.name
                shared_profile_before=engine_profile(shared_pool.scanners)
                pool_restarts_before=getattr(shared_pool,'restarts',0)
                position_cache_before=(shared_pool.position_cache.snapshot()
                                       if getattr(shared_pool,'position_cache',None) is not None else position_cache_before)
            else:
                requested_workers=(1 if engine_factory is not None and engine_workers is None
                                   else automatic_engine_workers() if engine_workers is None
                                   else max(1,min(_ENGINE_POOL_MAX,int(engine_workers))))
                for _ in range(requested_workers):scanners.append(EngineScanner(deadline,config,engine_factory))
                scanner=scanners[0]
                if any(item.name!=scanner.name for item in scanners[1:]):
                    raise ReviewError('Stockfish worker versions do not match.')
                engine_name=scanner.name
            if requested_workers>1:
                engine_executor=ThreadPoolExecutor(max_workers=requested_workers,thread_name_prefix='fairplay-engine')
        except Exception as error:
            for item in scanners:item.close()
            scanners.clear()
            raise ReviewError('Stockfish is unavailable. Engine screening could not be performed; no review priority was assigned.') from error
        if checkpoint is not None:
            from fairplay_maia import MODEL_SHA256
            checkpoint.bind(target,engine=engine_name,version=VERSION,config=config,
                            full_depth=full_depth_mode,maia=MODEL_SHA256)
        for group in buckets(history).values():
            for index,game in enumerate(group):game.control_index=index
        collected_at=time.monotonic()
        limit = primary_limit(config)
        v21_plan = None
        if v21_mode:
            from fairplay_v21 import broad_and_core
            v21_plan=broad_and_core(history,broad_count=limit,
                                     deep_count=200 if distributed_mode else 100)
            primary=list(v21_plan.primary)
            progress(f'Review plan: {len(primary)} broad games; {len(v21_plan.core)} peer-matched deep games')
        else:
            primary = history[-limit:]
        analyzed,probes = [],{}
        cached_deep = {}
        partial = False
        def fast_scan(game,worker=None,*,prepare_only=False):
            worker=worker or scanner
            check_deadline(deadline)
            key=(game.identity,game.color,engine_name,VERSION,config,full_depth_mode,
                 config.fast_nodes,config.fast_multipv,config.deep_nodes,config.deep_multipv,
                 __import__('fairplay_maia').MODEL_SHA256)
            with _game_cache_lock:
                cached=_game_cache.get(key)
                if cached and time.monotonic()-cached[0]<_GAME_CACHE_TTL:
                    cached=(cached[0],copy.deepcopy(cached[1]),cached[2],copy.deepcopy(cached[3]),
                            copy.deepcopy(cached[4]) if len(cached)>4 else None)
                else:
                    cached=None
            cached_payload=None
            if cached:
                if cached[2]:cached_payload=(copy.deepcopy(cached[1]),copy.deepcopy(cached[4]))
                game.decisions=cached[1]
                # Discovery always sees equal-budget fast evidence. A warm
                # cache must not add another ten deep games on every re-scan.
                for decision in game.decisions:
                    decision.metrics=copy.deepcopy(decision.fast_engine)
                game.deep=False
                game.fast_metrics=cached[3]
                game.metrics=copy.deepcopy(cached[3])
                game.metrics['timing']=timing_metrics(game,config)
            elif not prepare_only:
                if checkpoint is None:
                    if shared_pool is not None and use_shared:shared_pool.run(game,config.fast_nodes,deadline)
                    else:worker.analyse(game,config.fast_nodes)
                else:
                    for decision in game.decisions:
                        if not decision.useful:continue
                        if checkpoint.restore(target,game,decision,'fast',config,full_depth_mode):continue
                        if shared_pool is not None and use_shared:
                            shared_pool.run_decision(game,decision,config.fast_nodes,deadline)
                        else:worker.analyse_decision(game,decision,config.fast_nodes)
                        checkpoint.record(target,game,decision,'fast')
                    summarize(game,config)
                    for decision in game.decisions:
                        if decision.metrics:decision.fast_engine=deepcopy(decision.metrics)
                    game.fast_metrics={k:v for k,v in game.metrics.items() if k!='timing'}
            return game,cached_payload,bool(cached)
        # Full primary fast pass always precedes deep confirmation.
        ordered_primary=list(reversed(primary))
        # Shared-pool production can queue the whole primary pass at once.
        # ThreadPoolExecutor still runs only requested_workers tasks concurrently,
        # but a worker that finishes an unusually short game immediately takes
        # the next queued game instead of idling behind a fixed batch barrier.
        # Futures are resolved/committed in source order below, preserving
        # deterministic review output.
        step=(len(ordered_primary) if use_shared and engine_executor is not None
              else requested_workers)
        for offset in range(0,len(ordered_primary),step):
            batch=ordered_primary[offset:offset+step]
            progress(f'Fast engine scan: {offset} / {len(primary)}')
            try:
                if use_shared and engine_executor is not None and shared_pool.supports_decision_tasks:
                    # Keep the entire fixed-node fast budget, but dispatch each
                    # independent position to the next available Threads=1 engine.
                    # In particular, a long final game cannot strand other cores.
                    prepared=[fast_scan(game,prepare_only=True) for game in batch]
                    work=[(i,game,decision)
                          for i,(game,_,cache_hit) in enumerate(prepared) if not cache_hit
                          for decision in game.decisions if decision.useful]
                    fast_position_tasks+=len(work)
                    complete_ids,interrupted=run_position_batch(
                        engine_executor,shared_pool,[(game,d) for _,game,d in work],
                        config.fast_nodes,deadline,progress,'Fast engine scan',
                        checkpoint=checkpoint,target=target,phase='fast',
                        checkpoint_config=config,checkpoint_full_depth=full_depth_mode)
                    if interrupted:
                        partial=True
                        prepared=[item for item in prepared if item[2] or id(item[0]) in complete_ids]
                    # Equivalent to EngineScanner.analyse(fast_nodes): summarize
                    # only after every position in that game has finished.
                    for game,_,cache_hit in prepared:
                        if not cache_hit:
                            summarize(game,config)
                            for decision in game.decisions:
                                if decision.metrics:decision.fast_engine=deepcopy(decision.metrics)
                            game.fast_metrics={k:v for k,v in game.metrics.items() if k!='timing'}
                    completed=[(game,payload) for game,payload,_ in prepared]
                elif engine_executor is None:
                    completed=[fast_scan(batch[0],scanner)[:2]]
                else:
                    workers=([None]*len(batch) if use_shared else scanners[:len(batch)])
                    futures=[engine_executor.submit(fast_scan,game,item)
                             for game,item in zip(batch,workers)]
                    # Report completions as they happen, but commit the results
                    # in source order so detector output remains deterministic.
                    slots=[None]*len(futures)
                    positions={future:i for i,future in enumerate(futures)}
                    done=0
                    for future in as_completed(futures):
                        slots[positions[future]]=future.result()[:2]
                        done+=1
                        progress(f'Fast engine scan: {offset+done} / {len(primary)}')
                    completed=slots
                for game,cached_payload in completed:
                    analyzed.append(game)
                    if cached_payload is not None:cached_deep[game.identity]=cached_payload
                if partial:break
            except DeadlineReached:
                partial=True;break
        primary_complete=len(analyzed)==len(primary)
        progress(f'Fast engine scan: {len(analyzed)} / {len(primary)}')
        v21_deep=[]
        v21_candidate_positions=0
        distributed_handle=None
        distributed_completed={}
        if v21_mode and primary_complete:
            # Run the 500-game wide screen first. Then combine 100 recent
            # comparable-rating peer games with up to 25 stratified historical
            # periods/controls. Selection NEVER uses published Chess.com Accuracy.
            from fairplay_v21 import discovery_extras
            v21_plan=discovery_extras(v21_plan,
                                      max_extra=50 if distributed_mode else 25)
            chosen_ids={g.identity for g in v21_plan.deep}
            v21_deep=[g for g in analyzed if g.identity in chosen_ids]
            work=[(game,decision) for game in v21_deep
                  for decision in game.decisions
                  if decision.metrics and decision.metrics.get('useful')]
            v21_candidate_positions=len(work)
            progress(f'Fast candidate verification: 0 / {len(work)} positions')
            if work:
                if use_shared and engine_executor is not None and shared_pool.supports_decision_tasks:
                    _,v21_interrupted=run_position_batch(
                        engine_executor,shared_pool,work,config.fast_nodes,
                        deadline,progress,'Fast candidate verification',
                        checkpoint=checkpoint,target=target,phase='fast-pv3',
                        checkpoint_config=replace(config,fast_multipv=3),
                        checkpoint_full_depth=False,fast_multipv_override=3)
                else:
                    v21_interrupted=False
                    # Test/custom scanner fallback preserves the same evidence
                    # semantics. Never treat a PV1 restored move as PV3.
                    for i,(game,decision) in enumerate(work,1):
                        check_deadline(deadline)
                        if (checkpoint is not None and checkpoint.restore(
                                target,game,decision,'fast-pv3',
                                replace(config,fast_multipv=3),False)):
                            pass
                        else:
                            if use_shared and shared_pool is not None:
                                shared_pool.run_decision(game,decision,config.fast_nodes,
                                                         deadline,3)
                            else:
                                scanner.analyse_decision(game,decision,config.fast_nodes,
                                    fast_multipv_override=3)
                            if checkpoint is not None:
                                checkpoint.record(target,game,decision,'fast-pv3')
                        if i%max(1,len(work)//100)==0:
                            progress(f'Fast candidate verification: {i} / {len(work)} positions')
                if v21_interrupted:
                    raise ReviewError('MultiPV-3 fast candidate verification was interrupted; no v21 review result was issued.')
            # Paired semantic checks now have the same number of candidate
            # variations in both passes. Store the exact fast snapshot before
            # depth18 overwrites the played-position metrics.
            for game in v21_deep:
                summarize(game,config)
                for decision in game.decisions:
                    if decision.metrics:
                        decision.fast_engine=deepcopy(decision.metrics)
                game.fast_metrics={k:deepcopy(v) for k,v in game.metrics.items() if k!='timing'}
            progress(f'Fast candidate verification: {len(work)} / {len(work)} positions')
            if distributed_mode and v21_deep:
                from fairplay_distributed import start as start_distributed
                # Dispatch BEFORE Maia, so ten external Stockfish runners
                # work while the controller computes human-policy alternatives.
                distributed_handle=start_distributed(
                    v21_deep,target,revision=os.getenv('GITHUB_SHA',''),
                    engine=engine_name)
        fast_finished=time.monotonic()
        historical_targets=[]
        probe_complete=True
        # Older rated games remain raw context only: never fabricate engine
        # baselines from sparse probes or from unscanned decisions.
        context_only = [g for g in history if g.identity not in {x.identity for x in analyzed}]
        analyzed.sort(key=lambda g:(g.ended,g.identity))
        progress('Building human-move profile…')
        resume_kwargs=({'checkpoint':checkpoint,'target':target}
                       if checkpoint is not None else {})
        neural_reference=(
            __import__('fairplay_maia').annotate_history(
                v21_deep if v21_mode else analyzed,
                full_coverage=False,**resume_kwargs)
            if (primary_complete and time.monotonic()<deadline
                and not (deadline.cancel is not None and deadline.cancel.is_set()))
            else {'available':False,'positions':0,
                  'reason':'Primary engine pass incomplete or interrupted; optional human reference skipped.'})
        if (full_depth_mode and os.getenv('FAIRPLAY_REQUIRE_MAIA')=='1'
                and not neural_reference.get('available')):
            raise ReviewError('Complete depth-18 review requires local Maia-3; the model was unavailable.')
        from fairplay_policy import complete as complete_policy, allocate as allocate_policy
        if neural_reference.get('available'):
            progress('Comparing human alternatives…')
            neural_reference['fast_counterfactual']=complete_policy(
                v21_deep if v21_mode else analyzed,config.fast_nodes,deadline,executor=engine_executor,
                pool=shared_pool if use_shared else None,scanner=scanner,fast=True,
                **resume_kwargs)
            if (full_depth_mode and os.getenv('FAIRPLAY_REQUIRE_MAIA')=='1'
                    and not neural_reference['fast_counterfactual']['complete']):
                raise ReviewError('The complete Maia fast reference did not finish; no review was issued.')
        progress('Analyzing sessions and repertoire…')
        from fairplay_sequence import class_periods, adaptive_deep_games, confirmation_extension
        gameplay_periods=(class_periods(v21_deep,config,strict_original_sequence=True)
                          if v21_mode else class_periods(analyzed,config))
        candidates=(list(v21_deep) if v21_mode else
                    list(analyzed) if full_depth_mode else
                    allocate_policy(analyzed,adaptive_deep_games(analyzed,config,periods=gameplay_periods),config))
        deep_budget=chess.engine.Limit(depth=18) if full_depth_mode else config.deep_nodes
        game_deep_budget=(lambda game:full_depth_budget(game,config)) if full_depth_mode else (lambda game:deep_budget)
        deep_started=(distributed_handle["started"]
                      if distributed_handle is not None else time.monotonic())
        deep_incomplete=False
        if distributed_handle is not None:
            from fairplay_distributed import join as join_distributed
            # A missing or corrupt shard is NEVER assumed complete. Continue
            # with the existing local depth-18/depth-12 path for those games.
            distributed_completed=join_distributed(
                distributed_handle,progress,deadline)
            progress(f'Fair Play compute: {len(distributed_completed)} / '
                     f'{len(v21_deep)} remotely confirmed games')
        index=0
        def deep_scan(game,worker=None):
            confirmed=copy.deepcopy(game)
            if game.identity in cached_deep:
                decisions,metrics=cached_deep[game.identity]
                confirmed.decisions=copy.deepcopy(decisions)
                if metrics is not None:
                    confirmed.metrics=copy.deepcopy(metrics)
                else:
                    summarize(confirmed,config)
            else:
                if checkpoint is None:
                    if shared_pool is not None and use_shared:shared_pool.run(confirmed,game_deep_budget(game),deadline)
                    else:worker.analyse(confirmed,game_deep_budget(game))
                else:
                    for decision in confirmed.decisions:
                        if checkpoint.restore(target,confirmed,decision,'deep',config,full_depth_mode):continue
                        if shared_pool is not None and use_shared:
                            shared_pool.run_decision(confirmed,decision,game_deep_budget(game),deadline)
                        else:worker.analyse_decision(confirmed,decision,game_deep_budget(game))
                        checkpoint.record(target,confirmed,decision,'deep')
                    summarize(confirmed,config)
            return confirmed
        while index<len(candidates):
            # Extension candidates are still selected only after the original
            # wave completes. In production, however, the wave is balanced at
            # position granularity: a long game can no longer strand idle cores.
            batch=(candidates[index:] if use_shared and engine_executor is not None
                   else candidates[index:index+requested_workers])
            progress(f'Deep confirmation: {index} / {len(candidates)}')
            try:
                if use_shared and engine_executor is not None and shared_pool.supports_decision_tasks:
                    confirmed_batch=[]
                    work=[]
                    for game in batch:
                        confirmed=copy.deepcopy(game)
                        confirmed_batch.append(confirmed)
                        if game.identity in distributed_completed:
                            confirmed_batch[-1]=copy.deepcopy(distributed_completed[game.identity])
                            continue
                        if game.identity in cached_deep:
                            decisions,metrics=cached_deep[game.identity]
                            confirmed.decisions=copy.deepcopy(decisions)
                            if metrics is not None:confirmed.metrics=copy.deepcopy(metrics)
                            else:summarize(confirmed,config)
                            continue
                        for decision in confirmed.decisions:
                            work.append((confirmed,decision))
                    deep_position_tasks+=len(work)
                    complete_ids,interrupted=run_position_batch(
                        engine_executor,shared_pool,work,deep_budget,deadline,progress,
                        'Depth-18 rapid/blitz · depth-12 bullet' if full_depth_mode else 'Deep confirmation',
                        checkpoint=checkpoint,target=target,phase='deep',
                        checkpoint_config=config,checkpoint_full_depth=full_depth_mode,
                        budget_for_game=game_deep_budget)
                    if interrupted:
                        deep_incomplete=True
                        complete_pairs=[(game,confirmed) for game,confirmed in zip(batch,confirmed_batch)
                                        if game.identity in cached_deep or id(confirmed) in complete_ids]
                        batch=[game for game,_ in complete_pairs]
                        confirmed_batch=[confirmed for _,confirmed in complete_pairs]
                    for game,confirmed in zip(batch,confirmed_batch):
                        if game.identity in distributed_completed:
                            # Never trust a remote per-game aggregate. Rebuild
                            # it locally from individually verified positions.
                            summarize(confirmed,config)
                        elif game.identity not in cached_deep:
                            summarize(confirmed,config)
                elif engine_executor is None:
                    confirmed_batch=[deep_scan(batch[0],scanner)]
                else:
                    workers=([None]*len(batch) if use_shared else scanners[:len(batch)])
                    futures=[engine_executor.submit(deep_scan,game,item)
                             for game,item in zip(batch,workers)]
                    slots=[None]*len(futures)
                    positions={future:i for i,future in enumerate(futures)}
                    for future in as_completed(futures):
                        slots[positions[future]]=future.result()
                    confirmed_batch=slots
                for game,confirmed in zip(batch,confirmed_batch):
                    __import__('fairplay_maia').carry_policy_after_deep(game,confirmed)
                    game.decisions,game.metrics,game.deep=confirmed.decisions,confirmed.metrics,True
                    game.human_reference=confirmed.human_reference
                index+=len(batch)
                if deep_incomplete:break
                if index==len(candidates) and not full_depth_mode:
                    # One bounded extension; completed games are never rerun.
                    extra=confirmation_extension(analyzed,config,periods=gameplay_periods)
                    candidates.extend(g for g in extra if g not in candidates)
            except DeadlineReached:
                deep_incomplete=True;break
        if neural_reference.get('available'):
            deep_groups=([([g for g in analyzed if g.deep and g.time_class!='bullet'],deep_budget),
                          ([g for g in analyzed if g.deep and g.time_class=='bullet'],
                           chess.engine.Limit(depth=config.bullet_deep_depth))]
                         if full_depth_mode else [([g for g in analyzed if g.deep],deep_budget)])
            policy_reports=[complete_policy(group,budget,deadline,
                executor=engine_executor,pool=shared_pool if use_shared else None,scanner=scanner,
                **resume_kwargs) for group,budget in deep_groups if group]
            neural_reference['deep_counterfactual']={
                'complete':all(row['complete'] for row in policy_reports),
                'positions':sum(row['positions'] for row in policy_reports),
                'seconds':sum(row['seconds'] for row in policy_reports)}
            if (full_depth_mode and os.getenv('FAIRPLAY_REQUIRE_MAIA')=='1'
                    and not neural_reference['deep_counterfactual']['complete']):
                raise ReviewError('The complete depth-18 Maia comparison did not finish; no review was issued.')
        deep_finished=time.monotonic()
        if full_depth_mode and (deep_incomplete or not primary_complete or any(not g.deep for g in candidates)):
            raise ReviewError(f'All {len(primary)} primary games must complete their required engine depths before a priority can be issued.')
        progress(f'Deep confirmation: {sum(g.deep for g in candidates)} / {len(candidates)}')
        with _game_cache_lock:
            for game in analyzed:
                # Never freeze an explicitly non-exact MultiPV/root search
                # into the 3-hour warm cache: the next scan must be allowed
                # to re-check uncertain evidence rather than copy a LOW.
                inconsistent=any(
                    d.fast_engine.get('search_contract',{}).get('exact') is False
                    or (game.deep and d.metrics.get('search_contract',{}).get('exact') is False)
                    for d in game.decisions)
                if game.fast_metrics and not inconsistent:
                    key=(game.identity,game.color,engine_name,VERSION,config,full_depth_mode,
                 config.fast_nodes,config.fast_multipv,config.deep_nodes,config.deep_multipv,
                 __import__('fairplay_maia').MODEL_SHA256)
                    _game_cache[key]=(time.monotonic(),copy.deepcopy(game.decisions),game.deep,
                                      copy.deepcopy(game.fast_metrics),copy.deepcopy(game.metrics) if game.deep else None)
                    _game_cache.move_to_end(key)
            while len(_game_cache)>_GAME_CACHE_MAX:_game_cache.popitem(last=False)
        progress('Comparing personal timing baselines…')
        collection_coverage = getattr(api, 'fairplay_collection_coverage', {})
        if not isinstance(collection_coverage,dict):collection_coverage={}
        primary_archive_partial = collection_coverage.get('primary_archive_partial', archive_partial)
        coverage_state = {
            'primary_engine_complete':primary_complete and not primary_archive_partial,
            'required_deep_complete':not deep_incomplete,
            'context_history_complete':not archive_partial,
            'optional_context_partial':archive_partial and not primary_archive_partial,
            'context_only':len(context_only),
            'available_archive_months':collection_coverage.get('available_archive_months'),
            'visited_archive_months':collection_coverage.get('visited_archive_months'),
            'unvisited_archive_months':collection_coverage.get('unvisited_archive_months'),
            'eligible_games_capped':collection_coverage.get('eligible_games_capped'),
            'requested_context_limit':collection_limit(config),
            'requested_primary_limit':primary_limit(config),
            'deep_scope':('200 recent peer games + up to 50 stratified historical peer games' if distributed_mode
                          else '100 recent peer games + up to 25 stratified historical peer games') if v21_mode
                          else 'full legacy deep scope',
            'deep_scope_required':len(v21_deep) if v21_mode else len(primary) if full_depth_mode else None,
            'deep_scope_complete':all(g.deep for g in v21_deep) if v21_mode else None,
        }
        # Missing *older* optional archives must not veto independent HIGH
        # evidence in a fully scanned primary sample. Preserve the missing
        # historical context as a separate, explicit coverage warning.
        primary_partial=partial or primary_archive_partial or deep_incomplete
        # Shallow PV1 history has no measurable candidate spread. Never let it
        # dilute PV3 quality statistics or earn deep-confirmed priority. Its
        # exact 500-game broad scan remains available for discovery and audit.
        scoring_games=v21_deep if v21_mode else analyzed
        result=score_review(canonical,scoring_games,len(history),skipped,primary_partial,
                            engine_name,profile,time.monotonic()-started,config,
                            coverage_state=coverage_state,context_games=history,
                            gameplay_periods=gameplay_periods)
        from fairplay_policy import integrate as integrate_policy
        result=(integrate_policy(result,scoring_games,config,
                                 strict_original_sequence=True)
                if v21_mode else integrate_policy(result,scoring_games,config))
        # Explain which paired comparisons are missing versus genuinely
        # unstable. This does not modify either thresholds or priority.
        from fairplay_stability_audit import summarise_stability
        result.diagnostics['paired_stability_audit']=summarise_stability(
            scoring_games,(result.diagnostics.get('gameplay',{}).get('best') or {}).get('ids',()))
        # Astra evidence accounting is strictly observational. Production
        # eligibility, confidence and classifications were already frozen.
        from fairplay_evidence_audit import audit_engine_sample
        result.diagnostics['evidence_audit']=audit_engine_sample(
            scoring_games,config,period_ids=(result.diagnostics.get('gameplay',{}).get('best') or {}).get('ids',()))
        if v21_mode:
            # Explicitly expose BOTH denominators; the 500 broad positions
            # must not be mistaken for 500 depth18-confirmed games.
            result.coverage.update(fast_scanned=len(analyzed),
                                   broad_fast_scanned=len(analyzed),
                                   deep_scope_games=len(scoring_games),
                                   deep_scope_used=result.coverage.get('used',0))
            result.games=list(analyzed)  # owner evidence retains both tiers
        result.coverage.update(primary_collected=len(primary),primary_fast_scanned=min(len(analyzed),len(primary)) if not primary_complete else len(primary),
                               history_probed=len(probes),history_fast_scanned=sum(g not in primary for g in analyzed),
                               history_probe_complete=probe_complete,deep_incomplete=deep_incomplete,
                               archive_partial=archive_partial,primary_complete=primary_complete,
                               rated_primary=sum(g.rated is True for g in primary),casual_primary=sum(g.rated is False for g in primary),
                               unknown_primary=sum(g.rated is None for g in primary))
        result.history={'collected':len(history),'historical_candidates':len(historical_targets),
                        'sessions':session_history(history,config),'engine_selected':len(analyzed),'context':context_metrics(history,profile),
                        'repertoire':__import__('fairplay_opening').repertoire(history),
                        'timing_baselines':[{'class':kind,'control':control,
                            'profile':timing_profile(group,config)}
                            for (kind,control),group in buckets(history).items()]}
        if v21_mode:
            from fairplay_v21 import public_history_stats
            public_stats=None
            try:
                public_stats=api.get(canonical,'/stats')
            except Exception:
                pass  # optional public metadata does not affect the review
            result.diagnostics['public_history_stats']=public_history_stats(
                history,public_stats=public_stats)
            result.diagnostics['v21_selection']={
                'broad_fast_games':len(primary),
                'recent_peer_deep_games':len(v21_plan.core),
                'historical_extra_deep_games':len(v21_plan.reserve),
                'deep_games_completed':sum(g.deep for g in v21_deep),
                'eligible_rated_metadata_games':len(history),
                'peer_max_lower_rating_gap':500,
                'selected_fast_pv3_positions':v21_candidate_positions,
                'early_mode_fast_pv':config.fast_multipv,
                'selected_fast_pv':3,'selected_deep_pv':config.deep_multipv,
                'no_accuracy_cherry_picking':True,
                'scope_note':('500 games fast-screened, up to 250 peer games full-depth.'
                              if distributed_mode else
                              '500 games fast-screened, up to 125 peer games full-depth.')
                              + ' No full-depth claim for other games.'}
            result.diagnostics['distributed_compute']={
                'enabled':distributed_mode,
                'jobs_requested':__import__('fairplay_distributed').WORKERS
                    if distributed_handle is not None else 0,
                'shards_completed':(distributed_handle.get('stats') or {}).get('completed',0)
                    if distributed_handle is not None else 0,
                'shards_failed':(distributed_handle.get('stats') or {}).get('failed',0)
                    if distributed_handle is not None else 0,
                'shards_stalled':(distributed_handle.get('stats') or {}).get('stalled',0)
                    if distributed_handle is not None else 0,
                'compute_status':(distributed_handle.get('stats') or {}).get('reason','not_dispatched')
                    if distributed_handle is not None else 'not_dispatched',
                'games_verified_remotely':len(distributed_completed),
                'games_recomputed_locally':sum(g.identity not in distributed_completed for g in v21_deep)
                    if distributed_mode else 0,
                'distributed_time_seconds':round(time.monotonic()-distributed_handle['started'],1)
                    if distributed_handle is not None else None,
                'one_authoritative_report':True,
                'no_independent_worker_scoring':True}
        progress('Building report…')
        result.elapsed=time.monotonic()-started
        try:
            import resource
            memory_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
        except (ImportError,AttributeError):memory_mb=None
        if use_shared and shared_pool is not None:
            shared_profile_after=engine_profile(shared_pool.scanners)
            search_profile={key:shared_profile_after.get(key,0)-shared_profile_before.get(key,0)
                            for key in shared_profile_after}
        else:
            search_profile=engine_profile(scanners) if scanners else {}
        # No account identifiers, FENs, URLs or case labels in run metadata.
        from fairplay_maia import MODEL_SHA256, MODEL_REVISION, MODEL_NAME
        commit=os.environ.get('GITHUB_SHA','').lower()
        revision=commit[:12] if len(commit)==40 and all(c in '0123456789abcdef' for c in commit) else None
        result.diagnostics['run_contract']={
            'version':VERSION,'code_revision':revision,'engine':engine_name,
            'fast':{'mode':'nodes','budget':config.fast_nodes,'multipv':config.fast_multipv},
            'deep':{'mode':'depth' if full_depth_mode else 'nodes',
                    'budget':18 if full_depth_mode else config.deep_nodes,'multipv':config.deep_multipv,
                    'bullet_budget':config.bullet_deep_depth if full_depth_mode else config.deep_nodes},
            'maia_model':MODEL_NAME,'maia_revision':MODEL_REVISION[:12],
            'maia_checkpoint_sha256_prefix':MODEL_SHA256[:12],
            'parsed_primary_games':len(primary),
            'deep_completed_games':sum(g.deep for g in analyzed),
            'required_primary_depth':(18 if full_depth_mode and not any(g.time_class=='bullet' for g in primary)
                                      else None),
            'required_primary_depth_by_class':({'bullet':config.bullet_deep_depth,'blitz':18,'rapid':18}
                                               if full_depth_mode and not v21_mode else None),
            'selected_required_depth_by_class':({'bullet':config.bullet_deep_depth,'blitz':18,'rapid':18}
                                                if v21_mode else None),
            'required_full_coverage':bool(full_depth_mode and not v21_mode),
            'required_selected_full_depth':bool(full_depth_mode and v21_mode),
            'human_policy_sampling':('up to 1600 stratified positions across 200+50 deep peer games'
                                     if distributed_mode else
                                     'up to 800 stratified positions across 100+25 deep peer games')
                                     if v21_mode else 'stratified bounded positions (not all moves)',
            'depth18_completed_positions':sum(
                d.metrics.get('search_depth',0)>=18 for g in analyzed if g.deep and g.time_class!='bullet'
                for d in g.decisions) if full_depth_mode else None,
            'depth18_total_positions':sum(len(g.decisions) for g in
                    (v21_deep if v21_mode else analyzed) if g.time_class!='bullet')
                if full_depth_mode else None,
            'bullet_depth12_completed_positions':sum(
                d.metrics.get('search_depth',0)>=config.bullet_deep_depth
                for g in analyzed if g.deep and g.time_class=='bullet' for d in g.decisions)
                if full_depth_mode else None,
            'bullet_depth12_total_positions':sum(len(g.decisions) for g in
                    (v21_deep if v21_mode else analyzed) if g.time_class=='bullet')
                if full_depth_mode else None,
            'skipped_by_fixed_reason':dict(sorted(skipped.items())),
            'archive_failures':skipped.get('unavailable_archive',0),
            'runtime_seconds':round(result.elapsed,2),
        }
        result.diagnostics['human_reference']=neural_reference
        result.diagnostics['runtime']={'collection_seconds':collected_at-started,
            'fast_seconds':fast_finished-collected_at,'deep_seconds':deep_finished-deep_started,
            'profile_seconds':time.monotonic()-deep_finished,'elapsed_seconds':result.elapsed,
            'peak_process_memory_mb':memory_mb,'full_fast_games':len(analyzed),'deep_games':sum(g.deep for g in analyzed),
            'human_reference_seconds':neural_reference.get('seconds',0),
            'fast_position_tasks':fast_position_tasks,'deep_position_tasks':deep_position_tasks,
            'engine_workers':requested_workers,'shared_engine_pool':bool(use_shared),
            'stockfish_worker_restarts':(getattr(shared_pool,'restarts',0)-pool_restarts_before
                                         if use_shared and shared_pool is not None else None),
            'exact_position_cache':({key:value-position_cache_before.get(key,0)
                for key,value in shared_pool.position_cache.snapshot().items() if key!='entries'}
                if use_shared and shared_pool is not None and getattr(shared_pool,'position_cache',None) is not None else None),
            'full_depth18_mode':full_depth_mode,
            'v21_scoped_review':v21_mode,
            'selected_fast_pv3_positions':v21_candidate_positions,
            'full_depth18_games_completed':sum(g.deep for g in analyzed if g.time_class!='bullet') if full_depth_mode else None,
            'bullet_depth12_games_completed':sum(g.deep for g in analyzed if g.time_class=='bullet') if full_depth_mode else None,
            'effective_cpu_capacity':available_engine_cpus(),
            'effective_memory_limit_mb':available_engine_memory_mb(),
            'engine_pool_max':_ENGINE_POOL_MAX,
            'engine_searches':search_profile}
        return result
    except TimeoutError as error:
        raise ReviewError('A Stockfish search timed out despite worker recovery. No review result was issued; please retry later.') from error
    except (chess.engine.EngineError,OSError) as error:
        raise ReviewError('Stockfish failed despite worker recovery. No review result was issued; please retry later.') from error
    except DeadlineReached:
        raise ReviewError('The review reached its runtime limit before meaningful engine data was available. Try again later.')
    finally:
        api.close()
        if engine_executor is not None:engine_executor.shutdown(wait=True,cancel_futures=True)
        for item in scanners:item.close()


def review_interest(game):
    m = game.metrics
    return ((m.get('critical_top1') or 0)*min(m.get('critical',0),15)*4
            + (m.get('top1') or 0)*10 - min(m.get('robust_cpl') or 0,100)/10
            + 5*int(m.get('timing',{}).get('elevated',False)))
