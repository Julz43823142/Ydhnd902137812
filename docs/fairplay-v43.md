# Fair Play v43 — Policy contracts and Daily compute

## Evidence boundary fixes

- Boolean model probabilities and engine scores are invalid numeric evidence.
- Invalid FENs return unavailable policy evidence instead of raising out of the
  model-validation path.
- Completion/exactness flags require actual `True`; strings such as `"false"`
  and integer flags cannot certify a counterfactual search.
- Search budgets are type-checked without coercing arbitrary strings to
  integers. Missing/malformed budgets remain unknown rather than crashing.
- Achieved depth must be a non-boolean integer; malformed depth cannot pass
  provenance verification or raise during comparison.
- Malformed/null counterfactual payloads and score maps remain unavailable.

Valid numerical evidence retains its existing values and HIGH gates. Unknown
model data is neither evidence of misconduct nor evidence of fair play.
Closure metadata and validation labels remain outside production scoring.

## Less repeated work

Acute discovery and deep confirmation reuse the summary already calculated
for the same members and the same fast/deep stage. All windows, misses and
threshold checks remain; no persistent summary cache is introduced.

Daily games still receive Stockfish objective searches and descriptive Maia
predictions. The correspondence route deliberately does not score the
live-trained Maia policy. Additional Stockfish searches of Maia-favored Daily
alternatives therefore cannot contribute to that route and are omitted.
The report records `excluded_daily_positions`; stale Daily policy-root results
are cleared rather than presented as new confirmed work. Live policy roots
remain unchanged.

## Measured scope

A private 44-rated-Daily snapshot contained 266 eligible policy positions,
215 with extra roots, totaling 533 extra root searches. Those searches can now
be omitted without changing the correspondence evidence.

A deterministic two-position local benchmark using real Stockfish 19 at depth
18 performed four alternative searches before this change: 2.493 seconds.
The omitted stage took approximately 0.001 seconds afterwards (over 99.9%
reduction for that extra Daily-root phase). This is a small stage benchmark,
**not a full-account speedup percentage**. Full scan savings depend on Daily
coverage, engine complexity, runner throughput and other work. No objective
engine depth or required Maia inference is reduced.

Private before/after scoring replay retained identical complete analytical
results for one supported positive and one unresolved case. A separately
reannotated normal-control snapshot stayed LOW. The unresolved Daily sample
still lacks sufficient distributed hard opportunities and a material shift
against its own comparable historical games; v43 does not force a HIGH label.
