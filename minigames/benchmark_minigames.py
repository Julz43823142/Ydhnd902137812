"""Repeatable offline engine-start benchmark for *all fifteen* minigames.

No Discord client, Git transaction, public API, real wallet or secret is accessed.
Run: PYTHONPATH=. python minigames/benchmark_minigames.py --samples 12

These timings exclude network, Discord image rendering and external storage.
Do not use them as proof of production p95 responsiveness.
"""
import argparse
import json
import random
import statistics
import time
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import minigames_engine as engine
import minigames_chessle as chessle
from minigames_chess_trivia import chess_questions

# Force built-in opening catalogue for this standalone benchmark. Never fetch
# remote Lichess TSVs, even during the first Chessle game.
chessle._REMOTE_LOAD_ATTEMPTED = True


def local_bank():
    # Deterministic, fully offline question bank that meets all legacy tiers.
    bank = [
        {
            "id": f"{difficulty}-{i}",
            "difficulty": difficulty,
            "category": f"Other {i%3}",
            "question": f"{difficulty} sample question {i}?",
            "answer": "Option A",
            "options": ["Option A", "Option B", "Option C", "Option D"],
        }
        for difficulty in ("easy", "medium", "hard") for i in range(20)
    ]
    return bank + chess_questions()


def run_one(kind, index, bank):
    seed = index * 73471 + sum(map(ord,kind))
    rng = random.Random(seed)
    game_id = f"benchmark-{kind}-{index}"
    n = max(1,engine.MINIMUM[kind])
    wallets = {
        str(j): {"name": f"Player {j}", "coins": 1000.0, "points": 0.0}
        for j in range(n)
    }
    state = {"games": {}, "seen": {}, "daily": {}}
    mode = {
        "chessle": "expert", "codebreaker": "hard", "math": 120
    }.get(kind)
    started = time.perf_counter_ns()
    game = engine.create(
        state, wallets, game_id, "0", "Player 0", kind, 0, 1000,
        mode=mode,
    )
    for uid in range(1, n):
        engine.action(
            state, wallets, game_id, str(uid), f"Player {uid}", "join",
            "", 1001, rng, bank,
        )
    engine.start(state, wallets, game, 1002, rng, bank)
    elapsed_ms = (time.perf_counter_ns() - started) / 1e6
    return elapsed_ms, game.get("status"), len(state.get("games", {}))


def percentile(values, p):
    sorted_values=sorted(values)
    return sorted_values[min(len(sorted_values)-1,max(0,int((len(sorted_values)-1)*p+0.5)))]


def benchmark(samples):
    rows=[]
    bank=local_bank()
    for kind in engine.KINDS:
        elapsed=[]
        failures=[]
        for seed in range(samples):
            try:
                ms, status, games = run_one(kind, seed, bank)
                elapsed.append(ms)
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
        rows.append({
            "kind": kind, "runs_ok": len(elapsed),
            "runs_failed": len(failures),
            "min_ms": round(min(elapsed), 2) if elapsed else None,
            "p50_ms": round(statistics.median(elapsed), 2) if elapsed else None,
            "p95_ms": round(percentile(elapsed, .95), 2) if elapsed else None,
            "max_ms": round(max(elapsed), 2) if elapsed else None,
            "first_error": failures[0] if failures else None,
        })
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples",type=int,default=12)
    parser.add_argument("--json",action="store_true")
    opts=parser.parse_args()
    if not 1 <= opts.samples <= 1000:
        parser.error("--samples must be between 1 and 1000")
    rows=benchmark(opts.samples)
    if opts.json:
        print(json.dumps({"scope":"pure in-memory engine create/join/start only",
                          "samples":opts.samples,"results":rows},indent=2))
    else:
        print("OFFLINE ENGINE BENCHMARK — excludes Git, images, Discord and external APIs")
        print(f"{'Game':<14} {'OK':>4} {'Fail':>4} {'p50 (ms)':>12} {'p95 (ms)':>12}")
        for row in rows:
            print(f"{row['kind']:<14} {row['runs_ok']:>4} {row['runs_failed']:>4} "
                  f"{str(row['p50_ms']):>12} {str(row['p95_ms']):>12}")
            if row["first_error"]:
                print("  Error:",row["first_error"])
    raise SystemExit(1 if any(row["runs_failed"] for row in rows) else 0)


if __name__=="__main__":
    main()
