# Fast MultiPV 1 with reliable four-level priority

Fast screening uses Stockfish 19 with MultiPV 1 (24,000 nodes) and a separate
played-move root search where necessary. Deep confirmation uses MultiPV 5
at the existing depths (bullet 12, rapid/blitz 18) so best/second gaps,
candidate spread, critical positions and unique-best evidence remain measurable.

This restores all four existing **qualitative review priorities**:
LOW, MODERATE, HIGH, VERY HIGH. They are evidence-priority labels, **not**
calibrated probabilities of cheating. No numerical cheat probability is claimed.
Existing sample, baseline, deep-confirmation, persistence, and independent
corroboration gates still apply. MultiPV 1 fast-pass cannot measure candidate
difficulty, so fast-only signals must not be treated as multi-candidate evidence.

The existing one-retry Stockfish worker recovery remains unchanged.
Performance is not benchmarked: restoring deep MultiPV 5 sacrifices some
of the deep-pass speed improvement in return for evidence quality.
