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
from fairplay_timing import cadence, trivial_delay_metrics, trivial_delay_summary
from fairplay_baseline import personal_timing
from fairplay_data import (DeadlineReached, GameSample, PubAPI, ReviewError,
                           ScanDeadline, check_deadline, collect_games, finite_number, username)


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
    # Check responses/recaptures/openings were excluded before analysis. Decisive
    # won/lost positions are weak evidence: best-move CPL there is misleading.
    useful = decision.useful and abs(values[0]) <= 600
    critical = (useful and decision.legal >= config.critical_legal and gap is not None
                and spread is not None and gap >= config.critical_gap
                and spread >= config.critical_spread and not decision.capture
                and not decision.gives_check)
    return {'before_cp': values[0], 'actual_cp': actual, 'best': candidates[0],
            'top1': decision.move == candidates[0], 'top3': decision.move in candidates[:3],
            'cpl': min(1000, max(0, values[0]-actual)), 'gap': gap, 'spread': spread,
            'useful': useful, 'critical': bool(critical),
            'unique': bool(critical and gap >= config.unique_gap)}


def summarize(game: GameSample, config=CONFIG):
    moves = [d for d in game.decisions if d.metrics.get('useful')]
    critical = [d for d in moves if d.metrics['critical']]
    unique = [d for d in critical if d.metrics['unique']]
    losses = [d.metrics['cpl'] for d in moves]
    consecutive = run = 0
    for d in moves:
        # Count consecutive critical opportunities; a noncritical move is not
        # itself evidence but a missed critical move breaks the hit sequence.
        if d.metrics['critical']:
            run = run+1 if d.metrics['top1'] else 0
            consecutive = max(consecutive, run)
    game.metrics = {'decisions': len(moves), 'median_cpl': median(losses),
                    'robust_cpl': stats.mean(sorted(losses)[:max(1, math.ceil(len(losses)*.9))]) if losses else None,
                    'p90_cpl': percentile(losses), 'top1': ratio(sum(d.metrics['top1'] for d in moves), len(moves)),
                    'top3': ratio(sum(d.metrics['top3'] for d in moves), len(moves)),
                    'critical': len(critical), 'critical_top1': ratio(sum(d.metrics['top1'] for d in critical), len(critical)),
                    'critical_top3': ratio(sum(d.metrics['top3'] for d in critical), len(critical)),
                    'critical_cpl': median([d.metrics['cpl'] for d in critical]),
                    'unique': len(unique), 'unique_hits': sum(d.metrics['top1'] for d in unique),
                    'critical_sequence': consecutive,
                    'mistakes': sum(v >= config.mistake_cp for v in losses),
                    'blunders': sum(v >= config.blunder_cp for v in losses),
                    'critical_mistakes': sum(d.metrics['cpl'] >= config.mistake_cp for d in critical)}
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
    result = {'count':len(values), **cadence(values,config)}
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
    result['regime_shift'] = bool(len(last)>=config.min_timing_moves and later.get('elevated')
                                 and not prior.get('elevated') and prior.get('cv',0)>.5
                                 and median([d.metrics['cpl'] for d in first],0)>40
                                 and median([d.metrics['cpl'] for d in last],100)<15)
    # Bullet rounding, premoves and lag make these measures much less reliable.
    result['reliability'] = 'LOW' if game.time_class=='bullet' else 'MEDIUM' if len(values)>=15 else 'LOW'
    result['trivial_delay'] = trivial_delay_metrics(game.decisions,config)
    return result


