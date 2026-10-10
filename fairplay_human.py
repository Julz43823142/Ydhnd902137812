"""Rating-conditioned quality excess, not a learned probability distribution.

All derived rank/loss/difficulty measurements are ONE gameplay family. Misses
remain in uniformly capped game denominators. Heuristic references require
independently labelled holdout calibration before any accuracy claim.
"""
import math
import statistics
from typing import Protocol
from fairplay_config import CONFIG
from fairplay_calibration import lower_bound
from fairplay_difficulty import annotate, clamp, stability, loss_summary


class HumanReferenceModel(Protocol):
    name: str
    def expected_quality(self, decision, rating: int | None, time_class: str) -> float: ...


class HeuristicHumanModel:
    name = 'rating-conditioned heuristic (not probability)'

    def expected_quality(self, decision, rating, time_class='blitz'):
        # A bounded quality index: neither a move probability nor a hit rate.
        # Missing ratings use the conservative elite reference and cannot qualify
        # absolute routes. Strength rises continuously; difficulty lowers quality.
        strength = clamp(((rating if rating is not None else 2800)-400)/2400)
        difficulty = decision.metrics.get('difficulty', 0)
        time_allowance = .015 if time_class == 'rapid' else .03 if time_class == 'bullet' else 0
        return min(.97, .32+.62*strength+.16*(1-difficulty)+time_allowance)

    def expectedness(self, decision, rating):
        # Compatibility for local adapters; production uses expected_quality.
        return self.expected_quality(decision, rating)


HumanMoveModel = HumanReferenceModel
FALLBACK = HeuristicHumanModel()


def quality_components(observed, expected, rating, config=CONFIG):
    """Raw excess plus remaining-headroom-normalized anomaly strength.

    This is deliberately not a probability. Fixed raw-excess gates become
    mathematically unreachable as expected quality approaches 1.0; normalizing
    by the remaining headroom fixes that geometry. A raw-excess floor and a
    small continuous elite adjustment keep ordinary strong play from becoming
    exceptional merely because the player has a high rating.
    """
    excess=max(0.0, observed-expected)
    headroom=max(config.human_residual_headroom_floor, 1-expected)
    residual=min(1.0, excess/headroom)
    strength=max(excess, config.human_residual_weight*residual)
    rating_strength=clamp(((rating if rating is not None else 2800)-400)/2400)
    hit_threshold=config.human_quality_excess+max(
        0.0, rating_strength-config.human_elite_threshold_start
    )*config.human_elite_threshold_scale
    raw_threshold=config.human_min_raw_excess_base+config.human_min_raw_excess_rating*rating_strength
    return excess,residual,strength,hit_threshold,raw_threshold


def accuracy_index(scaled_loss):
    """Descriptive move-accuracy transform from win-percentage loss."""
    if scaled_loss is None:return None
    loss_percent=max(0.0,min(100.0,100*float(scaled_loss)))
    return max(0.0,min(100.0,103.1668*math.exp(-.04354*loss_percent)-3.1669))


