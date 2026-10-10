"""Opportunity-aware fair-play coverage and exploratory episodic triage.

A rate gate that demands multi-hit games from >=60% of opportunity games can
be *structurally impossible* when most low-rated games contain 1 difficult
position. Such a gate must be reported as unavailable, not fair-play evidence.

The complementary episodic route is LOW -> MODERATE human-review triage only.
It conditions on the total observed hard-move anomaly hits within one
time-control stratum and uses an exact combinatorial scan test for >=5 hits
in any single game, correcting for ALL pre-eligible strata. It is a
within-player exchangeability diagnostic, NOT a probability of cheating and
NOT calibrated against independently labeled fair play. It never grants HIGH.
"""
from __future__ import annotations

import math
from collections import defaultdict

SCHEMA = "sharkbot-opportunity-feasibility-v1"
EPISODIC_HITS = 5
EPISODIC_ALPHA = .01  # exploratory, family-wise across eligible strata


def _human(game, fast=False):
    data = (getattr(game, "fast_metrics", None) or getattr(game, "metrics", {}) or {}
            if fast else getattr(game, "metrics", {}) or {})
    return data.get("human") or {}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _measures(game, fast=False):
    m = _human(game, fast)
    n, k = m.get("opportunities", 0), m.get("hits", 0)
    if not _integer(n) or not _integer(k) or k > n:
        return None
    return n, k


def conditional_scan_probability(opportunities, hits, minimum_hits=EPISODIC_HITS):
    """Exact conditional null: at least one game with >= minimum_hits.

    Holds fixed every game's number of sampled opportunities and the stratum's
    total observed hits. Uses multivariate hypergeometric combinatorics, not
    random search or a guessed external rating-specific probability. A low
    value describes *concentration under exchangeability*, not misconduct.
    """
    if not opportunities or not all(_integer(n) for n in opportunities):
        return None
    total = sum(opportunities)
    if not _integer(hits) or hits > total or total < minimum_hits:
        return None
    constrained = [n for n in opportunities if n >= minimum_hits]
    if not constrained:
        return None
    unbounded = total - sum(constrained)
    ways = [math.comb(unbounded, k) if k <= unbounded else 0
            for k in range(hits + 1)]
    for n in constrained:
        next_ways = [0] * (hits + 1)
        for successes in range(min(n, minimum_hits - 1, hits) + 1):
            weight = math.comb(n, successes)
            for used in range(hits - successes + 1):
                if ways[used]:
                    next_ways[used + successes] += ways[used] * weight
        ways = next_ways
    overall = math.comb(total, hits)
    return max(0.0, min(1.0, (overall - ways[hits]) / overall))


def opportunity_feasibility(games, config):
    """No raw game identifiers or PGNs leave this read-only aggregate."""
    pool = [g for g in games if getattr(g, "deep", False)
            and getattr(g, "rated", None) is True
            and not getattr(g, "probe_only", False)]
    results = {}
    for kind in ("rapid", "blitz", "bullet"):
        members = [g for g in pool if getattr(g, "time_class", None) == kind]
        passes = {}
        for fast, label in ((True, "fast"), (False, "deep")):
            measures = [_measures(g, fast) for g in members]
            valid = [row for row in measures if row is not None]
            n = sum(a for a, b in valid)
            k = sum(b for a, b in valid)
            opp_games = sum(a > 0 for a, b in valid)
            possible_contributors = sum(a >= 2 for a, b in valid)
            needed_contributors = max(config.human_min_contributors,
                                      math.ceil(opp_games * config.human_contributor_fraction))
            needed_opportunities = max(config.human_min_opportunities, 2 * opp_games)
            passes[label] = {
                "deep_reviewed_games": len(members),
                "valid_measured_games": len(valid),
                "opportunities": n, "hits": k,
                "opportunity_games": opp_games,
                "max_two_hit_contributor_games": possible_contributors,
                "required_contributor_games": needed_contributors,
                "required_opportunities": needed_opportunities,
                "absolute_route_exposure_possible": (
                    n >= needed_opportunities and
                    possible_contributors >= needed_contributors),
                "acute_minimum_per_game": config.acute_min_game_opportunities,
                "games_capable_of_acute_minimum": sum(
                    a >= config.acute_min_game_opportunities for a, b in valid),
                "max_difficult_opportunities_in_one_game": max(
                    (a for a, b in valid), default=0),
            }
        results[kind] = passes
    return results