def performance_metrics(games):
    useful = [g for g in games if g.metrics.get('decisions',0)>=CONFIG.min_game_decisions]
    by_class = {}
    shifts = []
    contrasts = []
    for time_class in ('rapid','blitz','bullet'):
        group = [g for g in useful if g.time_class == time_class]
        best = None
        # Compare sustained 10-game windows at each boundary. Effect must be
        # >= 3 pooled MADs and 35 CP, with top-move + blunder/critical improvement.
        for cut in range(10,len(group)-9):
            before, after = group[cut-10:cut], group[cut:cut+10]
            old, new = [g.metrics['robust_cpl'] for g in before], [g.metrics['robust_cpl'] for g in after]
            old_m, new_m = median(old), median(new)
            mad = max(10, median([abs(v-old_m) for v in old])+median([abs(v-new_m) for v in new]))
            top_gain = median([g.metrics['top1'] for g in after])-median([g.metrics['top1'] for g in before])
            old_b = median([g.metrics['blunders']/g.metrics['decisions'] for g in before])
            new_b = median([g.metrics['blunders']/g.metrics['decisions'] for g in after])
            old_c = [g.metrics['critical_top1'] for g in before if g.metrics['critical']>=3]
            new_c = [g.metrics['critical_top1'] for g in after if g.metrics['critical']>=3]
            critical_gain = median(new_c,0)-median(old_c,0) if len(old_c)>=5 and len(new_c)>=5 else None
            sustained = sum(g.metrics['robust_cpl']<=20 and g.metrics['top1']>=.8 for g in after)>=8
            elevated = old_m-new_m>=max(35,3*mad) and new_m<=20 and top_gain>=.20 and sustained and (old_b-new_b>=.03 or (critical_gain is not None and critical_gain>=.25))
            candidate = {'class':time_class,'before_cpl':old_m,'after_cpl':new_m,
                         'top1_gain':top_gain,'critical_gain':critical_gain,'blunder_change':new_b-old_b,
                         'effect_mad':(old_m-new_m)/mad,'at':after[0].ended,'elevated':bool(elevated)}
            if best is None or candidate['effect_mad']>best['effect_mad']:best = candidate
        if best and best['elevated']:shifts.append(best)
        wins, losses = [g for g in group if g.result=='Win'], [g for g in group if g.result=='Loss']
        draws = [g for g in group if g.result=='Draw']
        contrast = None
        if len(wins)>=8 and len(losses)>=8:
            win_cpl = median([g.metrics['robust_cpl'] for g in wins])
            loss_cpl = median([g.metrics['robust_cpl'] for g in losses])
            contrast = {'win_cpl':win_cpl,'loss_cpl':loss_cpl,'elevated':win_cpl<=15 and loss_cpl>=70,
                        'clean_wins':sum(g.metrics['robust_cpl']<=20 for g in wins)}
            if contrast['elevated'] and contrast['clean_wins']>=8:contrasts.append(time_class)
        by_class[time_class] = {'games':len(group),'wins':len(wins),'losses':len(losses),'draws':len(draws),
                               'win_cpl':median([g.metrics['robust_cpl'] for g in wins]),
                               'loss_cpl':median([g.metrics['robust_cpl'] for g in losses]),
                               'draw_cpl':median([g.metrics['robust_cpl'] for g in draws]),
                               'shift':best,'win_loss_contrast':contrast,
                               'rolling_cpl':[median([g.metrics['robust_cpl'] for g in group[i:i+10]]) for i in range(max(0,len(group)-9))]}
    return {'classes':by_class,'shifts':shifts,'contrasts':contrasts}


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
        rated = [g for g in group if g.rating is not None and g.opponent_rating is not None]
        expected = [1/(1+10**((g.opponent_rating-g.rating)/400)) for g in rated]
        actual = [g.score for g in rated]
        ratings = [g.rating for g in group if g.rating is not None]
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
            'critical_mistakes':sum(d.metrics['cpl']>=CONFIG.mistake_cp for d in critical)}


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


def family_label(value):
    return 'Very High' if value>=.85 else 'High' if value>=.70 else 'Elevated' if value>=.5 else 'Slightly Elevated' if value>=.25 else 'Normal / limited evidence'


def priority_model(scores, *, games, decisions, critical, confidence, deep_confirmed, partial, config=CONFIG):
    if games<config.min_games or decisions<150:return 'INSUFFICIENT DATA'
    total = sum(value*weight for value,weight in zip(scores,config.weights))
    primary = scores[0]>=.65 and scores[1]>=.65 and critical>=config.min_critical
    supporting = max(scores[2:])>=.5
    if (total>=.78 and scores[0]>=.8 and scores[1]>=.85 and supporting and deep_confirmed
            and confidence=='HIGH' and not partial and games>=config.very_high_games
            and decisions>=config.high_decisions and critical>=config.very_high_critical):return 'VERY HIGH'
    if total>=.58 and primary and supporting and deep_confirmed and confidence!='LOW':return 'HIGH'
    return 'MODERATE' if total>=.30 or max(scores[:2])>=.65 else 'LOW'