def annotate_game(game, config=CONFIG, model=None):
    model = model or FALLBACK
    failures = 0
    for d in game.decisions:
        annotate(d, config)
        m = d.metrics
        if not m.get('useful'):
            continue
        # Clear derived flags on every pass: deweighted decisions must never
        # retain an opportunity from an earlier fast/deep annotation.
        opportunity = bool(m.get('competitive') and m.get('difficulty', 0)>=config.human_difficulty_floor
                           and not m.get('search_inconsistent') and not m.get('easy_conversion')
                           and not m.get('post_opponent_error') and not d.forced and not d.trivial_kind
                           and d.phase != 'opening')
        fallback = False
        try:
            if hasattr(model, 'expected_quality'):
                expected = float(model.expected_quality(d, game.rating, game.time_class))
            else:
                expected = float(model.expectedness(d, game.rating))
            if not math.isfinite(expected) or not 0<=expected<=1:
                raise ValueError('Invalid bounded quality reference')
        except Exception:
            failures += 1
            fallback = True
            expected = FALLBACK.expected_quality(d, game.rating, game.time_class)
        # Equal-budget actual-move evaluation preserves #2/#3 near-equivalence.
        # Rank is diagnostic, not a penalty when objective quality is equivalent.
        observed = min(clamp(1-m.get('cpl', 1000)/150),
                       clamp(1-m.get('scaled_loss', m.get('cpl', 1000)/1000)/.20))
        excess,residual,anomaly,hit_threshold,raw_threshold=quality_components(
            observed,expected,game.rating,config)
        uniqueness = 1/max(1, m.get('plausible_good_moves', 1))**.25
        information = m.get('difficulty', 0)*anomaly*uniqueness if opportunity else 0.0
        hit = (opportunity and observed>=.85 and excess>=raw_threshold
               and anomaly>=hit_threshold)
        quiet = not (d.capture or d.check or d.gives_check)
        m.update(expected_human_quality=expected, observed_move_quality=observed,
                 quality_excess=excess, quality_residual=residual,
                 anomaly_strength=anomaly, anomaly_hit_threshold=hit_threshold,
                 raw_excess_threshold=raw_threshold,
                 move_accuracy=accuracy_index(m.get('scaled_loss')),
                 human_anomaly_information=information,
                 human_expectedness=expected, human_information=information,
                 human_opportunity=opportunity, high_information=bool(hit),
                 informative_quiet_hit=bool(hit and quiet and m.get('plausible_good_moves', 1)<=3
                                            and m.get('played_boundary_cp', 0)>=config.critical_gap),
                 human_model=FALLBACK.name if fallback else model.name)
        stability(d, config)
    rows = [d.metrics for d in game.decisions if d.metrics.get('useful')]
    ranks = {str(k):sum(m.get('rank')==k for m in rows) for k in range(1, 6)}
    ranks['outside_top5'] = sum(m.get('rank') is None and m.get('candidate_count', 0)>=5 for m in rows)
    ranks['outside_observed'] = sum(m.get('rank') is None and m.get('candidate_count', 0)<5 for m in rows)
    opportunities = [m for m in rows if m.get('human_opportunity')]
    capped = opportunities
    if len(capped)>config.human_game_cap:
        capped = [capped[round(i*(len(capped)-1)/(config.human_game_cap-1))]
                  for i in range(config.human_game_cap)]
    buckets = {name:[m for m in rows if lo<=m.get('difficulty', 0)<hi]
               for name, lo, hi in [('easy', 0, .3), ('medium', .3, .55), ('hard', .55, 1.01)]}
    curve = {name:{'count':len(v),
                  'near_best':sum(m.get('near_best', False) for m in v)/len(v) if v else None,
                  'observed_quality':statistics.mean(m.get('observed_move_quality',0) for m in v) if v else None,
                  'anomaly_strength':statistics.mean(m.get('anomaly_strength',0) for m in v) if v else None}
             for name, v in buckets.items()}
    inversion = (len(buckets['easy'])>=6 and len(buckets['hard'])>=6 and
                 curve['hard']['near_best']-curve['easy']['near_best']>=.25)
    quality_inversion = ((curve['hard']['observed_quality'] or 0)-(curve['easy']['observed_quality'] or 0)
                         if len(buckets['easy'])>=6 and len(buckets['hard'])>=6 else 0.0)
    inversion_strength=max(
        0.0,
        (curve['hard']['near_best'] or 0)-(curve['easy']['near_best'] or 0)
            if len(buckets['easy'])>=6 and len(buckets['hard'])>=6 else 0.0,
        quality_inversion)
    selective_signal=bool(inversion_strength>=.20 and
                          (curve['hard']['anomaly_strength'] or 0)>=config.human_period_excess)
    stable = [m for m in capped if m.get('search_stability', {}).get('stable')]
    # A reliability measure must not require the *player* to find the
    # engine's best move. Otherwise a played blunder counts as an unstable
    # Stockfish search, double-counting the existing anomaly-hit gate.
    # Deep difficulty/opportunity eligibility was established above; the
    # paired search only checks whether both budgets agree on the *played
    # move's objective loss*. Fast difficulty may legitimately differ.
    measured = [m for m in capped if m.get('search_stability', {}).get('compared') is True]
    reliable = [m for m in measured
                if m.get('search_stability', {}).get('objective_quality_preserved') is True]
    result = {
        'model':FALLBACK.name if failures else model.name, 'model_failures':failures, 'rank':ranks,
        'opportunities':len(capped), 'raw_opportunities':len(opportunities),
        'hits':sum(m.get('high_information', False) for m in capped),
        'quiet_hits':sum(m.get('informative_quiet_hit', False) for m in capped),
        # Preserve the historical stricter measure as a separate diagnostic.
        'stable_opportunities':len(stable), 'stable_hits':sum(m.get('high_information', False) for m in stable),
        'paired_evaluated_opportunities':len(measured),
        'quality_stable_opportunities':len(reliable),
        'quality_stable_hits':sum(m.get('high_information', False) for m in reliable),
        'hard_opportunities':len(capped), 'hard_hits':sum(m.get('cpl', 1000)<=15 for m in capped),
        'hard_stable':len(stable),
        'information':statistics.mean(m['human_information'] for m in capped) if capped else 0,
        'quality_excess':statistics.mean(m['quality_excess'] for m in capped) if capped else 0,
        'quality_residual':statistics.mean(m['quality_residual'] for m in capped) if capped else 0,
        'anomaly_strength':statistics.mean(m['anomaly_strength'] for m in capped) if capped else 0,
        'accuracy_index':statistics.mean([m['move_accuracy'] for m in capped if m.get('move_accuracy') is not None])
            if any(m.get('move_accuracy') is not None for m in capped) else None,
        'observed_quality':statistics.mean(m['observed_move_quality'] for m in capped) if capped else None,
        'quality_reference':statistics.mean(m['expected_human_quality'] for m in capped) if capped else None,
        'difficulty_curve':curve, 'difficulty_inversion':bool(inversion),
        'difficulty_inversion_strength':inversion_strength, 'selective_signal':selective_signal,
        'competitive_scaled_loss':loss_summary([m for m in rows if m.get('competitive')]),
        'scaled_loss':loss_summary(rows), 'critical_scaled_loss':loss_summary([m for m in rows if m.get('critical')])}
    game.metrics['human'] = result
    return result


