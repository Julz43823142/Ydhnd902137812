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

MODE = "fairplay-v51-depth-only-shadow"
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
                   and isfinite(d.think) and 0 <= d.think <= 120]
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


def _verify_deep(game, config):
    requested = config.bullet_deep_depth if game.time_class == "bullet" else 18
    return bool(game.decisions) and all(
        d.metrics.get("search_contract", {}).get("mode") == "depth"
        and d.metrics["search_contract"].get("requested") == requested
        and d.metrics["search_contract"].get("multipv") == config.deep_multipv
        and d.metrics["search_contract"].get("completed") is True
        and d.metrics["search_contract"].get("exact") is True
        and d.metrics.get("search_depth", 0) >= requested
        for d in game.decisions)


def review(target, progress, config=CONFIG, *, api_factory=PubAPI,
           engine_factory=None, cancel=None, engine_workers=None,
           engine_pool=None, checkpoint=None):
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
    try:
        progress("Collecting games for deep-only review…")
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
        missing = [(game, d) for game in games if not game.deep
                   for d in game.decisions]
        progress(f"Depth-only Stockfish: 0 / {len(missing)} local positions")
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
                    if checkpoint is not None:
                        checkpoint.record(target, game, decision, "deep")
            if scheduled:
                for future, game, decision in scheduled:
                    future.result()
                    if checkpoint is not None:
                        checkpoint.record(target, game, decision, "deep")
            progress(f"Depth-only Stockfish: {min(offset + len(batch), len(missing))} / {len(missing)} local positions")
        for game in games:
            if not _verify_deep(game, config):
                raise ReviewError("Complete exact Stockfish depth coverage was not achieved. No review issued.")
            summarize(game, config)
            game.deep = True
        grouped = observations(games)
        diagnostics = summarize_observations(grouped)
        totals = aggregate(games)
        classes = {kind: aggregate([g for g in games if g.time_class == kind])
                   for kind in CLASSES}
        elapsed = time.monotonic() - began
        result = ReviewResult(
            username=target, games=games, selected_games=len(games),
            skipped=skipped, partial=partial, engine=engine_name,
            totals=totals, classes=classes, performance={}, context={},
            families={}, priority="INSUFFICIENT DATA", confidence="LOW",
            reasons=["Experimental depth-only anomaly audit; no validated cheating threshold. Human review required."],
            deep_confirmed=False, deep_coverage={"games": len(games)},
            elapsed=elapsed, version=MODE,
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
        progress("Building per-control charts…")
        return result
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if private_scanner is not None:
            private_scanner.close()
        api.close()


def chart_pngs(grouped):
    """Produce independent static PNG reports with game number on every x-axis.

    No population baseline is synthesized. Clock gaps are missing observations.
    """
    from xml.sax.saxutils import escape
    import cairosvg

    def text(x, y, value, size=15, color="#132136", anchor="start"):
        return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
                f'text-anchor="{anchor}" font-family="sans-serif">{escape(str(value))}</text>')

    result = []
    for kind in CLASSES:
        rows = grouped.get(kind, [])
        if not rows:
            continue
        width, height = 1160, 1210
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                 '<rect width="100%" height="100%" fill="#ffffff"/>',
                 text(50, 49, f"{kind.title()} — Fair Play Game-by-Game", 28),
                 text(50, 78, f"{len(rows)} games • Official Chess.com Accuracy when available • No population benchmark", 14, "#526477")]
        def plot(y_top, title, values, y_min, y_max, unit, color, second=None):
            left, right, h = 86, 1110, 185
            parts.append(text(50, y_top, title, 20))
            chart_top = y_top + 22
            parts.append(f'<rect x="{left}" y="{chart_top}" width="{right-left}" height="{h}" fill="#f8fafc" stroke="#cad5df"/>')
            for fraction in (0, .25, .5, .75, 1):
                yy = chart_top + h * (1 - fraction)
                parts.append(f'<line x1="{left}" y1="{yy}" x2="{right}" y2="{yy}" stroke="#e3ebf0"/>')
                parts.append(text(left-9, yy+5, f"{y_min+(y_max-y_min)*fraction:.0f}{unit}", 12, anchor="end"))
            parts.append(text(left, chart_top+h+22, "Game 1", 13))
            parts.append(text(right, chart_top+h+22, f"Game {len(rows)}", 13, anchor="end"))
            def draw(series, stroke, dashed=False):
                segment = []
                for index, value in enumerate(series):
                    if value is None:
                        if len(segment) >= 2:
                            parts.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{stroke}" stroke-width="2.5" '+('stroke-dasharray="7 5"' if dashed else '')+'/>')
                        segment = []
                        continue
                    v = max(y_min, min(y_max, value))
                    xx = left + (right-left) * index / max(1, len(rows)-1)
                    yy = chart_top + h * (1 - (v-y_min)/max(.000001, y_max-y_min))
                    segment.append(f"{xx:.1f},{yy:.1f}")
                if len(segment) >= 2:
                    parts.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{stroke}" stroke-width="2.5" '+('stroke-dasharray="7 5"' if dashed else '')+'/>')
                elif len(segment) == 1:
                    xx, yy = segment[0].split(",")
                    parts.append(f'<circle cx="{xx}" cy="{yy}" r="3" fill="{stroke}"/>')
            draw(values, color)
            if second is not None:
                draw(second, "#f08b38", True)
        plot(125, "Accuracy by Game (blue: estimated; orange: Chess.com)",
             [r["estimated_accuracy"] for r in rows], 0, 100, "%",
             "#2563eb", [r["official_accuracy"] for r in rows])
        elo = [r["opponent_elo"] for r in rows if r["opponent_elo"] is not None]
        elo_min = max(0, (int(min(elo)) // 200) * 200) if elo else 0
        elo_max = max(elo_min + 200, ((int(max(elo)) + 199) // 200) * 200) if elo else 3000
        plot(418, "Opponent Elo by Game",
             [r["opponent_elo"] for r in rows], elo_min, elo_max, "", "#0f766e")
        plot(707, "Median Think Time by Game (valid PGN clocks only)",
             [r["median_think_seconds"] for r in rows], 0, 120, "s", "#7c3aed")
        parts.append(text(50, 1010, "Accuracy by Opponent Elo (200-point groups)", 20))
        bins = defaultdict(list)
        for row in rows:
            acc = row["official_accuracy"] if row["official_accuracy"] is not None else row["estimated_accuracy"]
            if row["opponent_elo"] is not None and acc is not None:
                bins[int(row["opponent_elo"]) // 200 * 200].append(acc)
        keys = sorted(bins)
        if keys:
            left, right, y_base = 90, 1100, 1150
            band = (right-left) / len(keys)
            for ix, key in enumerate(keys):
                center = left + (ix+.5)*band
                bar_height = 104 * mean(bins[key])/100
                parts.append(f'<rect x="{center-band*.34:.1f}" y="{y_base-bar_height:.1f}" width="{band*.68:.1f}" height="{bar_height:.1f}" fill="#2563eb"/>')
                parts.append(text(center, y_base-bar_height-8, f"{mean(bins[key]):.1f}%", 12, anchor="middle"))
                parts.append(text(center, y_base+19, f"{key}–{key+199}", 11, anchor="middle"))
                parts.append(text(center, y_base+36, f"n={len(bins[key])}", 11, "#526477", "middle"))
        else:
            parts.append(text(90, 1100, "Insufficient rating and accuracy data"))
        parts.append("</svg>")
        png = cairosvg.svg2png(bytestring="".join(parts).encode("utf-8"))
        result.append((f"fairplay_{kind}_charts.png", png))
    return result