def score_review(target, games, selected, skipped, partial, engine_name, profile, elapsed, config=CONFIG):
    useful = [g for g in games if g.metrics.get('decisions',0)>=config.min_game_decisions]
    totals = aggregate(useful)
    classes = {kind:aggregate([g for g in useful if g.time_class==kind]) for kind in ('rapid','blitz','bullet')}
    performance, context = performance_metrics(useful), context_metrics(useful,profile)
    deep = [g for g in useful if g.deep]
    coverage = aggregate(deep)
    deep_confirmed = (coverage['decisions']>=config.min_deep_decisions and coverage['critical']>=config.min_deep_critical
                      and (coverage['top1'] or 0)>=.85 and (coverage['critical_top1'] or 0)>=.85
                      and (coverage['median_cpl'] if coverage['median_cpl'] is not None else 100)<=15)
    clocks = sum(g.metrics['timing']['count']>=config.min_timing_moves for g in useful)
    confidence = ('HIGH' if len(useful)>=30 and totals['decisions']>=500 and totals['critical']>=30
                  and coverage['decisions']>=60 and clocks>=10 and not partial else
                  'MEDIUM' if len(useful)>=config.min_games and totals['decisions']>=150 else 'LOW')
    # Per-class weighting prevents Bullet from promoting mixed-control evidence.
    engine_score = critical_score = timing_score = 0.0
    recurrent = []
    trivial_timing = {}
    personal = personal_timing(useful,config)
    for row in personal:
        level = {'Moderate':.5,'Strong':.75,'Very Strong':.85}.get(row['state'],0)
        timing_score = max(timing_score,level*(.35 if row['time_class']=='bullet' else 1))
    for kind in classes:
        group = [g for g in useful if g.time_class==kind]
        a = classes[kind]
        weight = .35 if kind=='bullet' else 1.0
        delay = trivial_timing[kind] = trivial_delay_summary(group,config)
        if delay['recurrent']:
            timing_score = max(timing_score,weight*(.75 if delay['same_cadence_games']>=10 else .5))
        if a['games']>=10 and a['decisions']>=200:
            exceptional = (a['top1'] or 0)>=.90 and (a['top3'] or 0)>=.98 and (a['p90_cpl'] or 0)<=30
            elevated = (a['top1'] or 0)>=.80 and (a['median_cpl'] or 0)<=15
            engine_score = max(engine_score,weight*(.85 if exceptional else .55 if elevated else .1))
        if a['games']>=10 and a['critical']>=30:
            hits = a['critical_top1'] or 0
            distinct = sum(g.metrics['critical']>=3 and (g.metrics['critical_top1'] or 0)>=.8 for g in group)
            critical_score = max(critical_score,weight*(.95 if hits>=.90 and distinct>=8 else .70 if hits>=.80 and distinct>=5 else .4 if hits>=.65 else .1))
        regular = [g for g in group if (g.metrics['timing'].get('elevated') or g.metrics['timing'].get('regime_shift')
                                       or g.metrics['timing'].get('critical_cadence',{}).get('elevated'))]
        if len(regular)>=5:
            recurrent.extend(regular)
            timing_score = max(timing_score, weight*(.75 if len(regular)>=10 else .5))
    perf_score = max((.85 if s['class']!='bullet' else .3 for s in performance['shifts']), default=0)
    if performance['contrasts']:perf_score = max(perf_score,.55 if any(k!='bullet' for k in performance['contrasts']) else .2)
    ctx_score = .1 if context['age_days'] is not None and context['age_days']<30 else 0
    for kind, row in context['classes'].items():
        weight = .35 if kind=='bullet' else 1
        if row['excess_z'] is not None and row['excess_z']>=3:ctx_score = max(ctx_score,.55*weight)
        if row['rating_gain'] is not None and row['rating_gain']>=300:ctx_score = max(ctx_score,.35*weight)
    # Strong/titled and potentially underrated players are innocent alternatives.
    if context['title']:engine_score *= .65;critical_score *= .85;ctx_score *= .5
    scores = (engine_score,critical_score,timing_score,perf_score,ctx_score)
    names = ('Engine Precision','Critical Position Precision','Move-Time Pattern','Performance Shift','Account / Results')
    reasons = []
    if engine_score>=.5:reasons.append('Sustained high engine agreement on non-opening, non-forced decisions.')
    if critical_score>=.65:reasons.append('Strong precision across difficult, quiet unique-choice positions in several games.')
    if timing_score>=.5:
        if any(row['state'] in ('Moderate','Strong','Very Strong') and row['time_class']!='bullet' for row in personal):
            reasons.append('The player’s timing style changed alongside sustained engine-quality improvement within the same base time and increment; habits, lag and legitimate improvement remain alternatives.')
        delayed = next((row for kind,row in trivial_timing.items() if kind!='bullet' and row['recurrent']),None)
        if delayed:
            reasons.append(f'Trivial and critical decisions repeatedly arrive in the same ~{delayed["common_band_seconds"]}-second cadence across {delayed["same_cadence_games"]} games; input delay, slow play or lag remain alternatives.')
        elif recurrent:reasons.append('A narrow move-time cadence recurs in several games; lag or clock rounding remain alternatives.')
    if perf_score>=.5:reasons.append('Sustained same-time-control performance change or a repeated win/loss quality contrast.')
    if ctx_score>=.5:reasons.append('Recorded results substantially exceed rating-based expectations; underrating is an alternative.')
    if not reasons:reasons.append('No well-supported elevated signal combination was found in this sample; this does not establish fair play.')
    return ReviewResult(target,useful,selected,skipped,partial,engine_name,totals,classes,performance,context,
                        dict(zip(names,(family_label(s) for s in scores))),
                        priority_model(scores,games=len(useful),decisions=totals['decisions'],critical=totals['critical'],
                                       confidence=confidence,deep_confirmed=deep_confirmed,partial=partial,config=config),
                        confidence,reasons,deep_confirmed,coverage,elapsed,
                        timing={'trivial_delay':trivial_timing,'personal':personal})


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
            board = chess.Board(decision.fen)
            # Clear hash between positions so order/cache warmth does not change
            # fixed-node candidate rankings. Threads=1 avoids search races.
            if 'Clear Hash' in self.engine.options:self.engine.configure({'Clear Hash':None})
            lines = self.engine.analyse(board,chess.engine.Limit(nodes=nodes),multipv=3)
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
        summarize(game,self.config)
        if nodes == self.config.fast_nodes:
            game.fast_metrics = {k:v for k,v in game.metrics.items() if k!='timing'}


