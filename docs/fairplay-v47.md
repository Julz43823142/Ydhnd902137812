# Fair Play v47 — Remove duplicate policy roots and unnecessary worker files

## Objective work reuse

Maia alternatives previously bypassed the existing exact root cache. Both
serial and bounded parallel policy scheduling now attach that same cache to
the exclusive engine worker. A restricted root can be reused across policy
comparisons and played-move analysis only when engine identity, complete FEN,
move and full search limit match. Candidate searches remain a separate cache.

Cache hits additionally certify exact/completed status, correct engine and
budget, MultiPV 1, requested root PV, valid score and achieved depth. Invalid
entries recompute. Fresh malformed roots are never admitted. Policies, board
history, probability mass, alternatives selected and per-player metrics are
recomputed; no learned probabilities are cached by this change. Node budgets,
depths, sample selection, model weights and scoring thresholds are unchanged.
Disabled caching, deadline checks, retry handling and bounded queues remain.
Counters distinguish actual played-root reuse from policy-root reuse; timing
cards do not convert hit counts into a total speedup percentage.

Real Stockfish 19 synthetic tests compared eight root queries at fast nodes and
depth 18. Four repeated queries were reused; complete scores and search contracts
matched cold searches. Local times: 1.852 s cold versus 0.971 s reused (47.6%
less time **for this duplicate-query workload**, not a production/full-review
speedup). Sharing a policy root back into played-move analysis also retained
its objective score.

Two private snapshot inventories had 3,087/2,551 policy root entries with
85/63 repeated queries and 13/14 overlaps with played roots. These are possible
reuse counts, not time savings: runner isolation, cache eviction, different
budgets and root cost affect real benefit. No case names or labels enter code.

## Compute-only checkout

Only the isolated Stockfish workflow gains sparse checkout. It retains every
root Python module, scripts, offline Fair Play assets and requirements at the
coordinator's exact SHA. The main bot keeps its complete historical checkout.
Economy ledgers, pet state, unrelated graphics and market receipts are not
needed by the compute process and no longer populate its working tree.

The inspected baseline had 13,899 tracked files / 88.94 MiB versus 226 selected
files / 5.77 MiB: 93.5% fewer uncompressed working-tree bytes. This is not a
measured network/scan-time percentage; Git metadata, compression, dependency
setup and engine work still cost time. Filtered shallow fetch and credentials
for the encrypted Git exchange remain unchanged.

A real local filtered/sparse Git test checks excluded blobs stay promised,
required code/reference files appear, the immutable HEAD stays pinned, and
writing/reading/removing encrypted artifacts still works. CI additionally
compares actual Stockfish game/decision metrics and serialization from the
full checkout against the compute-only source package.

No Discord path, economy mutations, publication behavior, runner concurrency,
case persistence or analytics labels are changed. Standard detector-version
binding still invalidates incompatible old checkpoints after an update.
