# v57 runtime throughput

No game quota, Stockfish depth/MultiPV, legal-move validation, Maia coverage,
priority threshold or Discord isolation changes. Existing budget remains
Bullet depth 12 and Rapid/Blitz depth 18. The deployed coordinator can dispatch
18 encrypted compute shards; the main bot and other workflows also need slots.
More jobs than the account's actual concurrency do not create extra CPUs.

Changes:

* Maia inference deduplicates identical pinned-model/history/both-rating inputs
  within one call, validates each attached distribution and retains every
  required decision. Different history or ratings cannot share a policy.
* Maia checkpoint batches update in-memory state without serializing the entire
  encrypted snapshot every 64 positions. Flush at 512 new inputs or 15 seconds,
  at normal completion and after a later inference failure. Partial references
  still cannot score. Unflushed work may need recomputation after process death.
* Legacy policy-alternative shards balance by actual selected root count as well
  as depth class. Objective Stockfish sharding remains unchanged. This improves
  the legacy route, not the default v51 coordinator's objective-only phase.
* Empty legacy counterfactual root sets complete without reserving a Stockfish
  process. Their empty exact-budget contract and position accounting remain.

Local validation:

* Synthetic checkpoint benchmark: 1,024 validated policies, identical encrypted
  resume contents. Writes 16 -> 2; median local stage time ~54 -> 18 ms over
  three runs (~67% reduction). This is not a full-review benchmark.
* Synthetic heterogeneous-root scheduling test reduces the maximum assigned
  root workload by more than half; this is not a real-run speed prediction.
* Duplicate-input test: 130 identical model inputs -> one inference, all 130
  decisions validated/populated. Different-rating inputs remain separate.
* Existing private frozen 190-game workload contained 6,195 distinct model
  inputs and zero duplicates: no inference savings are claimed for that case.

A whole-account wall-time improvement is NOT established by these measurements.
Stockfish position difficulty, runner queue/startup, encrypted transport and
fallback work can dominate. No promise of one hour becoming a fixed number of
minutes is supported without a comparable end-to-end production run. Routine
logs must not include account identities, FENs or model inputs.
