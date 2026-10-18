# Bounded recent-game review

For the separately labelled, owner-configured v55 worklist overlay, see
[`fairplay-v55-consistency-triage.md`](fairplay-v55-consistency-triage.md).
It can promote manual-review priority while leaving this gameplay assessment
unchanged. Historical v54 behavior below remains the gameplay reference.

`fairplay-v54-recent-200-maia` uses the existing Discord process, queue,
encrypted compute workers, exact search verification and persistent views.
It replaces the default v52 500-game depth-only review. The legacy v50
pipeline remains available through the existing `FAIRPLAY_V51=0` rollback.

## Collection and compute

- Collect at most the latest **200 completed rated standard live games**,
  ordered by completion time. Bullet, Blitz and Rapid count toward the cap;
  unrated games, variants and Daily games do not consume it. The legacy
  mixed-class collector keeps its default behavior for other callers.
- Bound collection itself to 200, including when legacy v21 environment
  switches are present. Previously it could fetch up to 1000 games before
  truncating to the 500-game engine sample.
- Preserve complete exact Stockfish depth 12 for Bullet and depth 18 for
  Blitz/Rapid, MultiPV 3, including misses. No sparse history probes, weaker
  search budget, hidden early stop or approximate engine cache substitution.
- Run the real checksum-pinned local **Maia-3 5M** on every player move in
  games with published Chess.com Accuracy **strictly greater than 80**.
  Additionally select up to ten chronological comparison games per time
  class. Reserve comparison coverage for missing published scores as well
  as lower scores. These comparisons are not labelled clean games.
- Dispatch remote Stockfish work before local model inference, so they can
  overlap. Preserve this scan's policies by exact move/FEN identity when
  remote results replace decision objects. No extra alternative-root searches
  are added by this model stage.
- Count verified policies after remote merge, not just prior inference calls.
  Progress/footer and owner-enabled rescans use the actual active detector
  version. The depth-only ETA uses measured deep throughput without waiting
  for a nonexistent fast phase; the progress bar includes only planned work.
- Provision Maia once at worker startup; never install/download during a
  review. Model failure leaves Stockfish operational and is shown explicitly.
  Cancellation is checked between inference batches and engine tasks.

The 500-to-200 cap reduces the maximum game count by **60%**, not necessarily
wall-clock time by 60%. Game lengths, time classes, worker availability and
model overhead matter. No fixed runtime or total speedup is claimed.

## Priority and recent periods

Keep the broad v52 gameplay routes and their thresholds. Add a separate
baseline-to-recent gameplay comparison using the existing relative-quality
profile without mutating legacy metrics. This profile caps raw quality
separation at feasible remaining headroom; its output is a heuristic index,
not a probability or an empirically calibrated Elo norm.

Compare predefined latest 10/20/50-game tails against at least twenty earlier
same-class, control- and rating-comparable games. All games remain in
chronology, including misses and games without difficult opportunities.
Require complete required engine/archive coverage, unique games, large
robust quality and rating-adjusted excess shifts, sample-protected hit-rate
separation, adequate difficult opportunities and contributors in both recent
halves. Overlapping tails are alternative views, not separate replications.

Bullet uses only 20/50-game tails, at least 60 difficult opportunities, at
least max(8, 60% of the tail) contributor games, a .85 hit lower bound and .40
information floor. It cannot borrow Rapid/Blitz games or their clock data.
This is a distinct matched-change route; it does not need two abnormal
30-game blocks. The route caps at HIGH, never VERY HIGH.

Published Accuracy provides compute allocation and a separate descriptive
10/20/50-game comparison, **never a priority threshold**. Require 80% recent
published-score coverage and twenty published comparable earlier scores
before reporting a median shift. Missing scores are unknown, not zero or
evidence of ordinary play. No calibrated universal Accuracy-by-Elo/Bullet
reference is claimed. A high score, new account or rare model move alone
does not imply HIGH.

Maia observations include unexpected weak moves and unexpected strong moves
separately, excluding book/forced/easy conversions from strong-play evidence.
The selected sample is biased toward high published Accuracy and its rates
must not be treated as population evidence or a standalone priority route.
Live-trained model uncertainty is greater for Bullet. Unsearched human
alternatives are not assumed bad. Ban/closure metadata is never read by the
analytical code; there are no username-specific rules or punishment actions.

## Validation

`test_fairplay_v54.py` covers the actual collection cap and class quota,
official-score validation, complete high-score selection, deterministic
comparison coverage, missing-score allocation, rare weak moves, model
failure, recent-tail non-dilution, context separation, Bullet change guards,
partial required coverage, unchanged legacy metrics and status invariance.
The existing Fair Play, Discord routing/idle and regression suites remain
required. Live case diagnostics stay local/private and are not fixtures or
threshold-training labels. Synthetic route tests verify logic, not real-world
sensitivity, specificity or misconduct certainty.
