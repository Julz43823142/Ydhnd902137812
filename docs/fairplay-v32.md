# Fair Play v32: signed gameplay evidence and distributed recovery

## Classification correction

The heuristic previously aggregated `max(0, observed - expected)` as
`quality_excess`. Rectification makes noisy play look above its reference even
when its actual average is below the reference. For example, qualities 1 and 0
against an expected quality of 0.7 produce a positive-only mean of 0.15 but a
signed mean of -0.2. Replicating this biased statistic in two chronological
halves does not remove the bias.

Per-decision and per-game `quality_excess` now retain the sign. The existing
uniform game caps, averaging, contributor requirements and priority thresholds
remain in place. `positive_quality_excess` preserves the former descriptive
measurement. Positive-tail hit and information measurements still locate
selective patterns; genuine short chronological high-evidence periods remain
eligible. Every route consuming the aggregate separation now receives the
signed value, including paired deep confirmation. The learned Maia policy
comparison already retained signed excess and is unchanged.

Fixed-half replication additionally rejects overlapping game IDs or overlapping
chronology, and reports both halves' fast/deep opportunity counts and signed
quality differences. These halves are temporal replication within one related
gameplay family, not independent statistical probabilities.

The detector version changes to invalidate old cached/checkpointed derived
statistics. The new calculation is not a calibrated misconduct probability.

## Compute failure and runtime correction

The coordinator selects rated and unrated games, but isolated Stockfish
workers previously rejected every shard containing an unrated game. That
validation and pool initialization happened outside failure reporting, so the
coordinator could wait for a timeout before recomputing the shard locally.

Workers now accept explicit boolean rating status while preserving it in the
returned payload. Unknown status remains invalid. Validation and pool startup
are covered by encrypted failure reporting and cleanup. Workers never assign a
priority; existing central rated/unrated scoring boundaries remain intact.

Distributed elapsed time is captured when the join finishes. Local recovery
and later policy work are no longer incorrectly included in that statistic.
Join waiting time is recorded separately. No wall-clock speedup is claimed
without a new deployment benchmark.

## Maia and runner cancellation

A failed fast Stockfish counterfactual comparison was described as an
incomplete Maia reference, hiding the phase that failed. Fast and deep
counterfactual failures now have distinct safe error codes. Full required Maia
coverage checks remain fail-closed.

A GitHub run cancelled before the planned rotation time is not evidence that
the rotation timer malfunctioned. A missing planned-rotation marker is expected
for externally cancelled runs. This change does not override deliberate
cancellation or dispatch extra Discord workers.

## Validation

Synthetic regressions cover below-reference variable strong play, signed
fast/deep aggregation, distributed extreme gameplay, a recent anomalous tail,
overlapping halves, mixed-status compute, invalid-workload failure publication,
and pool-startup recovery. Private owner evidence is replayed locally only;
no target names, account labels, game positions or evidence exports are tracked.
