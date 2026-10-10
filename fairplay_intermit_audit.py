"""Read-only intermittent-evidence and coverage audit for fully analysed histories.

Unlike legacy game-level scoring, this counts short deep-reviewed games with
1-7 useful decisions in the opportunity denominator. Chronological windows
are picked with FAST evidence only, then checked against all corresponding
DEEP outcomes, including failures. This does not establish misconduct and
does not modify LOW/MODERATE/HIGH/VERY HIGH.
"""
from __future__ import annotations

from collections import defaultdict

SCHEMA = "sharkbot-intermittent-coverage-v1"
WINDOWS = (3, 5, 8, 10, 15)


def _metric(game, *, fast=False):
    if fast:
        # Never replace a missing fast measurement with a future deep result:
        # that would select the research window using the outcome under test.
        return getattr(game, "fast_metrics", None) or {}
    return getattr(game, "metrics", {}) or {}


def _observation(game, *, fast=False):
    metrics = _metric(game, fast=fast)
    human = metrics.get("human") or {}
    return {
        "meaningful": int(metrics.get("decisions") or 0),
        "opportunities": int(human.get("opportunities") or 0),
        "hits": int(human.get("hits") or 0),
        "stable_hits": int(human.get("stable_hits") or 0),
        "blunders": int(metrics.get("blunders") or 0),
        "critical": int(metrics.get("critical") or 0),
    }


def _aggregate(games, *, fast=False):
    observed = [_observation(g, fast=fast) for g in games]
    totals = {name: sum(item[name] for item in observed)
              for name in ("meaningful", "opportunities", "hits", "stable_hits",
                           "blunders", "critical")}
    totals.update(games=len(games),
                  contributor_games=sum(x["hits"] > 0 for x in observed),
                  opportunity_games=sum(x["opportunities"] > 0 for x in observed),
                  short_games=sum(0 < x["meaningful"] < 8 for x in observed),
                  zero_decision_games=sum(x["meaningful"] == 0 for x in observed))
    totals["hit_rate"] = (round(totals["hits"] / totals["opportunities"], 4)
                          if totals["opportunities"] else None)
    return totals


def _runs(games):
    groups = defaultdict(list)
    for g in games:
        if (getattr(g, "deep", False) and getattr(g, "rated", None) is True
                and not getattr(g, "probe_only", False)
                and getattr(g, "time_class", None) in ("blitz", "rapid", "bullet")):
            groups[(g.time_class, str(getattr(g, "time_control", "")))].append(g)
    for (kind, control), series in sorted(groups.items()):
        items = sorted(series, key=lambda g: (g.ended, g.identity))
        run = []
        for g in items:
            index = getattr(g, "control_index", None)
            prev = getattr(run[-1], "control_index", None) if run else None
            if (run and index is not None and prev is not None
                    and index != prev + 1):
                yield kind, control, run
                run = []
            run.append(g)
        if run:
            yield kind, control, run