def period_summary(games, config=CONFIG, *, fast=False):
    rows = [(g, (g.fast_metrics or g.metrics if fast else g.metrics).get('human', {})) for g in games]
    n = sum(m.get('opportunities', 0) for g, m in rows)
    hits = sum(m.get('hits', 0) for g, m in rows)
    contributors = [g for g, m in rows if m.get('hits', 0)>=2
                    and m.get('information', 0)>=config.human_absolute_information_floor]
    hit_games = [g for g, m in rows if m.get('hits', 0)>=1]
    single_hit_games = [g for g, m in rows if m.get('hits', 0)==1]
    hard_n = sum(m.get('hard_opportunities', 0) for g, m in rows)
    hard_hits = sum(m.get('hard_hits', 0) for g, m in rows)
    rated = [g for g in games if g.rating is not None]
    eligible = [m for g, m in rows if m.get('opportunities', 0)]
    def average(key):
        values = [m[key] for m in eligible if m.get(key) is not None]
        return statistics.mean(values) if values else None
    return {
        'games':len(games), 'opportunities':n, 'hits':hits,
        'hit_lower':lower_bound(hits/n if n else None, n, config.rate_lower_bound_z),
        'contributors':len(contributors), 'contributor_ids':[g.identity for g in contributors],
        'hit_games':len(hit_games), 'hit_game_ids':[g.identity for g in hit_games],
        'single_hit_games':len(single_hit_games),
        'information':average('information') or 0, 'quality_excess':average('quality_excess') or 0,
        'quality_residual':average('quality_residual') or 0, 'anomaly_strength':average('anomaly_strength') or 0,
        'accuracy_index':average('accuracy_index'),
        'observed_quality':average('observed_quality'), 'quality_reference':average('quality_reference'),
        'quiet_hits':sum(m.get('quiet_hits', 0) for g, m in rows),
        'opportunity_games':len(eligible),
        'stable_opportunities':sum(m.get('stable_opportunities', 0) for g, m in rows),
        'stable_hits':sum(m.get('stable_hits', 0) for g, m in rows),
        'paired_evaluated_opportunities':sum(m.get('paired_evaluated_opportunities', 0) for g, m in rows),
        'quality_stable_opportunities':sum(m.get('quality_stable_opportunities', 0) for g, m in rows),
        'quality_stable_hits':sum(m.get('quality_stable_hits', 0) for g, m in rows),
        'hard_opportunities':hard_n, 'hard_hits':hard_hits,
        'hard_lower':lower_bound(hard_hits/hard_n if hard_n else None, hard_n, config.rate_lower_bound_z),
        'hard_contributors':sum(m.get('hard_hits', 0)>=1 for g, m in rows),
        'hard_stable':sum(m.get('hard_stable', 0) for g, m in rows),
        'rating_coverage':len(rated)/len(games) if games else 0,
        'rating_reference':statistics.median(g.rating for g in rated) if rated else None,
        'inversion_games':sum(m.get('difficulty_inversion', False) for g, m in rows),
        'selective_games':sum(m.get('selective_signal', False) for g, m in rows),
        'inversion_strength':average('difficulty_inversion_strength') or 0}


