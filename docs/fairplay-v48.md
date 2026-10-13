# Fair Play v48: throughput, progress, and mobile copy

This release preserves sample selection, engine budgets (Stockfish depth 18,
Bullet depth 12), Maia checkpoint and inference settings, and all scoring rules.
No account status or validation labels enter the detector.

## Compute pipeline

The existing eighteen isolated Actions workers still perform objective depth
analysis while the controller runs Maia. Large deep counterfactual workloads
(now at least 50 required alternative roots) get a second encrypted worker wave.
Workers perform only Stockfish searches; the controller keeps all inference,
validation, chronology, scoring and Discord presentation. Smaller workloads stay
local to avoid dispatch overhead. A failed or missing packet is recomputed locally.

Compute is partitioned into small decision blocks rather than entire games.
Blocks retain their original FENs, player side, budgets, fast evidence and
metadata. Derived per-game metrics are reconstructed over the **complete** game
centrally. A missing block never certifies a partially reviewed game. Exact
identical objective decision queries across workers reuse one checked result;
clocks, ratings, history, fast evidence and Maia probabilities remain original.

The request manifest and per-worker slices are published in one encrypted Git
CAS update. Only nonempty workers are dispatched. Replies contain metrics and an
input digest rather than redundant original histories. Existing full-packet
transport remains readable. Immutable code SHA, engine version, all objective
contracts and alternative roots/budgets are validated before reuse. No worker
may classify a case. Requests are frozen before asynchronous inference changes
controller state.

## Local optimizations

* Position single-flight reservations happen **before** borrowing an engine, so
  concurrent duplicates cannot exhaust engine slots waiting on each other.
  Deadline/cancellation and failed-query cleanup release reservations.
* Bounded exact-query cache grows to 4,096 entries; game cache holds at most
  512 games / 40,000 decisions for its existing three-hour lifetime.
* Complete cached deep games are not dispatched again; all-cached scans avoid
  even empty worker jobs.
* Maia reuses board tokens/masks and deduplicates repeated FEN strings in IPC
  batches. Every history frame and rating is preserved; neural batches remain
  32 and inference remains deterministic with one CPU thread.
* Legal-move sets are cached by full FEN. Every probability distribution still
  receives structural/numeric validation.
* Policy game summaries are memoized only inside the final, mutation-free
  scoring scope, separated by fast/deep stage and holding the source object.
* Historical ten-game discovery indexes exact-control populations first;
  collection counts retained nonbullet games incrementally.
* Redundant copies before replacing a game with a verified remote game are
  removed. Defensive copies across mutable stages remain.
* Archive body reuse requires fresh HTTP ETag/304 revalidation. This volatile
  cache is bounded to 64 responses / 32 MB; profiles/status are never cached.
* Exchange refs are fetched only if their remote SHA changed. Completed packets
  were already decoded once; that protection remains.
* Worker status is published by one coalescing background writer; it is drained
  before the mandatory terminal receipt. Advisory failures never certify work.
* Atomic encrypted checkpoints now compress JSON before encryption, reducing
  disk/network volume. Old encrypted JSON remains readable; decompression is
  bounded. Durability/CAS and final handoff flushing are retained.

## Progress and mobile UX

The progress bar uses actual planned work and observed phase rates when known;
its conservative fallback assigns more space to deep search and counterfactuals.
Discord progress remains monotonic and reaches 100 only at completion. Maia
inference and fast/deep alternatives have separate counters and estimates.
Remote ETA uses the slowest unfinished calibrated worker, rather than the
aggregate average alone. Unknown/stalled throughput remains explicitly uncertain.
Plans/rates survive encrypted checkpoint handoff. Estimates are not guarantees.

**Copy Report** replies ephemerally with ordinary text from the published card,
including version, disclaimer, scan time and Review ID. Long reports are split
without losing text. There is no attachment, download, public repost or claim
that the bot has written to the device clipboard. It works after service restart.

## Measurement and deliberate limits

A private 44.14 MB representative payload compressed at gzip level 1 in 0.202 s
versus 0.649 s at level 6, but grew from 4.76 MB to 6.74 MB. Compute-packet
compression therefore stays at level 6; subsecond CPU savings do not justify
41% larger downloads. Previously uncompressed checkpoints use fast level 1.

Private cached/uncached scoring ABBA replays retained identical priority and
all diagnostics. Scoring medians were 4.281 -> 3.414 s and 2.725 -> 2.473 s
(20.3% and 9.2% shorter **postprocessing**, not whole production reviews).
Synthetic real Stockfish tests compare every move/game metric with complete
cold searches and chunked/deduplicated depth-18 searches. Real Maia CI compares
cached and uncached board preparation, including Black and rating conditioning.

No production-wide percentage is established by these microbenchmarks.
Runner queueing, position difficulty and network availability still affect time.
No larger neural batches/extra CPU threads, lower depths, reduced coverage,
changed thresholds or early verdict exits are enabled. Full work stealing or
incremental checkpoint journaling is not introduced: small balanced blocks and
compressed atomic checkpoints address those overheads without changing the
transaction/recovery protocol. Actual phase timings should guide any follow-up.
