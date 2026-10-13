# Fair Play v45 — Exact candidate reuse

## Engine work

The bounded process-local position cache now also retains completed, exact,
consistent raw candidate searches. Two decisions can share this search only
when engine identity, full FEN, complete search limit and MultiPV match.
Different played moves still receive their own evaluation and contextual
metrics. A played move outside the candidates still gets its own equal-budget
root search. Maia policies, opening classification and player evidence are
never borrowed from another decision.

The same locked LRU capacity bounds both raw candidates and full metrics.
Entries are copied on read/write. Incomplete, bounded or contradictory searches
are not admitted. Disabling the position cache also disables candidate reuse.
Worker-local cache binding is removed in `finally`, including cancellation or
failure. Concurrent misses may safely compute the same search independently;
this optimization does not serialize heavy work on the Discord event loop.

Search depth, node budgets, MultiPV, games selected, scoring thresholds and
required deep coverage are unchanged. Raw candidate hit/miss counters are
available in each scanner's runtime profile. No persistent account cache or
public case ledger is added.

## Measured scope

A synthetic real-Stockfish 19 workload scored eight decisions at one repeated
position, using both the normal fast node budget and depth 18. Six candidate
searches were reused. Complete decision metrics matched fresh sequential
searches exactly. Local engine time was 7.921 seconds without reuse and 4.336
seconds with reuse, a 45.3% reduction **for this repeated-position workload**.
This is not a promised full-report speedup. Reviews with few repeated FENs
benefit less; outside-candidate played-move searches remain necessary.

CI runs the real-engine equivalence smoke. Unit regressions cover distinct
played moves, outside-candidate roots, independent opening context, separate
budgets/MultiPV/FENs, disabled caching, mutation isolation, inexact rejection
and full metric parity.

## Private replay

A previously unresolved correspondence snapshot still contains 44 rated Daily
games, all deeply reviewed, and 64 difficult opportunities across the history.
It does not satisfy the required distributed period and historical quality
change. The optimization therefore does not promote that case to HIGH.
Correspondence remains separate from live chess, timing is disabled, and
live-trained Maia is descriptive rather than a calibrated Daily scoring model.
No account-specific exception or closure-status feature was introduced.

As with earlier versions, the detector-version binding invalidates incompatible
old checkpoints. The first scan after an update can require fresh work; later
cache reuse is within the running worker process.
