"""Nightly, research-only CAPS2 approximation lab.

This process is executed ONLY by the scheduled 00:00-09:00 Europe/Amsterdam
GitHub workflow. It saves Chess.com's public reference PGNs, per-game Stockfish
metrics and research parameter candidates to a separate public Git branch.
It NEVER alters or deploys the Discord bot's scoring formula or Fair Play.

Two explicitly labelled analysis regimes are tested independently:
- depth18: Stockfish 19, production default depth 18;
- time1: Stockfish 19, research-only 1 second per searched position.
Neither regime is claimed to reproduce undocumented Chess.com settings.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import calibrate_review_accuracy as fitting
from scripts.collect_chesscom_accuracy import PublicChessComAPI, collect
from scripts.verify_chesscom_accuracy_dataset import verify

LOCAL = ZoneInfo("Europe/Amsterdam")
MODEL_VERSION = "sf19-ordinary-review-v8-depth18-or-time1-research-v1"
SCORE_VERSION = "caps2-research-v1"
SUPPORTED_MODES = ("depth18", "time1")


class DeadlineReached(Exception):
    """The nightly budget has elapsed; a partly analyzed game is not saved."""


def local_window(now):
    """Schedules delayed past 09:00 cannot start new engine work."""
    local = now.astimezone(LOCAL)
    return 0 <= local.hour < 9


def nightly_deadline(now, max_minutes=42):
    """Return an UTC cutoff strictly within this Amsterdam night."""
    local = now.astimezone(LOCAL)
    if not local_window(now):
        return None
    close = local.replace(hour=9, minute=0, second=0, microsecond=0)
    return min(now + timedelta(minutes=max_minutes), close.astimezone(timezone.utc) - timedelta(minutes=2))


def mode_for_time(now):
    """Each night tests both regimes; research results never mingle."""
    return "depth18" if now.astimezone(LOCAL).hour % 2 == 0 else "time1"


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix="." + path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def identity(game):
    value = game.get("uuid") or game.get("url")
    if not isinstance(value, str) or not value:
        raise ValueError("Reference game lacks canonical ID")
    return value.lower()


def metric_path(state, mode, game):
    digest = hashlib.sha256(game["pgn"].encode("utf-8")).hexdigest()
    return Path(state) / "metrics" / mode / (digest + ".json")


def validate_metric(saved, game, mode):
    if not isinstance(saved, dict):
        return False
    if saved.get("model_version") != MODEL_VERSION or saved.get("mode") != mode:
        return False
    if saved.get("id") != identity(game):
        return False
    if saved.get("source_sha256") != hashlib.sha256(game["pgn"].encode()).hexdigest():
        return False
    if saved.get("chesscom") != game.get("chesscom"):
        return False
    try:
        fitting.prepare(saved)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False
    return True


def extract_san(pgn):
    import chess
    import chess.pgn
    from io import StringIO
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None or game.errors:
        raise ValueError("Invalid source PGN")
    board = game.board()
    moves = []
    for move in game.mainline_moves():
        moves.append(board.san(move))
        board.push(move)
    if not moves or len(moves) > 600:
        raise ValueError("Empty or too-long PGN")
    return moves


def analyze_reference(game, mode, *, interrupt_check=None, analyze=None):
    """Use the identical full-strength review engine and save replayable metrics."""
    if mode not in SUPPORTED_MODES:
        raise ValueError("Unrecognized engine regime")
    import chess_play
    source = analyze if analyze is not None else chess_play.analyse_game_moves
    moves = extract_san(game["pgn"])
    kwargs = {"interrupt_check": interrupt_check}
    if mode == "time1":
        kwargs["analysis_time_per_position"] = 1.0
    result = source(moves, **kwargs)
    if result.get("truncated") or result.get("analysed_plies") != len(moves) or len(result.get("moves", [])) != len(moves):
        raise ValueError("Incomplete engine review; refusing metrics")
    metric = {
        "model_version": MODEL_VERSION,
        "mode": mode,
        "id": identity(game),
        "source_sha256": hashlib.sha256(game["pgn"].encode("utf-8")).hexdigest(),
        "chesscom": game["chesscom"],
        "engine": result.get("engine"),
        "analysed_plies": len(moves),
        "initial_white_win_pct": result["initial_white_win_pct"],
        "moves": [
            {key: row[key] for key in ("side", "win_loss_pct", "classification", "eval_white_cp")}
            for row in result["moves"]
        ],
    }
    # Check bounds, engine evaluation availability and scoring compatibility.
    fitting.prepare(metric)
    return metric


def stable_split(metrics):
    """Game-identity split remains fixed when new games arrive or order changes."""
    training, holdout = [], []
    for record in metrics:
        bucket = int(hashlib.sha256(record["id"].encode()).hexdigest()[:8], 16) % 4
        (holdout if bucket == 3 else training).append(fitting.prepare(record))
    return training, holdout


def fit_candidates(metrics, previous=None, *, seed=0, trials=2000, min_games=20):
    """Search only the training group. Holdout is evaluated AFTER selection."""
    if len(metrics) < min_games:
        return {
            "candidate_available": False,
            "reason": f"Need at least {min_games} fully engine-reviewed games; have {len(metrics)}.",
            "reviewed_games": len(metrics),
            "no_production_changes": True,
        }
    train, holdout = stable_split(metrics)
    if len(train) < 15 or len(holdout) < 5:
        return {
            "candidate_available": False,
            "reason": "Insufficient independent training/holdout coverage.",
            "training_games": len(train), "holdout_games": len(holdout),
            "no_production_changes": True,
        }
    rng = random.Random(seed)
    baseline = fitting.BASELINE
    best = baseline
    best_loss = fitting.mae(train, best)
    examined = 1

    def consider(candidate):
        nonlocal best, best_loss, examined
        examined += 1
        loss = fitting.mae(train, candidate)
        if loss < best_loss - 1e-10:
            best, best_loss = candidate, loss

    # First cover the existing deterministic 630 candidate grid.
    for candidate in itertools.product(
        fitting.DECAYS, fitting.POWERS, fitting.FLOORS,
        fitting.WEIGHT_MIXES, fitting.BOOK_MODES,
    ):
        consider(candidate)
    old = (previous or {}).get("settings") if isinstance(previous, dict) else None
    if (isinstance(old, list) and len(old) == 5
            and all(type(x) in (float, int) and math.isfinite(x) for x in old[:4])
            and type(old[4]) is bool):
        consider(tuple(old))

    # Fresh candidate exploration every run. Previous best gets additional
    # nearby samples. Never use holdout scores to choose a candidate.
    for _ in range(max(0, min(int(trials), 10000))):
        if rng.random() < 0.55:
            candidate = (
                round(max(0.015, min(0.14, rng.gauss(best[0], 0.006))), 6),
                round(max(-2.5, min(2.5, rng.gauss(best[1], 0.3))), 3),
                round(max(0.05, min(35.0, rng.gauss(best[2], 3.0))), 3),
                round(max(0, min(1, rng.gauss(best[3], 0.1))), 4),
                best[4] if rng.random() < 0.95 else not best[4],
            )
        else:
            candidate = (
                round(rng.uniform(0.015, 0.14), 6),
                round(rng.uniform(-2.5, 2.5), 3),
                round(rng.uniform(0.05, 35.0), 3),
                round(rng.random(), 4),
                bool(rng.getrandbits(1)),
            )
        consider(candidate)

    baseline_holdout = fitting.mae(holdout, baseline)
    candidate_holdout = fitting.mae(holdout, best)
    return {
        "candidate_available": True,
        "settings": list(best),
        "examined_candidates": examined,
        "reviewed_games": len(metrics),
        "training_games": len(train),
        "holdout_games": len(holdout),
        "baseline_training_mae": round(fitting.mae(train, baseline), 3),
        "candidate_training_mae": round(best_loss, 3),
        "baseline_holdout_mae": round(baseline_holdout, 3),
        "candidate_holdout_mae": round(candidate_holdout, 3),
        "heldout_bias": round(statistics.fmean(fitting.errors(holdout, best)), 3),
        "holdout_within_2_points": round(
            sum(abs(x) <= 2.0 for x in fitting.errors(holdout, best))
            / len(fitting.errors(holdout, best)), 3
        ),
        "heldout_improvement": round(baseline_holdout - candidate_holdout, 3),
        "research_promising": candidate_holdout <= baseline_holdout - 0.5,
        "reason": "Research result only; not an exact or deployed Chess.com formula.",
        "no_production_changes": True,
    }


def get_snapshot(state, *, player="Sharkmeister", target=100, collector=None):
    path = Path(state) / "dataset.json"
    current = read_json(path)
    if isinstance(current, list) and len(current) == target:
        verify(path)
        return current, False, None
    if collector is None:
        api = PublicChessComAPI(max_requests=35, max_attempts=2)
        incoming, report = collect(player, api, limit=target, max_archives=24)
    else:
        incoming, report = collector(player, target)
    if report.get("api_errors") or len(incoming) < target:
        # No unfounded claim of having 100 official references.
        atomic_json(Path(state) / "last_collection_report.json", report)
        if current:
            verify(path)
            return current, False, report
        return [], False, report
    # Independently validate the exact dataset before retaining it.
    draft = Path(state) / ("." + path.name + ".audit")
    atomic_json(draft, incoming)
    try:
        verify(draft)
        atomic_json(path, incoming)
    finally:
        draft.unlink(missing_ok=True)
    atomic_json(Path(state) / "collection_report.json", report)
    return incoming, True, report


def run(state, *, now=None, now_fn=None, max_minutes=42, trials=2000,
        max_games_per_run=100, mode=None, collector=None, analyze=None):
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    started = now if now is not None else now_fn()
    if started.tzinfo is None:
        raise ValueError("A timezone-aware clock is required")
    if not local_window(started):
        return {"status": "skipped_outside_window", "timezone": "Europe/Amsterdam",
                "no_production_changes": True}
    deadline = nightly_deadline(started, max_minutes=max_minutes)
    selected = mode or mode_for_time(started)
    if selected not in SUPPORTED_MODES:
        raise ValueError("Unsupported research mode")
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    snapshot, refreshed, collection_report = get_snapshot(state, collector=collector)
    if not snapshot:
        output = {"status": "no_reference_snapshot", "collection_report": collection_report,
                  "no_production_changes": True}
        atomic_json(state / "latest_report.json", output)
        return output
    if len(snapshot) > 100 or len({identity(g) for g in snapshot}) != len(snapshot):
        raise ValueError("Reference snapshot is not a unique bounded game list")

    successful = 0
    errors = []
    interrupted = False
    for game in snapshot:
        path = metric_path(state, selected, game)
        existing = read_json(path)
        if existing is not None and validate_metric(existing, game, selected):
            continue
        if successful >= max_games_per_run or now_fn() >= deadline:
            break

        def check():
            if now_fn() >= deadline or not local_window(now_fn()):
                raise DeadlineReached("Nightly research computation cutoff reached")

        try:
            check()
            metric = analyze_reference(game, selected, interrupt_check=check, analyze=analyze)
            atomic_json(path, metric)
            successful += 1
            print(f"Completed {selected} metrics for public game {successful} this run", flush=True)
        except DeadlineReached:
            interrupted = True
            break
        except Exception as exc:
            errors.append({"id": identity(game), "error": f"{type(exc).__name__}: {str(exc)[:140]}"})
            print(f"Skipping game {identity(game)}: {type(exc).__name__}: {str(exc)[:140]}", file=sys.stderr)
            if len(errors) >= 3:
                break
        finally:
            # Free native Stockfish resources between games and after cancellation.
            import chess_play
            chess_play._close_analysis_engine()

    available = []
    for game in snapshot:
        record = read_json(metric_path(state, selected, game))
        if record is not None and validate_metric(record, game, selected):
            available.append(record)

    before = read_json(state / f"best_{selected}.json", {})
    # Seed changes every hour; only candidate *training* error determines the best.
    seed = int(hashlib.sha256(
        f"{started.astimezone(LOCAL).isoformat()}:{selected}:{len(available)}".encode()
    ).hexdigest()[:16], 16)
    fitness = fit_candidates(available, previous=before, seed=seed, trials=trials)
    if fitness.get("candidate_available"):
        atomic_json(state / f"best_{selected}.json", fitness)

    report = {
        "status": "completed" if not errors else "partial_with_errors",
        "timezone": "Europe/Amsterdam",
        "started_at_utc": started.astimezone(timezone.utc).isoformat(),
        "deadline_utc": deadline.isoformat(),
        "finished_at_utc": now_fn().astimezone(timezone.utc).isoformat(),
        "mode": selected,
        "engine_profile": (
            "Stockfish 19 at 1 second per position (not verified Chess.com settings)"
            if selected == "time1" else "Stockfish 19 at fixed depth 18"
        ),
        "official_reference_games": len(snapshot),
        "cached_research_metrics": len(available),
        "new_completed_games": successful,
        "incomplete_game_interrupted": interrupted,
        "errors": errors,
        "collection_refreshed": refreshed,
        "selection": fitness,
        "no_production_changes": True,
        "no_fairplay_scans": True,
    }
    atomic_json(state / "latest_report.json", report)
    # Per-mode progress survives alternating hourly runs.
    atomic_json(state / f"latest_{selected}.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--max-minutes", type=int, default=42)
    parser.add_argument("--trials", type=int, default=2000)
    parser.add_argument("--max-games-per-run", type=int, default=100)
    args = parser.parse_args(argv)
    if not 1 <= args.max_minutes <= 48:
        parser.error("--max-minutes must be between 1 and 48")
    if not 0 <= args.trials <= 10000:
        parser.error("--trials must be between 0 and 10000")
    if not 1 <= args.max_games_per_run <= 100:
        parser.error("--max-games-per-run must be between 1 and 100")
    try:
        output = run(args.state_dir, max_minutes=args.max_minutes, trials=args.trials,
                     max_games_per_run=args.max_games_per_run)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 1 if output["status"] in ("no_reference_snapshot", "partial_with_errors") else 0
    except Exception as exc:
        print(f"Nightly CAPS2 research failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