def period_raw_excess_floor(s, config=CONFIG):
    """Minimum period-level raw separation required beside residual strength."""
    reference=s.get('quality_reference')
    ceiling=max(0.0,(reference if reference is not None else 1.0)-config.human_period_ceiling_reference)
    return config.human_period_min_raw_excess+ceiling*config.human_period_ceiling_raw_scale


def absolute_blockers(s, config=CONFIG):
    # Wilson bounds describe the sampled anomaly-hit fraction, NOT an expected
    # human-quality probability. Residual strength is paired with a raw-excess
    # floor so tiny differences near the expected-quality ceiling cannot alone
    # create an absolute HIGH route.
    required_contributors = max(config.human_min_contributors,
                                math.ceil(s['opportunity_games']*config.human_contributor_fraction))
    required_opportunities = max(config.human_min_opportunities, 2*s['opportunity_games'])
    tests = {
        'at least ten games':s['games']>=config.min_games,
        'known rating coverage':s['rating_coverage']>=.8,
        'distributed opportunity coverage':s['opportunities']>=required_opportunities,
        'distributed contributor games':s['contributors']>=required_contributors,
        'rating-adjusted information':s['information']>=config.human_absolute_information_floor,
        'raw quality excess beyond ceiling guard':s.get('quality_excess', 0)>=period_raw_excess_floor(s,config),
        'headroom-aware anomaly strength':s.get('anomaly_strength', 0)>=config.human_period_excess,
        'anomaly hit lower bound':s['hit_lower']>=config.human_hit_lower}
    return [label for label, passed in tests.items() if not passed]


def absolute_qualified(summary, config=CONFIG):
    return not absolute_blockers(summary, config)


def evidence_funnel(games, config=CONFIG):
    """Return overlapping category counts and true sequential survivor counts."""
    decisions=[d for game in games for d in game.decisions]
    counts = {key:0 for key in ['parsed', 'book', 'forced', 'trivial', 'easy_conversion', 'search_unstable',
                               'engine_useful', 'competitive', 'high_difficulty', 'critical', 'unique', 'high_information']}
    for d in decisions:
        m=d.metrics
        counts['parsed']+=1
        counts['book']+=d.phase=='opening'
        counts['forced']+=d.forced
        counts['trivial']+=bool(d.trivial_kind or m.get('simple_threat_response'))
        counts['easy_conversion']+=bool(m.get('easy_conversion') or m.get('automatic_material_gain'))
        counts['search_unstable']+=bool(m.get('search_inconsistent') or
            (m.get('search_stability',{}).get('compared') and not m.get('search_stability',{}).get('stable')))
        for key,flag in [('engine_useful',m.get('useful')),('competitive',m.get('competitive')),
                         ('high_difficulty',m.get('difficulty',0)>=config.human_difficulty_floor),
                         ('critical',m.get('critical')),('unique',m.get('unique')),
                         ('high_information',m.get('high_information'))]:
            counts[key]+=bool(flag)
    remaining=list(decisions);flow={'parsed':len(remaining)}
    stages=[
        ('off_book',lambda d:d.phase!='opening'),
        ('not_forced',lambda d:not d.forced),
        ('not_trivial',lambda d:not (d.trivial_kind or d.metrics.get('simple_threat_response'))),
        ('not_easy_conversion',lambda d:not (d.metrics.get('easy_conversion') or d.metrics.get('automatic_material_gain'))),
        ('engine_useful',lambda d:bool(d.metrics.get('useful'))),
        ('competitive',lambda d:bool(d.metrics.get('competitive'))),
        ('high_difficulty',lambda d:d.metrics.get('difficulty',0)>=config.human_difficulty_floor),
    ]
    for name,predicate in stages:
        remaining=[d for d in remaining if predicate(d)]
        flow[name]=len(remaining)
    # These attributes overlap. Never present them as additional serial
    # eligibility gates or suggest the remaining count is the scoring pool.
    counts['search_stability_unknown']=sum(
        not d.metrics.get('search_stability',{}).get('compared')
        and not d.metrics.get('search_inconsistent',False) for d in decisions)
    # Compatibility-only branch count; not a genuine serial requirement.
    flow['high_information']=sum(bool(d.metrics.get('high_information')) for d in remaining)
    counts['_flow']=flow
    return counts
