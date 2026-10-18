# Full human-reference and bounded period evidence

`fairplay-v56-model-period-evidence` keeps the existing isolated Discord bot,
queue, encrypted compute shards, latest-200 rated live-game collection and
exact Stockfish budgets: depth 12 Bullet, depth 18 Blitz/Rapid, MultiPV 3.
It does not change SharkBot Game Review Accuracy or the legacy v50 classifier.

## Corrections

The full-depth pipeline enables `human_reachable_headroom` for scoring only.
Raw move/period excess floors are capped at 90% of the reference's remaining
quality headroom. Previously a reference of .94–.97 could require .09 extra
quality from a bounded index whose maximum is 1. Perfect decisions were
mathematically unable to qualify. Ordinary precision and tiny excess remain
insufficient; legacy replay keeps its original configuration.

Maia-3 5M now evaluates every player position in every collected rated game,
including misses and games without published Chess.com Accuracy. Model weights,
causal history, rating conditioning, local CPU inference, pin verification and
failure cleanup are unchanged. No scan-time download or remote model service.
Coverage may still be unavailable when ratings/history/model data are missing.
This costs additional inference, not additional Stockfish searches; no fixed
wall-clock speed claim is made.

## New combined engine/model route

`fairplay_model_priority.py` adds one related gameplay family, not independent
votes or misconduct odds. It preserves the broad and recent-change routes and
leaves the owner-configured worklist overlay explicitly separate.

- Candidate periods are fixed chronological blocks plus the latest endpoint
  at widths 6/10/20/50, separately for Rapid/Blitz/Bullet. All games remain in
  chronology; ranked best-game subsets are forbidden. Overlapping periods
  never accumulate evidence or count as separate replication.
- Both engine and human comparison use the **same difficult competitive
  opportunities**, including misses. Book, forced, trivial, automatic material
  gain and easy conversion protections remain. Each game contributes at most
  six uniformly selected opportunities, chosen before inspecting their values.
- The learned reference uses `fairplay_maia.policy_evidence`: objective actual
  quality minus an **upper bound** on Maia-expected alternative quality.
  Unsearched alternatives receive perfect quality; the existing .10 allowance
  accounts conservatively for model/domain uncertainty. A rare bad move or
  an equivalent good alternative is not anomalous merely for being rare.
- A joint hit requires excellent actual quality (>=.85), model excess >=.20,
  and the rating-conditioned difficult engine hit on that same decision.
  Per-game signed averages retain mistakes; positive-only tails cannot qualify.
- HIGH needs at least four contributor games and 60% of the entire period,
  at least two contributors in each chronological half, and at least
  `max(12, 2 * required_contributors)` opportunities. Each model contributor
  needs two joint hits, signed excess >=.15 and information >=.12.
- HIGH also needs a Wilson lower bound >=.65 for both reported hit fractions,
  aggregate signed model excess >=.15, model information >=.12 and the
  existing reachable rating-conditioned engine excess floor. This makes
  19/22 possible with replicated covered evidence; 60% alone is insufficient.
- Bullet starts at 20 games, >=40 opportunities, >=.80 lower bounds and
  >=.20 signed model excess. It never borrows other classes or clock data.
- MODERATE uses >=12 opportunities, three contributors, .40 lower bounds,
  .10 signed model excess and the existing engine excess floor. It is still
  a joint multi-game route, never a raw move-rarity rule.
- Complete required exact-depth and policy coverage, valid unique identities
  and no missing archive within the period are mandatory. The account minimum
  of ten useful rated games remains. Missing optional older archives do not
  invalidate an otherwise covered period. Missing model data leaves existing
  Stockfish routes available. This route never creates VERY HIGH.

Wilson limits describe the sampled hit fraction. Decisions from the same
player/game are dependent; these bounds are **not** calibrated misconduct
probabilities or population false-positive rates. The game cap and breadth
checks limit influence; they do not establish statistical independence.

The human policy is trained to predict human choices, not assistance. Domain
shift, underrated players, preparation and model error remain possible.
These are transparent experimental thresholds requiring external holdout
calibration. No known account identity, closure, validation label, official
Accuracy threshold or punishment action enters this analytical route.

## Diagnostics and tests

Discord Human Moves and Review Gates expose the selected period, actual
numerator/denominator, contributor games, signed excess and exact blockers.
Fractions are labelled as sampled decision fractions, never cheating odds.
The active version appears in the existing footer/progress display. Configured
manual review rules preserve the actual underlying gameplay profile.

Synthetic regressions cover 19/22 across six contributors, a short recent
period after normal history, one huge game, 60% alone, ordinary expected human
play, #2/#3 choices, Bullet guards, missing/invalid model or exact-depth data,
missing comparison data, unknown alternatives, book/conversion exclusions,
normal elite quality, duplicates, archive gaps, ablations, immutability,
identity/status/Accuracy invariance and Discord limits. They establish route
behavior, not real-world detection accuracy. Private case replays stay outside
Git and must be reported separately from synthetic tests.
