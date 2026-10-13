# Fair Play v46 — Exact root reuse and Daily evidence diagnostics

## Engine work and correctness

Completed, consistent, exact played-move root searches can now be reused when
engine identity, full FEN, played move and complete search limit match. This
cache is distinct from unrestricted candidate searches. Candidate MultiPV and
scoring context can differ; move metrics and context are still recalculated.
Inexact, incomplete and contradictory results are never admitted. Depth, node
budgets and required move coverage are unchanged.

The whole-game fallback previously bypassed the raw position cache. Both that
path and the position queue now bind the same bounded, locked process-local
cache to the exclusive worker, with cleanup in `finally`. Disabled caching
remains disabled in both paths. Whole-metric cache keys now include all Limit
constraints, preventing depth-only and depth-plus-node requests from aliasing.

Raw searches remain volatile and isolated per runner. No account-specific
exceptions, closure features, public case files or new dependencies are added.
The existing version/config checkpoint binding invalidates incompatible older
checkpoints; the first scan after an update can require fresh work.

## Measured performance

A three-game synthetic full-depth-18 review was run before/after and then in
reverse order with a newly initialized one-worker engine pool each time. All
complete game/decision metrics, aggregates, deep coverage and priorities matched.
Official Stockfish 19 was used, at unchanged fast nodes and depth 18.

| Version | Total review median | Fast stage median | Deep stage median |
| --- | --- | --- | --- |
| v45 | 95.834 s | 4.503 s | 90.134 s |
| v46 | 68.165 s | 3.210 s | 64.909 s |

This is **28.9% less elapsed time for this synthetic workload**, not a production
speedup guarantee. Collection was a local synthetic PubAPI fixture, with no
network, remote queue or loaded Maia model. The initial reference run included
one-time opening initialization; the reverse run warmed that reference before
measurement and retained the speed advantage. Different game overlap, worker
counts, Maia work and remote latency change production results.

The public real-engine smoke now checks twelve decisions, including repeated
outside-candidate roots with different scoring contexts. Complete metrics match
fresh search. Local cold-search versus reuse time was 11.288 s versus 5.837 s
for that repeated-position workload; this is not the total review benchmark.

## Daily evidence and UI

Boolean difficulty values no longer masquerade as numeric hard positions.
Only `deep is True` certifies a deep review, including reference selection and
coverage counts. Invalid stability containers retain the opportunity but supply
no paired confirmation; they no longer crash the analysis.

Review Gates and Engine Analysis now show candidate/prior game counts, near-best
difficult decisions, multi-decision contributors and per-game quality. Scan
Timing exposes candidate/root reuse counts. Search timing is correctly described
as summed search wall time across workers, not measured CPU time or additional
review elapsed time. Reuse counts are never shown as a speedup percentage.

Private replay retained the supported live positive as HIGH and the normal
control as LOW. The unresolved correspondence sample still does not qualify:
its best recent period has 10/11 near-best hard decisions versus 29/32 in its
prior reference, and both per-game quality medians are 1.0. This is not a
supported historical rise. No label-driven threshold reduction was made.
Correspondence remains separate from live chess; timing is disabled and Maia's
live policy is descriptive, not a calibrated correspondence misconduct model.