def summarize_intermit(games, *, minimum_game_decisions=8):
    games = list(games)
    scanned = [g for g in games
               if getattr(g, "deep", False) and getattr(g, "rated", None) is True
               and not getattr(g, "probe_only", False)]
    overall = _aggregate(scanned)
    # Match score_review's *actual* two-pass per-game eligibility gate.
    # A deep-only count can rise above eight when the position classification
    # changes at depth 18/12, yet its FAST pass remains under eight. Such games
    # do not contribute to the official 'gameplay-scoring games' total.
    def fast_for_scoring(game):
        return (getattr(game, "fast_metrics", None)
                or getattr(game, "metrics", {}) or {})
    fast_qualified = {
        g.identity for g in scanned
        if int(fast_for_scoring(g).get("decisions") or 0) >= minimum_game_decisions
    }
    deep_qualified = {
        g.identity for g in scanned
        if _observation(g)["meaningful"] >= minimum_game_decisions
    }
    fully_scored = len(fast_qualified & deep_qualified)
    overall.update(fully_scored_games=fully_scored,
                   fast_minimum_games=len(fast_qualified),
                   deep_minimum_games=len(deep_qualified),
                   fast_pass_only_games=len(fast_qualified - deep_qualified),
                   deep_pass_only_games=len(deep_qualified - fast_qualified),
                   below_general_game_minimum=len(scanned) - fully_scored,
                   general_game_minimum=minimum_game_decisions)
    summary = {"schema": SCHEMA, "scoring_influence": False,
               "deep_sample": overall,
               "classes": {}, "chronological_research_candidate": None,
               "interpretation": (
                   "Only a FAST-selected period for human review research. "
                   "Short/easy/poor-quality games are kept in denominators. "
                   "Neither this audit nor Maia rarity independently proves misconduct.")}

    for kind in ("rapid", "blitz", "bullet"):
        subset = [g for g in scanned if getattr(g, "time_class", None) == kind]
        if subset:
            summary["classes"][kind] = _aggregate(subset)

    # Select a single pre-declared window from fast data only, not by how
    # strongly the deep result eventually confirms it. Overlapping candidate
    # windows are not independent evidence.
    candidate = None
    rank = None
    windows_examined = 0
    for kind, control, run in _runs(games):
        for width in WINDOWS:
            if width > len(run):
                continue
            for i in range(len(run) - width + 1):
                group = run[i:i + width]
                fast = _aggregate(group, fast=True)
                windows_examined += 1
                if (fast["opportunities"] < 5
                        or fast["contributor_games"] < 2):
                    continue
                # Fixed opportunity-volume-first ordering, then hit fraction:
                # short perfect games cannot outrank substantially sampled
                # periods. Candidate selection is ONLY a diagnostic.
                order = (min(fast["opportunities"], 25),
                         min(fast["contributor_games"], 6),
                         fast["hits"] / max(1, fast["opportunities"]),
                         -width)
                if rank is None or order > rank:
                    rank = order
                    candidate = (kind, width, group, fast)
    summary["windows_examined"] = windows_examined
    if candidate is not None:
        kind, width, group, fast = candidate
        deep = _aggregate(group)
        summary["chronological_research_candidate"] = {
            "time_class": kind,
            "games": width,
            "fast": fast,
            "deep": deep,
            "deep_to_fast_hit_retention": (
                round(deep["hits"] / fast["hits"], 4) if fast["hits"] else None),
            "reason": (
                "Predefined fast-only window search. Deep follow-up includes "
                "mistakes, sparse games and non-hits; no independent calibration."),
        }
    # Fixed chronological halves are selected before observing success. This
    # detects *possible changes* in the player's own gameplay distribution,
    # without choosing the best streak or borrowing isolated wins. Adjacent
    # games stay paired by identical time control and original archive index.
    halves = []
    for kind, control, run in _runs(games):
        if len(run) < 12:
            continue
        midpoint = len(run) // 2
        earlier, later = run[:midpoint], run[midpoint:]
        a, b = _aggregate(earlier), _aggregate(later)
        if a["opportunities"] < 5 or b["opportunities"] < 5:
            continue
        halves.append({
            "time_class": kind, "games": len(run),
            "earlier": a, "later": b,
            "deep_hit_rate_difference": round(
                b["hits"] / b["opportunities"]
                - a["hits"] / a["opportunities"], 4),
            "note": "Fixed chronological halves; descriptive, not a cheating test.",
        })
    summary["predeclared_half_comparisons"] = sorted(
        halves, key=lambda x: (-x["games"], x["time_class"]))[:6]

    summary["maia_rating_domain"] = {
        "below_600_chesscom_rating_games": sum(
            isinstance(getattr(g, "rating", None), (int, float))
            and g.rating < 600 for g in scanned),
        "rating_known_games": sum(
            isinstance(getattr(g, "rating", None), (int, float))
            for g in scanned),
        "note": (
            "Maia reference targets Lichess-rated human populations. Chess.com "
            "ratings are not interchangeable, especially at very low levels. "
            "Uncalibrated rating conditioning must not be treated as proof."),
    }
    return summary
