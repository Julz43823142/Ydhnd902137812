# Fair Play v50 — selective policy comparisons

## What changes

The learned human reference already estimates whether strong observed choices
outperform Maia's human alternatives. Rarity alone remains insufficient.

### Adaptive reference coverage

The deterministic alternative planner now selects up to twelve additional legal
roots instead of three, targeting 90% human-policy mass with a 0.5% per-move
floor. Roots are chosen by model probability and existing coverage, independently
of observed move quality. Existing exact root caching and distributed workers are
reused. Every extra root keeps the same engine and requested nodes/depth. Missing
probability mass still receives optimistic perfect quality, and the existing
0.10 model-uncertainty margin is unchanged. Coverage can remain unresolved at the
cap; no missing data becomes evidence. Cached/resumed root sets must exactly
match the current plan or be recalculated.

A specified flat twenty-legal-move test has five original engine candidates. The
old three-root plan covers 40% of human probability; twelve extra roots cover
85%, crossing the unchanged 65% comparability floor. These are coverage numbers
for a synthetic position, not detector accuracy or a runtime improvement.

### Selective episodes — at most MODERATE

A whole-game mean can dilute a small assistance-like run with unrelated mistakes.
`fairplay_policy_episodes.py` compares fixed, non-overlapping six-nontrivial-decision blocks
anchored to the original nontrivial sequence. Verified opening/forced/trivial
and fast-context automatic/conversion choices contribute no engine evidence and
are excluded before selecting blocks. Missing model/search data remains a gap;
deep results cannot change the selected original indices. Incomplete decisions break a block;
misses remain in its signed quality mean. One fast-selected block per game is
checked at exactly the same indices under deep search; deep outcomes cannot
choose a new block. Each block needs three model-unusual strong choices, the
existing 0.18 signed-excess and 0.12 information floors, at least five of six
semantically stable paired decisions, three retained distinct strong choices,
and deep information retention of 75%.

A chronological Rapid or Blitz period needs six confirmed contributor games,
at least 60% contributors, two contributors in each period half, and twelve
distinct confirmed position/move pairs. Original class indices prevent gaps from
creating consecutive periods. Duplicate games do not add evidence. Required
engine/deep coverage must be complete. Daily, Bullet, and unrated games do not
enter this live-policy route. Disabling human/neural features disables it.

The route can elevate LOW/INSUFFICIENT DATA to MODERATE only. Existing HIGH or
VERY HIGH results/reasons remain intact. This is related gameplay evidence, not
an independent vote or calibrated probability. Multiple searched blocks/periods
are disclosed; no p-value or exact real-world error-rate claim is made.

### UX and diagnostics

Progress cards show `fairplay-v50-selective-policy`, including stopped cards.
Human Moves and Review Gates expose comparable/missing/low-mass reference counts
and selective-episode confirmation. Final-report copying/persistent views retain
their existing behavior. No new commands or public case ledger.

## Research and licensing

This is an original implementation using methodological ideas from:

- [Irwin](https://github.com/clarkerubber/irwin): ordered move-quality/candidate/time features.
- [Kaladin](https://github.com/lichess-org/kaladin): context-dependent behavioral comparisons; private browser-focus data is unavailable through Chess.com PubAPI and is not fabricated.
- [Maia-3](https://github.com/CSSLab/maia3): the existing rating-conditioned human move reference.
- [ChessFraud](https://github.com/artem-lepin-ml/chess-fraud): paired comparisons and explicit held-out evaluation boundaries.
- [Chess Signatures of Play](https://arxiv.org/abs/2606.18544): motivation for preserving order/interactions; its synthetic-study results are not SharkBot accuracy.

No third-party detector source/weights were copied or relabelled. This version
neither installs Kaladin/Irwin nor claims to ship a newly trained ChessFraud/Maia
misconduct classifier. Such a classifier still needs training, platform/control
calibration and independently held-out evaluation before deployment.

## Validation and limits

Tests use synthetic distributions and fake exact engine values, covering
selective runs, human-policy draws, elite expected moves, rare bad choices,
one/two exceptional games, missing searches, unstable deep results, chronology
gaps, duplicate games/positions, missing reference, and versioned progress.
`scripts/benchmark_fairplay_selective.py` compares 100 specified toy samples
across five regimes, with seeds separate from the unit fixtures. The old
comparator is only the policy contributor gate, not the full v49 detector.
Its output explicitly states real-world accuracy was not measured. Both larger
human coverage and the selective detection behavior are demonstrated only in
these specified scenarios. No private account is used to select thresholds.

More counterfactual roots may increase runtime; no global speedup is claimed.
Engine concurrency, deadline/cancellation, exact budgets and cleanup are
unchanged. Existing process-local cache and encrypted checkpoint paths are reused;
no save-data migration, new external service or model download is required.