def episodic_audit(games, config):
    """Predeclared exposure test; never search outcome-selected windows."""
    groups = defaultdict(list)
    for game in games:
        if (getattr(game, "deep", False)
                and getattr(game, "rated", None) is True
                and not getattr(game, "probe_only", False)
                and getattr(game, "time_class", None) in ("rapid", "blitz")):
            groups[(game.time_class, str(getattr(game, "time_control", "")))].append(game)
    tested = []
    for (kind, control), members in sorted(groups.items()):
        # If any populated game has malformed counters, exclude the entire
        # stratum rather than biasedly dropping misses from the denominator.
        stats = [_measures(g) for g in members]
        if any(row is None for row in stats):
            continue
        opportunities = [n for n, _ in stats]
        total = sum(opportunities)
        hits = sum(k for _, k in stats)
        hit_games = sum(k > 0 for _, k in stats)
        if (len(members) < 15 or total < 30 or hits < 12
                or hit_games < 8 or max(opportunities, default=0) < EPISODIC_HITS):
            continue
        probability = conditional_scan_probability(opportunities, hits)
        if probability is None:
            continue
        # A real candidate must have completed, quality-preserving paired
        # search contracts, and >=2 fast hard hits, not merely five shallow
        # or post-hoc deep successes.
        confirmed = 0
        for game, (n, k) in zip(members, stats):
            if n < EPISODIC_HITS or k < EPISODIC_HITS:
                continue
            human = _human(game)
            fast = _measures(game, True)
            if (not fast or fast[1] < 2 or fast[0] < 2):
                continue
            if (human.get("paired_evaluated_opportunities", 0) < n
                    or human.get("quality_stable_hits", 0) < EPISODIC_HITS):
                continue
            confirmed += 1
        tested.append({
            "time_class": kind, "control": control,
            "games": len(members), "opportunities": total, "hits": hits,
            "hit_games": hit_games, "max_hits_in_one_game": max(k for n, k in stats),
            "deep_confirmed_episodic_games": confirmed,
            "conditional_concentration_tail": round(probability, 8),
        })
    # Bonferroni counts ALL eligible strata, even ones with zero extreme
    # episodes: selection based only on success would inflate the claim.
    comparisons = len(tested)
    for record in tested:
        record["adjusted_tail"] = round(
            min(1.0, comparisons * record["conditional_concentration_tail"]), 8)
        record["exploratory_flag"] = bool(
            record["deep_confirmed_episodic_games"] >= 1
            and record["adjusted_tail"] <= EPISODIC_ALPHA)
    return {
        "schema": SCHEMA, "scoring_influence": "MODERATE_MANUAL_TRIAGE_ONLY",
        "comparisons": comparisons, "strata": tested,
        "exploratory_flag": any(row["exploratory_flag"] for row in tested),
        "interpretation": (
            "Within-player conditional exchangeability is not a calibrated "
            "fair-play null. Heterogeneous decision difficulty, opponent skill "
            "and selective engine assistance are alternative explanations. "
            "This flags manual review only; never HIGH or proof."),
    }


def integrate(result, games, config):
    report = {
        "schema": SCHEMA,
        "feasibility": opportunity_feasibility(games, config),
        "episodic": episodic_audit(games, config),
    }
    result.diagnostics["opportunity_feasibility_audit"] = report
    if result.priority == "LOW" and report["episodic"]["exploratory_flag"]:
        result.priority = "MODERATE"
        result.diagnostics["moderate_path"] = "Deep-confirmed concentrated episodic gameplay (exploratory)"
        result.reasons = [
            "A concentrated series of difficult, quality-preserving decisions "
            "in one game is unusual compared with the player's own measured "
            "opportunities, accounting for inspected time-control strata.",
            "This is exploratory MODERATE manual-review triage. Its conditional "
            "reference is not independently calibrated and cannot establish "
            "misconduct, raise HIGH or justify an automated sanction.",
        ]
    return result
