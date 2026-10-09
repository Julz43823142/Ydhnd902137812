# Single-PV fast and deep Stockfish analysis

SharkBot now runs Stockfish 19 NNUE with **MultiPV 1 for both fast and deep**
analysis. The fast pass remains 24,000 nodes; the deep pass retains the
existing depth schedule: bullet 12, rapid/blitz 18. A separate root-restricted
search evaluates a played move that differs from Stockfish's best line.
The scope stays at 500 eligible rated standard live games.

**Critical limitation:** the existing critical/unique detection measures
require the best-vs-second gap and spread across at least three candidate
scores. Single-PV analysis cannot supply these. Critical and unique flags
therefore remain unavailable rather than being fabricated. **HIGH and VERY
HIGH are explicitly disabled** in the single-PV evidence contract until a
validated replacement for difficulty/uniqueness is implemented and tested.
LOW/MODERATE remain descriptive screening results, not cheating verdicts.
Top-three agreement is not a meaningful top-three statistic under MultiPV 1;
it must not be interpreted as equivalent to the previous MultiPV 5 metric.

## Worker failure near 80%

The screenshot's error is a generic UCI/timeout handler; the exact underlying
cause is unknown. A worker is restarted and the affected position retried
once for transient UCI errors/timeouts. A second failure still stops safely,
with already completed encrypted checkpoints eligible for resumption.
Sanitized logs contain only failure category and restart status.

## Performance

This is not an exact reproduction of Chess.com Fast or a one-second time
limit. MultiPV 1 likely reduces engine search time, but separate played-move
searches remain. Full-account throughput and detection quality have not been
benchmarked. The config version change invalidates old checkpoint contracts.
