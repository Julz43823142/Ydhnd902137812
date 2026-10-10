"""Exploratory Fair Play research audit. Never used for priority or punishment.

Inspired by Regan's candidate-aware modelling, Maia human-move policies,
position-conditioned timing models and ChessFraud-style holdout validation.
These are game-block diagnostics, NOT calibrated cheating probabilities.
Only bounded aggregates are exported; never accounts, FENs, PGNs or move lists.
"""
import math
import random
import statistics
from collections import defaultdict

from fairplay_config import CONFIG

WINDOWS = (6, 10, 20, 50)
BOOTSTRAPS = 160
PERMUTATIONS = 160


def _quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[int((len(ordered) - 1) * q)], 5)


def _game_observation(game, config):
    """Do not infer absent clocks, Maia policy or invalid engine contracts."""
    decisions = getattr(game, "decisions", ())
    ordinary = [d.think for d in decisions
                if getattr(d, "clock_valid", False)
                and isinstance(getattr(d, "think", None), (int, float))
                and math.isfinite(d.think) and d.think >= 0
                and getattr(d, "phase", None) != "opening"
                and not getattr(d, "forced", False)
                and not getattr(d, "trivial_kind", None)]
    typical = statistics.median(ordinary) if len(ordinary) >= 5 else None
    quality = []
    eligible = joint = timed = policy_covered = 0
    for d in decisions:
        m = getattr(d, "metrics", {}) or {}
        contract = m.get("search_contract") or {}
        if (not m.get("useful") or contract.get("completed") is not True
                or contract.get("exact") is not True):
            continue
        eligible += 1
        q = m.get("quality_excess")
        if isinstance(q, (float, int)) and math.isfinite(q):
            quality.append(max(0.0, float(q)))
        clock = (typical is not None and getattr(d, "clock_valid", False)
                 and isinstance(getattr(d, "think", None), (float, int))
                 and math.isfinite(d.think) and d.think >= 0)
        if clock:
            timed += 1
        policy = getattr(d, "human_policy", None)
        if not isinstance(policy, dict) or d.move not in policy:
            continue
        mass = sum(p for p in policy.values()
                   if isinstance(p, (int, float)) and math.isfinite(p) and p >= 0)
        if not .995 <= mass <= 1.005:
            continue
        played = policy[d.move]
        if not isinstance(played, (int, float)) or not math.isfinite(played):
            continue
        policy_covered += 1
        if (clock and m.get("near_best") is True
                and m.get("difficulty", 0) >= config.human_difficulty_floor
                and played <= .05 and d.think <= .5 * max(typical, .1)):
            joint += 1
    return {"eligible":eligible, "quality":sum(quality) / len(quality) if quality else None,
            "joint":joint, "joint_denominator":policy_covered,
            "timed":timed, "policy_covered":policy_covered}


def _window_max(values):
    """Window search is paired with a MAX-statistic permutation diagnostic."""
    if len(values) < WINDOWS[0]:
        return None
    best = 0.0
    centered = [x - sum(values) / len(values) for x in values]
    for window in WINDOWS:
        if window > len(centered):
            break
        rolling = sum(centered[:window])
        best = max(best, rolling / window)
        for i in range(window, len(centered)):
            rolling += centered[i] - centered[i-window]
            best = max(best, rolling / window)
    return round(best, 6)


def _bucket(rows):
    """Resample whole games; keep all moves from each game together."""
    scores = [r["quality"] for r in rows if r["quality"] is not None]
    combined = {"games":len(rows), "eligible_moves":sum(r["eligible"] for r in rows),
                "clock_covered_moves":sum(r["timed"] for r in rows),
                "maia_covered_moves":sum(r["policy_covered"] for r in rows),
                "joint_fast_rare_high_quality_moves":sum(r["joint"] for r in rows),
                "joint_status":"descriptive_only_not_an_independent_vote"}
    if len(scores) < 6:
        combined.update(game_block_quality_mean=None, game_block_quality_interval=None,
                        max_window_excess=None, max_window_shuffle_fraction=None,
                        uncertainty="insufficient_complete_games_for_game_block_test")
        return combined
    mean = sum(scores) / len(scores)
    # Seed is constant and never derived from an account, username or game ID.
    rng = random.Random(0xC4E55)
    estimates = [sum(scores[rng.randrange(len(scores))]
                     for _ in scores) / len(scores) for _ in range(BOOTSTRAPS)]
    combined["game_block_quality_mean"] = round(mean, 5)
    combined["game_block_quality_interval"] = [_quantile(estimates, .025),
                                                 _quantile(estimates, .975)]
    observed = _window_max(scores)
    combined["max_window_excess"] = observed
    if observed is None:
        combined["max_window_shuffle_fraction"] = None
    else:
        exceed = 0
        for _ in range(PERMUTATIONS):
            shuffled = scores.copy()
            rng.shuffle(shuffled)
            if _window_max(shuffled) >= observed - 1e-7:
                exceed += 1
        combined["max_window_shuffle_fraction"] = round(
            (1 + exceed) / (1 + PERMUTATIONS), 4)
    combined["uncertainty"] = (
        "Exploratory within-bucket shuffled-order diagnostic; adjacent games "
        "may be dependent. Not a calibrated p-value or cheating probability.")
    return combined


def summarize_research(games, config=CONFIG):
    buckets = defaultdict(list)
    # Retain chronological ordering within comparable rated time controls.
    selected = sorted(
        (g for g in games if getattr(g, "rated", None) is True
         and not getattr(g, "probe_only", False) and getattr(g, "deep", False)),
        key=lambda g: (getattr(g, "ended", 0), getattr(g, "identity", "")))
    for game in selected:
        label = (str(getattr(game, "time_class", "unknown")),
                 str(getattr(game, "time_control", "unknown")))
        if label[0] not in ("rapid", "blitz", "bullet"):
            continue
        buckets[label].append(_game_observation(game, config))
    # Per-control metrics, never raw moves, usernames, game IDs or FENs.
    summary = {f"{kind} | {control}":_bucket(rows)
               for (kind, control), rows in sorted(buckets.items())}
    return {"schema":"sharkbot-fairplay-research-v1",
            "scoring_influence":False,
            "interpretation":"Research-only. No accusation or independent vote.",
            "evidence_families":("objective_engine_quality", "human_move_policy",
                                 "position_difficulty", "observed_clock_time"),
            "compared_games":len(selected),
            "buckets":summary,
            "validation":{"labeled_holdout_tested":False,
                          "chessfraud_offline_benchmark":"scripts/benchmark_chessfraud.py",
                          "false_positive_rate":None,
                          "warning":"Requires separately validated held-out fair-play and normal cases."},
            "limitations":(
                "Same-game moves are correlated; resample whole games, not positions.",
                "Temporal shuffle assumes exchangeable game order and is descriptive.",
                "Absent clocks or Maia policies never count as negative evidence.",
                "Maia rarity and engine quality are correlated gameplay features.",
                "No website has publicly provided Chess.com enforcement internals.")}