# Bounded process-local successful game cache, not an account/case ledger.
_game_cache = OrderedDict()


def review(target: str, progress: Callable, config=CONFIG, *, api_factory=PubAPI, engine_factory=None, cancel=None):
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
        games, skipped, partial = collect_games(api,canonical,progress,config)
        if not games:raise ReviewError('No eligible standard live games with enough meaningful moves were found.')
        check_deadline(deadline)
        try:scanner = EngineScanner(deadline,config,engine_factory)
        except Exception as error:raise ReviewError('Stockfish is unavailable. Engine screening could not be performed; no review priority was assigned.') from error
        analyzed = []
        for index, game in enumerate(games):
            progress(f'Fast engine scan: {index} / {len(games)}')
            try:
                check_deadline(deadline)
                key = (game.identity,game.color,scanner.name,VERSION,config)
                cached = _game_cache.get(key)
                if cached and time.monotonic()-cached[0]<3600:
                    import copy
                    game.decisions = copy.deepcopy(cached[1]);game.deep = cached[2]
                    game.fast_metrics = copy.deepcopy(cached[3]);summarize(game,config)
                else:scanner.analyse(game,config.fast_nodes)
                analyzed.append(game)
            except DeadlineReached:
                partial = True;break
            except (chess.engine.EngineError, TimeoutError, ReviewError):
                raise ReviewError('Stockfish stopped responding. The scan was stopped safely; please try again later.')
        candidates = sorted([g for g in analyzed if not g.deep],key=review_interest,reverse=True)[:config.deep_games]
        for index, game in enumerate(candidates):
            progress(f'Reviewing unusual games: {index} / {len(candidates)}')
            try:
                import copy
                confirmed = copy.deepcopy(game)
                scanner.analyse(confirmed,config.deep_nodes)
                game.decisions, game.metrics, game.deep = confirmed.decisions, confirmed.metrics, True
            except DeadlineReached:
                partial = True;break
            except (chess.engine.EngineError, TimeoutError, ReviewError):
                raise ReviewError('Stockfish stopped responding during deep review. Please try again later.')
        import copy
        for game in analyzed:
            if game.deep:
                key = (game.identity,game.color,scanner.name,VERSION,config)
                _game_cache[key] = (time.monotonic(),copy.deepcopy(game.decisions),True,copy.deepcopy(game.fast_metrics))
                _game_cache.move_to_end(key)
        while len(_game_cache)>200:_game_cache.popitem(last=False)
        progress('Building report…')
        return score_review(canonical,analyzed,len(games),skipped,partial,scanner.name,profile,time.monotonic()-started,config)
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
