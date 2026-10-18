# v55 execution efficiency

The review classifier, thresholds, depth targets and model are unchanged. Keep
the v55 evidence/checkpoint namespace: a scheduling improvement must not erase
valid work or trigger version-based rescans of identical analysis.

Three execution changes remove avoidable work:

1. Bind and validate engine/model/config checkpoints before remote dispatch.
   Dispatch only games that still lack complete verified depth coverage. Partial
   games retain full-game remote review; invalid cached positions are rescanned.
2. Replace the barrier between 128-position batches with a rolling queue of at
   most 128 futures. A free worker can start subsequent positions while an
   earlier slow search finishes. Depth 12 Bullet / depth 18 Blitz and Rapid,
   MultiPV 3, engine hash resets, engine concurrency and completion checks remain
   unchanged. Failed futures do not count as evidence. Other finished positions
   are drained and checkpointed when a worker fails, so recovery does not repeat
   successful work from the same pending queue. Checkpoint writes remain
   batched; cancellation is checked while waiting and before refilling.
3. Instantiate the local Maia worker only if validated, pinned policies are
   missing. A fully cached reference does not need a new neural process. One
   missing policy still requires inference and complete validation.

`v51_execution` records restored positions, remote-requested games and new local
positions. These counters contain no identity, board, report or validation label.

## Reproducible measurements

Run `python scripts/benchmark_fairplay_queue.py --real-engine` with the pinned
Stockfish available. Both schedules use the same synthetic positions and cold
exact engine searches. The native comparison alternates schedule order and
asserts equality of every decision metric.

An October 18 local run measured:

| Fixture | Batched | Rolling | Reduction |
| --- | ---: | ---: | ---: |
| Synthetic skew, 384 tasks, 4 workers, queue 128 | 0.453 s | 0.217 s | 52.2% |
| Real Stockfish 19, 12 synthetic positions, 2 workers, queue 4 | 8.320 s | 5.022 s | 39.6% |

The first fixture demonstrates scheduling stalls, not engine throughput. The
second is small and deliberately mixed in cost; its queue size is smaller than
production. Neither percentage is a measured full-account speedup. Account
runtime still depends on positions, remote capacity, API access and cache state.

Synthetic regressions cover slow-first completion/refill, bounded pending work,
exact per-class budgets, failure/cancellation, fully/partially restored dispatch,
invalid cached depth, and full/partial Maia cache reuse. The native benchmark
confirms unchanged evidence. These checks establish execution compatibility,
not improved misconduct detection accuracy.
