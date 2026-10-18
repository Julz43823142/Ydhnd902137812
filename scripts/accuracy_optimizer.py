"""Resumable reference-lab queue. Research only: no bot imports or deployment.

Extends nightly_caps2_lab/fitting instead of changing live game review. A
completed experiment is one engine budget and one training-selected formula
on the entire frozen dataset. Scheduler invocations are NOT completed runs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import itertools
import json
import math
import os
from pathlib import Path
import random
import signal
import statistics
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import calibrate_review_accuracy as fitting
from scripts.accuracy_optimizer_engine import BUDGETS, Budget, analyze_in_child, engine_identity
from scripts.nightly_caps2_lab import atomic_json, extract_san, read_json
from scripts.verify_chesscom_accuracy_dataset import verify

SCHEMA = "chesscom-accuracy-optimizer-v1"
FORMULA_VERSION = "ordinary-review-parameter-family-v1"
MAX_STATE_BYTES = 64 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def source_version():
    files = ("chess_play.py", "ordinary_review_opening.py",
             "scripts/calibrate_review_accuracy.py", "scripts/accuracy_optimizer.py",
             "scripts/accuracy_optimizer_engine.py")
    return digest({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files})


def game_id(game):
    # The verifier checks duplicates by UUID AND URL. Use the physical URL for
    # splitting so both player scores always remain in the same partition.
    return game["url"].rstrip("/").casefold()


def partition(game):
    return "validation" if int(digest(game_id(game))[:8], 16) % 4 == 3 else "training"


@contextmanager
def exclusive(state):
    Path(state).mkdir(parents=True, exist_ok=True)
    with (Path(state) / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another optimizer owns this state; no work started") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load_dataset(path):
    count = verify(path)
    games = read_json(path)
    if not 1 <= count <= 500:
        raise ValueError("Reference dataset must contain 1..500 verified games")
    return sorted(games, key=game_id)


def initialize(state, dataset):
    games = load_dataset(dataset)
    with exclusive(state):
        existing = read_json(Path(state) / "dataset.json")
        if existing is not None and digest(existing) != digest(games):
            raise ValueError("Dataset is frozen; use a new state directory for a new dataset")
        atomic_json(Path(state) / "dataset.json", games)
        if not (Path(state) / "status.json").exists():
            write_status(state, None, "ready_for_isolated_worker")
    return {"reference_games": len(games), "dataset_sha256": digest(games)}


def statistics_for(records, settings=None):
    """Each game contributes both sides; state the actual denominators."""
    residuals = []
    both = {limit: 0 for limit in (1, 2, 5)}
    for record in records:
        predicted = (record["production_scores"] if settings is None
                     else fitting.score(fitting.prepare(record), settings))
        differences = [predicted[side] - record["chesscom"][side] for side in ("white", "black")]
        residuals.extend(differences)
        for limit in both:
            both[limit] += int(all(abs(d) <= limit for d in differences))
    absolute = [abs(d) for d in residuals]
    return {"games": len(records), "player_scores": len(residuals),
            "mae": statistics.fmean(absolute) if absolute else None,
            "bias": statistics.fmean(residuals) if residuals else None,
            "median_absolute_error": statistics.median(absolute) if absolute else None,
            "within_points_fraction": {str(n): sum(x <= n for x in absolute) / len(absolute)
                                       if absolute else None for n in both},
            "both_players_within_points_fraction": {str(n): both[n] / len(records)
                                                   if records else None for n in both}}


def measurements(records, settings=None):
    return {"overall": statistics_for(records, settings),
            "by_time_class": {kind: statistics_for([r for r in records if r["time_class"] == kind], settings)
                              for kind in ("bullet", "blitz", "rapid", "daily", "unknown")}}


def valid_metric(record, game, contract, budget):
    if not isinstance(record, dict):
        return False
    try:
        stored = dict(record)
        checksum = stored.pop("checksum")
        if checksum != digest(stored):
            return False
        if (record["contract"] != contract or record["budget"] != asdict(budget)
                or record["source_sha256"] != digest(game)
                or record["id"] != game_id(game)
                or record["analysed_plies"] != game["plies"]
                or len(record["moves"]) != game["plies"]):
            return False
        if record["chesscom"] != game["chesscom"]:
            return False
        initial = record["initial_white_win_pct"]
        if type(initial) not in (int, float) or not math.isfinite(initial) or not 0 <= initial <= 100:
            return False
        for i, row in enumerate(record["moves"]):
            if row["side"] != ("white" if i % 2 == 0 else "black"):
                return False
            cp = row["eval_white_cp"]
            if type(cp) not in (int, float) or not math.isfinite(cp):
                return False
        for value in record["production_scores"].values():
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 100:
                return False
        if set(record["production_scores"]) != {"white", "black"}:
            return False
        if budget.kind == "depth" and record["search"]["minimum_depth"] < budget.value:
            return False
        fitting.prepare(record)
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def make_metric(game, budget, contract, result, elapsed):
    if (result.get("truncated") or result.get("analysed_plies") != game["plies"]
            or len(result.get("moves", [])) != game["plies"]
            or result.get("engine_binary_sha256") != contract["engine"]["binary_sha256"]
            or result.get("research_search", {}).get("budget") != asdict(budget)):
        raise ValueError("Incomplete or mismatched isolated engine result")
    record = {"id": game_id(game), "contract": contract, "budget": asdict(budget),
              "source_sha256": digest(game), "chesscom": game["chesscom"],
              "partition": partition(game), "time_class": game.get("time_class", "unknown"),
              "engine_name": result.get("engine"), "analysed_plies": result["analysed_plies"],
              "initial_white_win_pct": result["initial_white_win_pct"],
              "moves": [{k: row[k] for k in ("side", "win_loss_pct", "classification", "eval_white_cp")}
                        for row in result["moves"]],
              "production_scores": {side: result[side]["accuracy"] for side in ("white", "black")},
              "search": result["research_search"], "elapsed_seconds": elapsed}
    record["checksum"] = digest(record)
    if not valid_metric(record, game, contract, budget):
        raise ValueError("Invalid completed metric; not checkpointed")
    return record


def candidates(seed, trials):
    # Include the existing formula's parameter representation; only training
    # MAE chooses a candidate. Official targets never enter move scoring.
    yield fitting.BASELINE
    yield from itertools.product(fitting.DECAYS, fitting.POWERS, fitting.FLOORS,
                                fitting.WEIGHT_MIXES, fitting.BOOK_MODES)
    rng = random.Random(seed)
    for _ in range(trials):
        yield (rng.uniform(.015, .14), rng.uniform(-2, 2), rng.uniform(.1, 30),
               rng.random(), bool(rng.getrandbits(1)))


def fit(records, seed, trials, deadline):
    train = [r for r in records if r["partition"] == "training"]
    validation = [r for r in records if r["partition"] == "validation"]
    if len(train) < 15 or len(validation) < 5:
        return None
    prepared = [fitting.prepare(r) for r in train]
    best, best_loss, history = None, math.inf, []
    for settings in candidates(seed, trials):
        if time.monotonic() >= deadline:
            raise TimeoutError("Formula search checkpoint deferred")
        loss = fitting.mae(prepared, settings)
        history.append({"settings": list(settings), "training_mae": loss})
        if loss < best_loss:
            best, best_loss = settings, loss
    return {"settings": list(best), "training": measurements(train, best),
            "validation": measurements(validation, best),
            "same_budget_production": measurements(validation),
            "candidate_trials": history,
            "selection": "training MAE only; validation is not used for selection",
            "validation_limit": "Repeated research reports are not a fresh final holdout. Validate on new games before deployment."}


def metric_path(root, budget, game):
    return root / "metrics" / budget.key / (digest(game_id(game)) + ".json")


def experiment_key(contract, budget, round_number, trials):
    return digest({"contract": contract, "budget": asdict(budget),
                   "round": round_number, "random_trials": trials})


def completed_experiments(root):
    return sorted([read_json(path) for path in (root / "experiments").glob("*.json")],
                  key=lambda r: (r["finished_at_utc"], r["id"]))


def write_status(state, root, phase, *, active=None, error=None):
    records = completed_experiments(root) if root is not None else []
    history = []
    for directory in sorted((Path(state) / "contracts").glob("*")):
        if directory.is_dir():
            history.extend(completed_experiments(directory))
    history.sort(key=lambda r: (r["finished_at_utc"], r["id"]))
    baseline = read_json(root / "production_baseline.json") if root is not None else None
    best = min(records, key=lambda r: (r["training"]["overall"]["mae"], r["id"])) if records else None
    # Never choose an engine profile using repeatedly inspected validation MAE.
    candidate = best["validation"] if best else None
    baseline_mae = baseline["overall"]["mae"] if baseline else None
    candidate_mae = candidate["overall"]["mae"] if candidate else None
    value = {"schema": SCHEMA, "updated_at_utc": timestamp(), "state": phase,
             "data_contract": read_json(root / "contract.json") if root is not None else None,
             "completed_experiments": len(records), "active_experiment": active,
             "total_completed_experiments": len(history),
             "comparison_scope": "Best/baseline use only the current identical dataset/source/engine contract",
             "current_production_formula": baseline,
             "best_training_selected_formula": ({"experiment_id": best["id"],
                 "budget": best["budget"], "settings": best["settings"],
                 "formula_version": FORMULA_VERSION, "validation": candidate} if best else None),
             "validation_improvement_points": (baseline_mae - candidate_mae
                 if baseline_mae is not None and candidate_mae is not None else None),
             "context": "Official Chess.com reference targets; research only, no production changes",
             "run_manifest": "runs.json", "error": error,
             "freshness": "Snapshot at updated_at_utc; running may be stale after a crash. Check workflow heartbeat.",
             "no_production_changes": True, "no_fairplay_scans": True}
    atomic_json(Path(state) / "status.json", value)
    # This manifest is aggregate-only and safe for the AI to inspect. It does
    # NOT contain player names, PGNs, per-game URLs, FENs or official raw data.
    public = [{k: r[k] for k in ("id", "budget", "settings", "formula_version", "finished_at_utc",
                                "training", "validation", "elapsed_seconds", "elapsed_scope", "engine_seconds", "engine_names", "cost")}
              for r in history]
    for sequence, (item, record) in enumerate(zip(public, history), start=1):
        item["sequence"] = sequence
        item["engine"] = record["contract"]["engine"]
        item["source_sha256"] = record["contract"]["source_sha256"]
        item["dataset_sha256"] = record["contract"]["dataset_sha256"]
    atomic_json(Path(state) / "runs.json", public)
    return value


def run(state, *, authorized=False, isolated=False, minutes=20, trials=2000,
        profiles=BUDGETS, analyze=None, engine=None, max_game_seconds=900):
    if (not .01 <= minutes <= 48 or type(trials) is not int or not 0 <= trials <= 2000
            or not 1 <= max_game_seconds <= 2700 or not profiles):
        raise ValueError("Invalid bounded research configuration")
    if len({b.key for b in profiles}) != len(profiles):
        raise ValueError("Duplicate engine profile")
    began, state = time.monotonic(), Path(state)
    deadline = began + minutes * 60
    with exclusive(state):
        if not authorized or not isolated:
            return write_status(state, None, "blocked",
                error="Reference authorization and isolated compute must both be explicitly configured")
        if not (state / "dataset.json").exists():
            return write_status(state, None, "waiting_for_reference_dataset")
        games = load_dataset(state / "dataset.json")
        contract = {"schema": SCHEMA, "dataset_sha256": digest(games),
                    "source_sha256": source_version(), "engine": engine or engine_identity(),
                    "formula_version": FORMULA_VERSION}
        root = state / "contracts" / digest(contract)
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(root / "contract.json", contract)
        queue = read_json(root / "queue.json", {"round": 0, "failures": {}})
        records = completed_experiments(root)
        round_number = queue["round"]
        for budget in profiles:
            experiment_id = experiment_key(contract, budget, round_number, trials)
            if any(r["id"] == experiment_id for r in records):
                continue
            experiment_start = time.monotonic()
            cache, failed = [], False
            for game in games:
                path = metric_path(root, budget, game)
                try:
                    record = read_json(path)
                except (OSError, ValueError):
                    record = None  # corrupt cache is recomputed, never certified
                if valid_metric(record, game, contract, budget):
                    cache.append(record)
                    continue
                failure_key = budget.key + ":" + digest(game_id(game))
                if queue["failures"].get(failure_key, 0) >= 3:
                    failed = True
                    continue
                active = {"id": experiment_id, "budget": asdict(budget), "completed_games": len(cache),
                          "total_games": len(games), "phase": "engine"}
                write_status(state, root, "running", active=active)
                remaining = min(max_game_seconds, deadline - time.monotonic())
                if remaining <= 0:
                    return write_status(state, root, "paused_budget", active=active)
                try:
                    start = time.monotonic()
                    result = (analyze or analyze_in_child)(extract_san(game["pgn"]), budget, remaining)
                    record = make_metric(game, budget, contract, result, time.monotonic() - start)
                    atomic_json(path, record)
                    queue["failures"].pop(failure_key, None)
                    cache.append(record)
                except TimeoutError:
                    # Native timeout may require a larger per-game allowance;
                    # don't retry the same too-small allowance forever.
                    if time.monotonic() < deadline - 1:
                        queue["failures"][failure_key] = queue["failures"].get(failure_key, 0) + 1
                        atomic_json(root / "queue.json", queue)
                    return write_status(state, root, "paused_budget", active=active)
                except Exception as exc:
                    queue["failures"][failure_key] = queue["failures"].get(failure_key, 0) + 1
                    atomic_json(root / "queue.json", queue)
                    # Exception messages can contain PGNs/usernames. Publish
                    # only the class; retain no raw case material in logs.
                    atomic_json(root / "last_error.json", {"type": type(exc).__name__, "at": timestamp()})
                    failed = True
                atomic_json(root / "queue.json", queue)
            if failed or len(cache) != len(games):
                continue  # a partial panel is NEVER a completed experiment
            write_status(state, root, "running", active={"id": experiment_id,
                "budget": asdict(budget), "completed_games": len(cache),
                "total_games": len(games), "phase": "formula_search"})
            validation = [r for r in cache if r["partition"] == "validation"]
            if budget == Budget("depth", 18) and validation:
                atomic_json(root / "production_baseline.json",
                            {**measurements(validation), "evaluation_partition": "validation"})
            if time.monotonic() >= deadline:
                return write_status(state, root, "paused_budget")
            try:
                fitness = fit(cache, int(experiment_id[:16], 16), trials, deadline)
            except TimeoutError:
                return write_status(state, root, "paused_budget")
            if fitness is None:
                return write_status(state, root, "insufficient_training_validation_games")
            report = {**fitness, "id": experiment_id, "budget": asdict(budget),
                      "contract": contract, "formula_version": FORMULA_VERSION,
                      "formula_search_seed": int(experiment_id[:16], 16), "random_trials": trials,
                      "finished_at_utc": timestamp(), "elapsed_seconds": time.monotonic() - experiment_start,
                      "elapsed_scope": "Completion invocation only; engine_seconds includes prior/cached complete games",
                      "engine_names": sorted({r["engine_name"] for r in cache}),
                      "engine_seconds": sum(r["elapsed_seconds"] for r in cache),
                      "cost": {"currency_amount": None, "reason": "Account billing unavailable; no free-cost claim",
                               "engine_seconds_including_reused_metrics": sum(r["elapsed_seconds"] for r in cache)}}
            # Immutable result IDs; flock + atomic rename avoid duplicated runs
            # after a restart. An interrupted fitting pass simply repeats.
            atomic_json(root / "experiments" / (experiment_id + ".json"), report)
            records.append(report)
            write_status(state, root, "running")
            if time.monotonic() >= deadline:
                return write_status(state, root, "paused_budget")
        if all(any(r["id"] == experiment_key(contract, b, round_number, trials)
                   for r in records) for b in profiles):
            queue["round"] += 1
        atomic_json(root / "queue.json", queue)
        return write_status(state, root, "idle" if queue["round"] > round_number else "retryable_errors")


def transport_state(state, bundle, *, restore=False):
    """Encrypted private persistence; never write PGNs into public Git state."""
    from cryptography.fernet import Fernet
    key = os.environ.get("ACCURACY_OPTIMIZER_STATE_KEY", "")
    if not key:
        raise ValueError("ACCURACY_OPTIMIZER_STATE_KEY is required")
    cipher = Fernet(key.encode())
    state, bundle = Path(state), Path(bundle)
    if restore:
        if bundle.stat().st_size > MAX_STATE_BYTES * 2:
            raise ValueError("Encrypted state exceeds bounded size")
        packed = cipher.decrypt(bundle.read_bytes())
        with zipfile.ZipFile(io.BytesIO(packed)) as archive:
            infos = archive.infolist()
            if sum(i.file_size for i in infos) > MAX_STATE_BYTES:
                raise ValueError("Expanded state exceeds bounded size")
            for info in infos:
                p = Path(info.filename)
                if p.is_absolute() or ".." in p.parts or p.suffix != ".json":
                    raise ValueError("Invalid state member")
            # Authenticate/validate every JSON before publishing anything.
            entries = [(i.filename, json.loads(archive.read(i))) for i in infos]
        if len({name for name, _ in entries}) != len(entries):
            raise ValueError("Duplicate state members")
        with exclusive(state):
            for name, content in entries:
                destination = state / name
                if any(p.is_symlink() for p in (destination, *destination.parents)):
                    raise ValueError("Symlink state not allowed")
                atomic_json(destination, content)
    else:
        with exclusive(state):
            stream = io.BytesIO()
            total = 0
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(state.rglob("*.json")):
                    if path.is_symlink():
                        raise ValueError("Symlink state not allowed")
                    data = path.read_bytes()
                    total += len(data)
                    if total > MAX_STATE_BYTES:
                        raise ValueError("State exceeds bounded size; archive old rounds before continuing")
                    archive.writestr(path.relative_to(state).as_posix(), data)
            bundle.parent.mkdir(parents=True, exist_ok=True)
            temporary = bundle.with_suffix(".tmp")
            temporary.write_bytes(cipher.encrypt(stream.getvalue()))
            temporary.replace(bundle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "run", "status", "seal", "unseal"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--minutes", type=float, default=20)
    parser.add_argument("--trials", type=int, default=2000)
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = read_json(args.state_dir / "status.json", {"state": "not_initialized", "mae": None})
        elif args.action == "init":
            if args.dataset is None:
                parser.error("init requires --dataset")
            result = initialize(args.state_dir, args.dataset)
        elif args.action in {"seal", "unseal"}:
            if args.bundle is None:
                parser.error("seal/unseal requires --bundle")
            transport_state(args.state_dir, args.bundle, restore=args.action == "unseal")
            result = {"state": "restored" if args.action == "unseal" else "encrypted"}
        else:
            result = run(args.state_dir, isolated=args.isolated, minutes=args.minutes, trials=args.trials,
                         authorized=os.environ.get("CHESSCOM_RESEARCH_WRITTEN_AUTHORIZATION") == "true")
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("state") in {"blocked", "retryable_errors"} else 0
    except Exception as exc:
        print(f"Optimizer failed: {type(exc).__name__}; no production changes", file=sys.stderr)
        return 1


if __name__ == "__main__":
    def terminate(_signum, _frame):
        # Raise through analyze_in_child's finally so a normal runner stop
        # terminates ONLY this research process group and native descendants.
        raise KeyboardInterrupt("Research worker stopped")
    signal.signal(signal.SIGTERM, terminate)
    raise SystemExit(main())
