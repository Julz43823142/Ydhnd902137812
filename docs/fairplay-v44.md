# Fair Play v44 — Resume evidence and policy audit

## Resume correctness

Objective checkpoints previously accepted a missing `exact` flag as if exact
scores had been certified. Such metrics are no longer recorded or restored.
Malformed metric/contract containers, inconsistent searches and non-integer
achieved depths are rejected without changing the destination decision.
Rejection means recompute; it is neither favorable nor adverse player evidence.

Maia counterfactual checkpoints must contain a completed exact contract, a
positive integer budget, matching achieved depth/node metadata and finite
non-boolean numeric scores. A declared depth-18 contract with stored depth 17
can no longer skip the needed root search. Legitimate empty score maps still
represent completed no-alternative work. Valid encrypted checkpoints retain
normal reuse, cancellation and batching protections.

The existing detector-version/config binding remains in effect: changed
versions invalidate incompatible checkpoints. The first resumed scan after
an update may therefore require fresh work. No private checkpoint data is
migrated into public files.

## One policy validation per diagnostic decision

The Maia evidence funnel previously validated legal-move alignment and then
called a policy calculation that repeated the same validation. `inspect_policy`
returns alignment and analytical evidence after one validation. The public
`policy_evidence` wrapper continues to validate every input; nothing is cached
across changing model/engine stages. A valid policy with invalid engine scores
still differs diagnostically from an invalid policy.

Non-string FEN payloads now fail policy validation cleanly. Daily cards disclose
rated, unrated and unknown-status context counts, and identify rated deep
coverage explicitly. Only rated Daily gameplay contributes to review priority.
Historical reports with absent new counters display Unknown, not invented data.

## Performance and private replay

Before/after/after/before local scoring benchmarks retained exactly identical
complete analytical results for two private snapshots:

| Snapshot | v43 median scoring | v44 median scoring | Reduction |
| --- | --- | --- | --- |
| Supported positive | 5.007 s | 4.533 s | 9.5% |
| Unresolved case | 2.446 s | 2.157 s | 11.8% |

These measure scoring/post-processing, **not a full engine scan**. Engine nodes,
depths, required move coverage, candidate windows and thresholds are unchanged.
A separately reannotated normal-control snapshot remained LOW. The unresolved
44-rated-Daily sample still contains 64 hard opportunities without a supported
historical change; no target-specific HIGH adjustment was added.

Synthetic regressions cover incomplete objective and root checkpoints,
non-mutation on rejection, valid root reuse, distinct model/engine uncertainty,
one policy validation per audit decision and explicit Daily context scope.
