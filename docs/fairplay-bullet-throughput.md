# Fair Play 500-game throughput plan (draft, not deployed)

## Scope and safeguards

- The **latest up to 500 eligible rated standard live Chess.com games** are still collected and engine-screened. No analysis windows or gameplay evidence are silently omitted.
- Stockfish 19 depth **18** remains required for every rapid and blitz decision in full-depth mode.
- Bullet uses Stockfish 19 depth **12** for its full-depth-mode second pass, with the existing 24,000-node fast pass preserved. Bullet is **lower-resolution screening**, not equivalent to depth-18 verification; its reduced precision must be considered during human review. Bullet decisions are not presented as depth-18 in the owner evidence.
- The engine search still uses MultiPV=5, a root-restricted search for played moves missing from MultiPV, one search thread per worker, and search-exactness checks.
- Historical baseline/clock analysis, Maia reference selection, per-model audit and existing LOW/HIGH gates remain in place. No automatic action against players is added.
- Engine work remains distributed over the existing CPU/memory-bounded position-level worker pool. Increasing worker count beyond actual CPU capacity would reduce throughput.

## Throughput improvements

1. **Bullet depth-12 second pass**: the speedup applies to bullet's deep engine searches and their bounded human-policy counterfactuals, *not* to the collection phase or first 24k-node screening pass.
2. **Batched checkpoint serialization**: completed engine positions are recorded in memory immediately and persisted at most every 64 positions or approximately 15 seconds of activity; the last batch is forcibly flushed before leaving the position queue, even after deadline interruption. This removes repeated full-snapshot encryption and local file replacement on every position. It bounds the potential crash-loss tail to the last not-yet-persisted batch. If the remote Git push fails, durable resume is not guaranteed, exactly as before.
3. **Per-time-control proof**: the owner audit reports the number of depth-12 bullet positions and the number of depth-18 non-bullet positions separately, with explicit required budgets. Checkpoint restore rejects incompatible budgets. Changing the scan-contract version intentionally invalidates cached positions from earlier deployments so incompatible evidence cannot be mixed.

## Performance: before vs after

Earlier *unverified planning estimate* for a full 500-game, all-depth-18 scan: **12–48+ hours**, potentially longer. The new full-run time **has not been measured**. It depends heavily on how many of the 500 games are bullet, the number of relevant positions, how often a played-move root search is required, and the runner's CPU.

The PR includes `scripts/benchmark_fairplay_bullet.py` and the CI job `bullet-depth-benchmark`, which will measure *real* Stockfish 19 depth-12 versus depth-18 searches (including the played-move root search) on four **synthetic chess positions**. These are engine-only microbenchmarks, not evidence of full-game or full-account wall time. A measured full-case A/B comparison would be required before promising 1–2 hours.

For a scan dominated by rapid/blitz, depth-12 bullet routing yields little direct search savings. For a bullet-heavy scan, it can materially reduce the second-pass engine cost, though the unchanged 24k fast pass and Maia will still take time. Checkpoint batching benefits all time controls.

## Validation and deployment

- Run synthetic 500-game regression proving 500 primary games and per-class depth contracts.
- Validate depth-matched encrypted checkpoint restore, all positions surviving a synthetic worker restart, and O(N/64) rather than O(N) checkpoint serialization.
- Run repository offline tests, engine smoke, and real Stockfish microbenchmark CI.
- **Draft only. Do not merge/deploy until CI and independent performance benchmarks are reviewed.**
