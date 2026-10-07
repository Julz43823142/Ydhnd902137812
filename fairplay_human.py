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
        excess = max(0.0, observed-expected)
        uniqueness = 1/max(1, m.get('plausible_good_moves', 1))**.25
        information = m.get('difficulty', 0)*excess*uniqueness if opportunity else 0.0
        hit = opportunity and observed>=.85 and excess>=config.human_quality_excess
        quiet = not (d.capture or d.check or d.gives_check)
        m.update(expected_human_quality=expected, observed_move_quality=observed,
                 quality_excess=excess, human_anomaly_information=information,
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
                  'near_best':sum(m.get('near_best', False) for m in v)/len(v) if v else None}
             for name, v in buckets.items()}
    inversion = (len(buckets['easy'])>=6 and len(buckets['hard'])>=6 and
                 curve['hard']['near_best']-curve['easy']['near_best']>=.25)
    stable = [m for m in capped if m.get('search_stability', {}).get('stable')]
    result = {
        'model':FALLBACK.name if failures else model.name, 'model_failures':failures, 'rank':ranks,
        'opportunities':len(capped), 'raw_opportunities':len(opportunities),
        'hits':sum(m.get('high_information', False) for m in capped),
        'quiet_hits':sum(m.get('informative_quiet_hit', False) for m in capped),
        'stable_opportunities':len(stable), 'stable_hits':sum(m.get('high_information', False) for m in stable),
        'hard_opportunities':len(capped), 'hard_hits':sum(m.get('cpl', 1000)<=15 for m in capped),
        'hard_stable':len(stable),
        'information':statistics.mean(m['human_information'] for m in capped) if capped else 0,
        'quality_excess':statistics.mean(m['quality_excess'] for m in capped) if capped else 0,
        'observed_quality':statistics.mean(m['observed_move_quality'] for m in capped) if capped else None,
        'quality_reference':statistics.mean(m['expected_human_quality'] for m in capped) if capped else None,
        'difficulty_curve':curve, 'difficulty_inversion':bool(inversion),
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
        'information':average('information') or 0, 'quality_excess':average('quality_excess') or 0,
        'observed_quality':average('observed_quality'), 'quality_reference':average('quality_reference'),
        'quiet_hits':sum(m.get('quiet_hits', 0) for g, m in rows),
        'opportunity_games':len(eligible),
        'stable_opportunities':sum(m.get('stable_opportunities', 0) for g, m in rows),
        'stable_hits':sum(m.get('stable_hits', 0) for g, m in rows),
        'hard_opportunities':hard_n, 'hard_hits':hard_hits,
        'hard_lower':lower_bound(hard_hits/hard_n if hard_n else None, hard_n, config.rate_lower_bound_z),
        'hard_contributors':sum(m.get('hard_hits', 0)>=1 for g, m in rows),
        'hard_stable':sum(m.get('hard_stable', 0) for g, m in rows),
        'rating_coverage':len(rated)/len(games) if games else 0,
        'rating_reference':statistics.median(g.rating for g in rated) if rated else None,
        'inversion_games':sum(m.get('difficulty_inversion', False) for g, m in rows)}


def absolute_blockers(s, config=CONFIG):
    # Wilson bounds describe the sampled anomaly-hit fraction, NOT an expected
    # human-quality probability. Quality excess has its own continuous gate.
    required_contributors = max(config.human_min_contributors,
                                math.ceil(s['opportunity_games']*config.human_contributor_fraction))
    required_opportunities = max(config.human_min_opportunities, 2*s['opportunity_games'])
    tests = {
        'at least ten games':s['games']>=config.min_games,
        'known rating coverage':s['rating_coverage']>=.8,
        'distributed opportunity coverage':s['opportunities']>=required_opportunities,
        'distributed contributor games':s['contributors']>=required_contributors,
        'rating-adjusted information':s['information']>=config.human_absolute_information_floor,
        'rating-adjusted quality excess':s.get('quality_excess', 0)>=config.human_period_excess,
        'anomaly hit lower bound':s['hit_lower']>=config.human_hit_lower}
    return [label for label, passed in tests.items() if not passed]


def absolute_qualified(summary, config=CONFIG):
    return not absolute_blockers(summary, config)


def evidence_funnel(games, config=CONFIG):
    counts = {key:0 for key in ['parsed', 'book', 'forced', 'trivial', 'easy_conversion', 'search_unstable',
                               'engine_useful', 'competitive', 'high_difficulty', 'critical', 'unique', 'high_information']}
    for game in games:
        for d in game.decisions:
            m = d.metrics
            counts['parsed'] += 1
            counts['book'] += d.phase=='opening'
            counts['forced'] += d.forced
            counts['trivial'] += bool(d.trivial_kind or m.get('simple_threat_response'))
            counts['easy_conversion'] += bool(m.get('easy_conversion') or m.get('automatic_material_gain'))
            counts['search_unstable'] += bool(m.get('search_inconsistent') or
                (m.get('search_stability', {}).get('compared') and not m['search_stability']['stable']))
            for key, flag in [('engine_useful', m.get('useful')), ('competitive', m.get('competitive')),
                              ('high_difficulty', m.get('difficulty', 0)>=config.human_difficulty_floor),
                              ('critical', m.get('critical')), ('unique', m.get('unique')),
                              ('high_information', m.get('high_information'))]:
                counts[key] += bool(flag)
    return counts
