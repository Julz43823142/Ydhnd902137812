"""Fair Play v51: complete depth-only observational experiment.

Opt-in from /fairplay with FAIRPLAY_V51=1. This shadow methodology has no
cheating classification or moderation authority. No fast engine search, Maia
inference, personal identity archive, or external baseline is invented.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from math import exp, isfinite
from statistics import mean, median
import os
import time

import chess.engine

from fairplay_config import CONFIG, VERSION
from fairplay_data import (PubAPI, ReviewError, ScanDeadline, collect_games,
                           check_deadline, username)
from fairplay_analysis import (ReviewResult, aggregate, get_shared_engine_pool,
                               EngineScanner, summarize)

MODE = "fairplay-v52-depth-review-priority"
CANDIDATE_MODE = "fairplay-v53-relative-gameplay-research"
CLASSES = ("bullet", "blitz", "rapid")
MAX_GAMES = 500


def _finite(value):
    return (float(value) if type(value) in (int, float) and isfinite(value)
            else None)


def _accuracy(game):
    """Independent descriptive estimate; NOT recovered Chess.com CAPS2.

    Use the same win-probability loss curve currently used by SharkBot Game
    Review. Only assessed non-book, non-forced player moves enter the mean.
    The official score, if present, is never used in our calculation.
    """
    from chess_play import _move_accuracy_from_win_loss
    rows = [d.metrics for d in game.decisions
            if d.metrics.get("search_contract", {}).get("completed") is True
            and d.phase != "opening" and not d.forced
            and d.metrics.get("search_inconsistent") is False
            and _finite(d.metrics.get("scaled_loss")) is not None]
    if not rows:
        return None
    return round(mean(_move_accuracy_from_win_loss(m["scaled_loss"] * 100)
                      for m in rows), 1)


def _validation(rows):
    differences = [row["estimated_accuracy"] - row["official_accuracy"] for row in rows
                   if row["estimated_accuracy"] is not None
                   and row["official_accuracy"] is not None]
    count = len(differences)
    return {"compared_games": count,
            "mae": round(mean(abs(x) for x in differences), 2) if count else None,
            "bias": round(mean(differences), 2) if count else None,
            "within_2": round(sum(abs(x) <= 2 for x in differences) / count, 3) if count else None,
            "within_5": round(sum(abs(x) <= 5 for x in differences) / count, 3) if count else None,
            "median_abs_error": round(median(abs(x) for x in differences), 2) if count else None}


def observations(games):
    """Chronological, class-specific game indices with missing-clock honesty."""
    grouped = {kind: [] for kind in CLASSES}
    for game in sorted(games, key=lambda g: (g.ended, g.identity)):
        if game.time_class not in grouped:
            continue
        seconds = [d.think for d in game.decisions
                   if d.clock_valid and d.think is not None and d.phase != "opening"
                   and isfinite(d.think) and d.think >= 0]
        group = grouped[game.time_class]
        group.append({
            "game": len(group) + 1,
            "control": game.time_control,
            "rated": game.rated,
            "official_accuracy": _finite(game.accuracy),
            "estimated_accuracy": _accuracy(game),
            "opponent_elo": _finite(game.opponent_rating),
            "player_elo": _finite(game.rating),
            "median_think_seconds": round(median(seconds), 2) if seconds else None,
            "clock_moves": len(seconds),
            "moves": len(game.decisions),
        })
    return grouped


def class_anomalies(rows):
    """Descriptive changes only; no unvalidated z-scores or accusations.

    Compare ordered equal-control/equal-rated-status games. Inspect both
    accuracy and opponent strength; a rating drop blocks the simple jump flag.
    These are research markers, not a normative population reference.
    """
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row["control"], row["rated"])].append(row)
    flags = []
    for (control, rated), group in sorted(buckets.items()):
        eligible = [r for r in group
                    if r["estimated_accuracy"] is not None
                    and r["opponent_elo"] is not None]
        if len(eligible) < 24:
            continue
        half = len(eligible) // 2
        older, newer = eligible[:half], eligible[half:]
        early_accuracy = mean(r["estimated_accuracy"] for r in older)
        late_accuracy = mean(r["estimated_accuracy"] for r in newer)
        early_elo = median(r["opponent_elo"] for r in older)
        late_elo = median(r["opponent_elo"] for r in newer)
        rise = round(late_accuracy - early_accuracy, 1)
        elo_shift = round(late_elo - early_elo)
        if rise >= 10 and elo_shift >= -75:
            flags.append({"control": control, "rated": rated,
                          "games": len(eligible), "accuracy_shift": rise,
                          "opponent_elo_shift": elo_shift,
                          "label": "Descriptive accuracy rise; human review only"})
    return flags


def summarize_observations(grouped):
    rows = [row for rows in grouped.values() for row in rows]
    return {"schema": MODE, "deep_games": len(rows), "fast_games": 0,
            "accuracy_validation": _validation(rows),
            "by_class": {
                kind: {"games": len(grouped[kind]),
                       "clock_games": sum(r["median_think_seconds"] is not None
                                          for r in grouped[kind]),
                       "accuracy_validation": _validation(grouped[kind]),
                       "flags": class_anomalies(grouped[kind])}
                for kind in CLASSES},
            "population_reference": "Not calibrated; no population reference line",
            "priority_authority": False,
            "clock_source": "PGN per-move [%clk], when provided"}


def _verified_decision(decision, kind, config):
    requested = config.bullet_deep_depth if kind == "bullet" else 18
    metrics = decision.metrics
    if not isinstance(metrics, dict):
        return False
    contract = metrics.get("search_contract")
    if not isinstance(contract, dict):
        return False
    return (contract.get("mode") == "depth"
            and type(contract.get("requested")) is int
            and contract["requested"] == requested
            and type(contract.get("multipv")) is int
            and contract["multipv"] == config.deep_multipv
            and contract.get("completed") is True
            and contract.get("exact") is True
            and type(metrics.get("search_depth")) is int
            and metrics["search_depth"] >= requested)


def _verify_deep(game, config):
    return bool(game.decisions) and all(
        _verified_decision(d, game.time_class, config) for d in game.decisions)


def review(target, progress, config=CONFIG, *, api_factory=PubAPI,
           engine_factory=None, cancel=None, engine_workers=None,
           engine_pool=None, checkpoint=None, candidate=False):
    """One 500-game deep Stockfish pass; no fast stage or Maia stage.

    Remote shards are attempted where available. Exact local checkpoint
    recovery covers incomplete shards. No result is returned for partial or
    inexact depth coverage.
    """
    if config != CONFIG:
        raise ReviewError("v51 must use the production configuration.")
    began = time.monotonic()
    seconds = max(0, int(os.getenv("FAIRPLAY_FULL_SCAN_DEADLINE_SECONDS", "0")))
    deadline = ScanDeadline(began + seconds if seconds else float("inf"), cancel)
    target = username(target)
    api = api_factory(deadline)
    private_scanner = None
    executor = None
    checkpoint_dirty = 0

    def record_completed(game, decision):
        nonlocal checkpoint_dirty
        if checkpoint is not None:
            saved = checkpoint.record(target, game, decision, "deep", persist=False)
            checkpoint_dirty += int(bool(saved))
            if checkpoint_dirty >= 32:
                flush_checkpoint()

    def flush_checkpoint():
        nonlocal checkpoint_dirty
        if checkpoint_dirty and checkpoint is not None:
            checkpoint.flush(force=False)
            checkpoint_dirty = 0
    try:
        progress("Fetching profile…")
        profile = api.get(target, profile=True)
        if not isinstance(profile, dict) or username(profile.get("username", "")) != target:
            raise ReviewError("The public profile cannot be verified.")
        games, skipped, partial = collect_games(
            api, target, progress, config,
            include_unrated=True, include_latest_fifty_short=True,
            include_recent_all_short_count=MAX_GAMES)
        games = sorted((g for g in games if g.time_class in CLASSES
                        and type(g.rated) is bool),
                       key=lambda g: (g.ended, g.identity))[-MAX_GAMES:]
        if not games:
            raise ReviewError("No eligible Bullet, Blitz, or Rapid games were found.")
        check_deadline(deadline)
        if callable(getattr(progress, 'workload', None)):
            progress.workload({'deep': sum(len(g.decisions) for g in games)})
        if engine_factory is None:
            pool = engine_pool or get_shared_engine_pool(config)
            engine_name = pool.name
            workers = pool.size
        else:
            pool = None
            private_scanner = EngineScanner(deadline, config, engine_factory)
            engine_name = private_scanner.name
            workers = 1
        if checkpoint is not None:
            from fairplay_maia import MODEL_SHA256
            checkpoint.bind(target, engine=engine_name, version=VERSION,
                            config=config, full_depth=True, maia=MODEL_SHA256)
        completed_remote = {}
        remote_handle = None
        if (engine_factory is None and pool is not None
                and os.getenv("FAIRPLAY_DISTRIBUTED") == "1"
                and os.getenv("GITHUB_ACTIONS") == "true"):
            from fairplay_distributed import start
            remote_handle = start(games, target,
                                  revision=os.getenv("GITHUB_SHA", ""),
                                  engine=engine_name)
        if remote_handle:
            from fairplay_distributed import join
            progress("Waiting for deep Stockfish workers…")
            completed_remote = join(remote_handle, progress, deadline)
        by_id = {g.identity: g for g in games}
        for game_id, remote in completed_remote.items():
            if game_id in by_id and _verify_deep(remote, config):
                by_id[game_id].decisions = remote.decisions
                by_id[game_id].deep = True
        missing = [(game, d) for game in games for d in game.decisions
                   if not _verified_decision(d, game.time_class, config)]
        progress(f"Depth-18 rapid/blitz · depth-12 bullet: 0 / {len(missing)} positions")
        # Bounded batches; no 500-game backlog of concurrent future objects.
        if pool is not None:
            executor = ThreadPoolExecutor(max_workers=max(1, min(workers, 16)))
        for offset in range(0, len(missing), 128):
            check_deadline(deadline)
            batch = missing[offset:offset + 128]
            scheduled = []
            for game, decision in batch:
                if (checkpoint is not None
                        and checkpoint.restore(target, game, decision, "deep", config, True)):
                    continue
                budget = chess.engine.Limit(
                    depth=config.bullet_deep_depth if game.time_class == "bullet" else 18)
                if executor is not None:
                    future = executor.submit(pool.run_decision, game, decision, budget, deadline)
                    scheduled.append((future, game, decision))
                else:
                    private_scanner.analyse_decision(game, decision, budget)
                    record_completed(game, decision)
            if scheduled:
                pending = {future: (game, decision) for future, game, decision in scheduled}
                for future in as_completed(pending):
                    game, decision = pending[future]
                    future.result()
                    record_completed(game, decision)
            flush_checkpoint()
            progress(f"Depth-18 rapid/blitz · depth-12 bullet: {min(offset + len(batch), len(missing))} / {len(missing)} positions")
        for game in games:
            if not _verify_deep(game, config):
                raise ReviewError("Complete exact Stockfish depth coverage was not achieved. No review issued.")
            summarize(game, config, relative=candidate)
            game.deep = True
        grouped = observations(games)
        diagnostics = summarize_observations(grouped)
        if candidate:
            diagnostics['schema'] = CANDIDATE_MODE
        from fairplay_depth_priority import review_priority
        tier = review_priority(games, config, complete=True, candidate=candidate,
            incomplete_archive_months=getattr(api, 'fairplay_collection_coverage', {}).get(
                'incomplete_archive_months', ()))
        diagnostics["review_priority"] = tier
        totals = aggregate(games)
        classes = {kind: aggregate([g for g in games if g.time_class == kind])
                   for kind in CLASSES}
        elapsed = time.monotonic() - began
        result = ReviewResult(
            username=target, games=games, timeline=games, selected_games=len(games),
            skipped=skipped, partial=partial, engine=engine_name,
            totals=totals, classes=classes, performance={}, context={},
            families={}, priority=tier["priority"], confidence=tier["confidence"],
            reasons=tier["reasons"],
            deep_confirmed=False, deep_coverage={"games": len(games)},
            elapsed=elapsed, version=CANDIDATE_MODE if candidate else MODE,
            coverage={"deep_reviewed": len(games), "fast_scanned": 0,
                      "primary_collected": len(games), "used": len(games),
                      "rated_primary": sum(g.rated is True for g in games),
                      "casual_primary": sum(g.rated is False for g in games),
                      "requested_primary_limit": MAX_GAMES},
            diagnostics={"v51_shadow": diagnostics,
                         "run_contract": {"engine": engine_name,
                                          "fast": None,
                                          "deep": {"mode": "depth", "multipv": config.deep_multipv,
                                                   "bullet": config.bullet_deep_depth,
                                                   "blitz": 18, "rapid": 18},
                                          "code_revision": os.getenv("GITHUB_SHA", "")[:12]},
                         "v51_elapsed_seconds": round(elapsed, 2),
                         "v51_distributed_games": len(completed_remote)})
        progress("Building report…")
        return result
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        try:
            flush_checkpoint()
        finally:
            if private_scanner is not None:
                private_scanner.close()
            api.close()


def chart_pngs(grouped):
    """Compatibility entry point for the shared readable chart renderer."""
    from fairplay_charts import chart_pngs as render
    return render(grouped)
