# v76 rating charts and CI scheduling

`fairplay-v76-rating-ci` rebuilds the rating-chart change from PR #278 on current
main, which already contains v74 via squashed PR #277. Only the rating addition
is replayed; obsolete copies of v74 are not merged again. Detection is unchanged.

CI keeps every original script check and all root unittest modules:

- Six deterministic whole-module partitions replace four alphabetical partitions.
  Measured test-body durations guide the heavy modules; AST test counts provide
  fallback weights. New/malformed modules are still scheduled. No allowlist drops
  new tests. The runner prints actual module timings for later rebalancing.
- Four engine groups run independently, preserving normal/PGO engine dependencies.
  The existing `engine-smoke` status is a fail-closed aggregate: a cancelled,
  failed or skipped engine group prevents success.
- Python download/wheel caches are keyed by workload, platform, Python version and
  requirement/workflow content. Dependency installation still runs on every job.
- Stockfish 18 uses its own pinned-source AVX2 binary cache. Compilation runs on
  misses; the real version comparison checks engine identity on both cache hits
  and misses. Stockfish 19 normal and PGO caches remain separate.
- The explicit legacy regression job is retained as a separate safety check in
  this pass, despite duplicate coverage. No nightly-only checks replace PR gates.

Before: recent complete workflows took about 193–304 seconds. The measured engine
job was 296 seconds; four independent groups are estimated around 51–77 seconds
plus setup with warm caches. A cold SF18 compile adds about 43 seconds to its
group. Queue contention and runner speed matter; these are planning estimates,
not a measured speedup of the new workflow. GitHub CI results are authoritative.

Local verification: scheduler coverage/disjointness/determinism tests; chart,
UI and triage regressions; repository YAML/JSON validation; script inventory
comparison verifies all previous smoke/benchmark invocations remain present.

First complete GitHub run (40147584590): all checks passed. Engine groups took
64/65/71/157 seconds (last group includes cold SF18 compilation); unit groups
179/99/97/108/60/62 seconds. The long first group motivated a second refinement:
`scripts/ci_test_weights.json` records all 171 actual module durations, and shard0
reserves 20 seconds for auxiliary checks. The partition still discovers every
root test module dynamically; this JSON schedules work and never filters tests.
