# v41 — coverage-aware gameplay evidence

This pass fixes evidence accounting and confirmation logic. No case identity,
validation label or external account closure status enters production scoring.
No global HIGH cutoffs, Stockfish budgets or learned-policy positive thresholds
were lowered.

## Counterfactual Maia quality bounds

Policy comparison assigned unsearched alternatives quality 1, plus a .10 model
uncertainty margin. The resulting signed quality excess is a **lower bound**.
A negative lower bound cannot establish that the actual excess is negative.
Previously the fixed-half counterevidence gate interpreted that bound as a
negative finding. Partial alternative coverage could therefore veto established
heuristic gameplay evidence.

The new calculation retains the old conservative lower bound for positive
signals. It also computes expected-quality lower bounds (unsearched quality 0,
minus the uncertainty margin) and hence a signed-excess upper bound. Game-capped
aggregation preserves both directions. Counterevidence requires the **upper**
bound to be negative, plus the existing coverage, stability and contributor
checks. Missing bounds never become negative evidence. Fully measured negative
references still veto heuristic-only HIGH. These are bounded counterfactual
model quantities, not calibrated misconduct probabilities or confidence
intervals for cheating.

## Complete recent replication

Old discontinuous deep selections must not invalidate a newer, completely
reviewed chronological tail. Alongside whole-class halves, the detector evaluates
two fixed halves of up to the newest 100 contiguous games. It stops at any
recent coverage gap and never chooses a flattering older run. Scope is explicit;
identical scopes are deduplicated and overlapping views are not independent
replication.

Complete deep review may resolve only a positive fast-pass raw-margin screening
failure. Every game in the half must be deeply reviewed, every other fast gate
must pass, and every existing deep gate (including its original quality-margin
threshold) must pass. Partial deep review, negative fast margins and any other
failed gates cannot use this exception. Learned-reference counterevidence still
applies afterwards.

## Daily coverage

Extra Daily reservation uses pre-engine meaningful-decision coverage rather than
filling slots with short/no-opportunity games. Latest-fifty guarantees and the
existing total deep capacity remain intact. Selection does not rank engine hits.

Daily opportunities use continuous difficulty, competitive state and completed
paired search, rather than additionally requiring the legacy binary critical
flag. This retains excellent #2/#3 choices with meaningful candidate geometry.
Book, forced, automatic and easy-conversion decisions remain excluded.

Preset chronological windows of 5/10/20/30 games retain misses and zero-opportunity
games. Every candidate game must be completely deep-reviewed; unreviewed games
are missing evidence, never zero-opportunity context. Reference games must also
be deep-reviewed. At least five multi-opportunity contributors and 60% of opportunity games
must contribute; the original 40-opportunity, Wilson-bound, paired-search and
prior-change requirements remain. Reference selection matches the current
correspondence-control mix by metadata-only quotas and newest-first order.
A mix mismatch greater than .10 blocks comparison. Clocks and live-trained Maia
still cannot create Daily HIGH. Continuous assistance without an adequate
historical reference remains a limitation. Public details show opportunity and
reference coverage explicitly.

## Validation scope

Tests cover both interval directions, missing bounds, valid negative
counterevidence, full versus partial deep confirmation, historical/recent
chronology gaps, deduplication, noncritical equivalent choices, mixed controls,
metadata-only reference selection and zero-opportunity reservation. Private
replays do not replace player-disjoint calibration or prove misconduct.
