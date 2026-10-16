"""Continuous independent SharkBot accuracy research without Chess.com data.

Generates reproducible, ORIGINAL standard-chess games locally. Compares
Stockfish 19 fast reviews to independently computed Stockfish 19 depth-18
reference reviews. Searches numeric scoring parameters on a training split
and reports held-out accuracy. NEVER contacts Chess.com, imports their
scores/PGNs/Brilliant labels, modifies production, or claims CAPS2 recovery.

Each hourly GitHub Actions execution is bounded. Checkpoints in a separate
Git branch make consecutive runs resumable; GitHub schedules are best effort.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import tempfile

import chess

MODES = ("depth18", "fast")
MODEL_VERSION = "independent-sf19-comparison-v1"
TOTAL_GAMES = 100


class DeadlineReached(Exception):
    pass


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def own_game(index, *, maximum_plies=48):
    """Generated entirely offline: no player names, downloaded PGNs, or APIs."""
    if not 0 <= index < TOTAL_GAMES:
        raise ValueError("Game seed outside configured range")
    rng = random.Random("sharkbot-original-position-v1-" + str(index))
    board = chess.Board()
    san = []
    for _ in range(maximum_plies):
        if board.is_game_over(claim_draw=True):
            break
        legal = sorted(board.legal_moves, key=lambda move: move.uci())
        # Capture/check moves make better tactical tests than fully uniform play.
        weights = [3 if board.is_capture(m) else (2 if board.gives_check(m) else 1)
                   for m in legal]
        selected = rng.choices(legal, weights=weights, k=1)[0]
        san.append(board.san(selected))
        board.push(selected)
    return {"id": f"original-{index:03d}", "san": san,
            "sha256": hashlib.sha256(" ".join(san).encode()).hexdigest()}


def metric_path(state, mode, game):
    return Path(state) / "metrics" / mode / (game["id"] + ".json")


def validate_metric(metric, mode, game):
    if not isinstance(metric, dict):
        return False
    if (metric.get("model_version") != MODEL_VERSION or metric.get("mode") != mode
            or metric.get("id") != game["id"] or metric.get("sha256") != game["sha256"]
            or metric.get("analysed_plies") != len(game["san"])):
        return False
    if not isinstance(metric.get("moves"), list) or len(metric["moves"]) != len(game["san"]):
        return False
    if any(not isinstance(row, dict)
           or row.get("side") not in ("white", "black")
           or type(row.get("win_loss_pct")) not in (float, int)
           or not math.isfinite(row["win_loss_pct"])
           or not 0 <= row["win_loss_pct"] <= 100 for row in metric["moves"]):
        return False
    scores = metric.get("reference_scores")
    return (isinstance(scores, dict) and all(
        type(scores.get(color)) in (float, int) and math.isfinite(scores[color])
        and 0 <= scores[color] <= 100 for color in ("white", "black")))


def evaluate_game(game, mode, *, analyze=None, interrupt_check=None):
    if mode not in MODES:
        raise ValueError("Unknown evaluation profile")
    if analyze is None:
        from chess_play import analyse_game_moves
        analyze = analyse_game_moves
    kwargs = {"interrupt_check": interrupt_check}
    if mode == "fast":
        kwargs["analysis_time_per_position"] = 0.12
    result = analyze(game["san"], **kwargs)
    if (result.get("truncated") or result.get("analysed_plies") != len(game["san"])
            or len(result.get("moves", [])) != len(game["san"])):
        raise ValueError("Engine returned an incomplete game")
    record = {"model_version": MODEL_VERSION, "mode": mode,
              "id": game["id"], "sha256": game["sha256"],
              "analysed_plies": len(game["san"]),
              "reference_scores": {c: float(result[c]["accuracy"]) for c in ("white", "black")},
              "moves": [{"side": row["side"], "win_loss_pct": float(row["win_loss_pct"]),
                         "classification": row["classification"],
                         "is_best": bool(row.get("is_best", False)),
                         "sacrifice_cp": int(row.get("sacrifice_cp", 0))}
                        for row in result["moves"]]}
    if not validate_metric(record, mode, game):
        raise ValueError("Engine metric does not pass integrity checks")
    return record


def stable_split(pairs):
    train, holdout = [], []
    for deep, fast in pairs:
        digest = hashlib.sha256(deep["id"].encode()).digest()[0]
        (holdout if digest % 4 == 3 else train).append((deep, fast))
    return train, holdout


def estimate(rows, settings):
    """Independent fast-to-deep score mapping, not a Chess.com formula."""
    scale, power, worst_weight = settings
    if not rows:
        return 100.0
    losses = [max(0.0, float(row["win_loss_pct"])) for row in rows]
    mean_loss = sum(value ** power for value in losses) / len(losses)
    # Independent calibration of penalized loss with rare-outlier sensitivity.
    effective = (1.0 - worst_weight) * mean_loss + worst_weight * max(v ** power for v in losses)
    return round(max(0.0, min(100.0, 100.0 * math.exp(-scale * effective))), 1)


def score_pairs(pairs, settings=None):
    deviations = []
    for deep, fast in pairs:
        for side in ("white", "black"):
            actual = deep["reference_scores"][side]
            if settings is None:
                predicted = fast["reference_scores"][side]
            else:
                rows = [r for r in fast["moves"] if r["side"] == side and r["classification"] != "book"]
                predicted = estimate(rows, settings)
            deviations.append(abs(actual - predicted))
    return round(statistics.mean(deviations), 4) if deviations else None


def search_formulas(pairs, *, trials=2000, seed=0):
    train, holdout = stable_split(pairs)
    baseline = {"training_mae": score_pairs(train), "holdout_mae": score_pairs(holdout)}
    if len(train) < 10 or len(holdout) < 3:
        return {"status": "insufficient_paired_original_games",
                "paired_games": len(pairs), "train_games": len(train), "holdout_games": len(holdout),
                "baseline": baseline, "no_chesscom_data": True}
    rng = random.Random(seed)
    settings_list = [(0.04, 1.0, 0.0), (0.025, 1.0, 0.1)]
    for _ in range(trials):
        settings_list.append((rng.uniform(0.001, 0.3), rng.uniform(0.5, 2.0),
                              rng.uniform(0.0, 0.5)))
    best = None
    best_train = float("inf")
    for settings in settings_list:
        value = score_pairs(train, settings)
        if value < best_train:
            best, best_train = settings, value
    return {"status": "evaluated", "paired_games": len(pairs),
            "train_games": len(train), "holdout_games": len(holdout),
            "baseline": baseline, "best_settings": {"loss_scale": round(best[0], 7),
            "loss_power": round(best[1], 7), "worst_move_weight": round(best[2], 7)},
            "candidate_training_mae": best_train,
            "candidate_holdout_mae": score_pairs(holdout, best),
            "target": "original Stockfish depth-18 review scores (NOT Chess.com)",
            "no_chesscom_data": True, "production_changed": False}


def run(state, *, max_minutes=47, trials=2000, max_games=100, now_fn=None,
        analyze=None, mode=None):
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    start = now_fn()
    deadline = start + timedelta(minutes=max_minutes)
    if mode is None:
        # Alternation per run, persistent even if a scheduled run is dropped.
        count_file = Path(state) / "run_counter.json"
        count = load_json(count_file, {"runs": 0}).get("runs", 0)
        selected = MODES[count % 2]
        atomic_json(count_file, {"runs": count + 1})
    else:
        selected = mode
    if selected not in MODES:
        raise ValueError("Unknown research mode")
    completed = 0
    errors = []
    interrupted = False
    for i in range(TOTAL_GAMES):
        game = own_game(i)
        path = metric_path(state, selected, game)
        if validate_metric(load_json(path), selected, game):
            continue
        if now_fn() >= deadline or completed >= max_games:
            break
        def check():
            if now_fn() >= deadline:
                raise DeadlineReached("Research execution time reached")
        try:
            check()
            metric = evaluate_game(game, selected, analyze=analyze, interrupt_check=check)
            atomic_json(path, metric)
            completed += 1
            print(f"Completed original game {game['id']} in {selected} profile", flush=True)
        except DeadlineReached:
            interrupted = True
            break
        except Exception as exc:
            errors.append({"game": game["id"], "error": f"{type(exc).__name__}: {exc}"[:150]})
            if len(errors) >= 3:
                break
        finally:
            if analyze is None:
                from chess_play import _close_analysis_engine
                _close_analysis_engine()
    pairs = []
    counts = {"depth18": 0, "fast": 0}
    for i in range(TOTAL_GAMES):
        game = own_game(i)
        found = {}
        for profile in MODES:
            record = load_json(metric_path(state, profile, game))
            if validate_metric(record, profile, game):
                found[profile] = record
                counts[profile] += 1
        if len(found) == 2:
            pairs.append((found["depth18"], found["fast"]))
    best = search_formulas(pairs, trials=trials, seed=int(start.timestamp()) % (2**32))
    if best["status"] == "evaluated":
        previous = load_json(Path(state) / "best.json", {})
        # Holdout is a report only; selection uses training performance.
        if (previous.get("paired_games", 0) < best["paired_games"] or
                previous.get("candidate_training_mae", float("inf")) > best["candidate_training_mae"]):
            atomic_json(Path(state) / "best.json", best)
    report = {"started_at_utc": start.isoformat(),
              "finished_at_utc": now_fn().isoformat(),
              "mode": selected, "status": "partial_errors" if errors else "completed",
              "new_completed_games": completed, "cached_profiles": counts,
              "paired_games": len(pairs), "interrupted": interrupted, "errors": errors,
              "optimization": best, "no_chesscom_data": True,
              "no_chesscom_requests": True, "no_production_changes": True}
    atomic_json(Path(state) / "latest_report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--max-minutes", type=int, default=47)
    parser.add_argument("--trials", type=int, default=2000)
    parser.add_argument("--max-games", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.max_minutes <= 55 or not 0 <= args.trials <= 10000 or not 1 <= args.max_games <= TOTAL_GAMES:
        parser.error("Invalid runtime or research limits")
    result = run(args.state_dir, max_minutes=args.max_minutes,
                 trials=args.trials, max_games=args.max_games)
    print(json.dumps(result, indent=2))
    return int(result["status"] == "partial_errors")


if __name__ == "__main__":
    sys.exit(main())
