# Method 3 v66 — evidence reliability and independent validation

**Status:** targeted safety/correctness improvement, **not** a statistically validated increase in engine-cheating detection. Built from the 20 October 2026 17-page research review. This change does not add Stockfish or Maia to Method 3 and does not change the HIGH result-residual/search thresholds.

## What changed and why

1. `independent_results_tier` always runs the time-control Elo and repeated-opposition checks, even if a personal historical Accuracy check passes. Previously `accuracy_confirmed=True` returned early, and `VERY HIGH` never entered those checks at all.
2. If a selected time control has **zero rated games with valid ratings in the preceding 365 days** and its selected period contains at least five games, `rating_support` now exposes `unverified=True`. This is distinct from the older cross-control discrepancy rule (fewer than 20 rated anchors, 10+ rated other-class games, gap >=400).
3. A flagged `HIGH` or `VERY HIGH` period is limited to `MODERATE`, preserving human screening without treating Accuracy as proof that the Elo/independence assumptions have been repaired. The existing extra short-period downgrade to LOW remains for *demonstrably mismatched* time-control Elo without corroborating Accuracy. Ordinary valid-rated candidates and calibrated thresholds are not changed.
4. Method 3 diagnostics now emit **observed** denominators for valid player/opponent rating pairs, published Accuracy, identifiable opponents, skipped reasons, and archive coverage. These counts are not claims about missing data. The Discord summary and Review Gates show these limits and actual applied guards.
5. `fairplay_validation.py --profile method3-local` selects the exact Method 3 scoring entry instead of implicitly evaluating the v50/v51 engine route. It clears dispatch credentials, disables distributed/deep computation flags, and isolates the Method 3 flag. Validation labels remain outside scoring.

## How to benchmark the correct route

A *private*, locally ignored `validation_cases.local.json` contains player-disjoint, evidence-audited labels; a private offline directory contains frozen Chess.com PubAPI snapshots. The existing validation CLI accepts both:

```bash
python fairplay_validation.py validation_cases.local.json \
  --offline-dir fairplay_validation.local/snapshots \
  --profile method3-local --partition development

# Read-only PubAPI live collection; never submit the resulting report
# as proof of cheating:
python fairplay_validation.py validation_cases.local.json \
  --live --profile method3-local --partition holdout
```

Run the matching depth profile separately (`--profile production-local`) on **exactly the same player-disjoint cohorts**. Do not compare different collection dates as if only the detector changed. `method3-local` does not run chess-engine analysis. Keep snapshots and reviewer labels private. A Chess.com fair-play closure can concern more than engine assistance, so it cannot independently be used as a certified game-level engine label.

## Required evaluation before saying the detector is better

- Freeze the commit, Method 3 flag, config, observation cutoff and account-disjoint/chronological holdout. Prespecify which outputs count as a review escalation.
- Compare v65 and v66 *on the same frozen games* and separately on independently audited engine-use and audited normal cases.
- Report HIGH/VERY HIGH false-positive rates on actual audited normals, recall on audited engine-use cases, MODERATE queue size, skipped/abstained inputs, and human reviews consumed; include uncertainty intervals.
- Inspect subgroups: rated vs unrated, provisional/fast-rising ratings, exact time controls, limited Accuracy coverage, repeated opponents, incomplete archives.
- Run adversarial synthetic tests for ZERO recent rated anchors, Accuracy-confirmed HIGH/VERY HIGH with unreliable Elo, concentrated head-to-head games, missing opponents, and time-separated references.
- Do not use the nominal Chernoff or Accuracy binomial-tail model as a real population false-positive probability. The code retains its original assumptions and uncalibrated bounds.

## Remaining research work

The report proposes player-/opponent-/session-level calibration, empirical maximum-window null distributions, missingness sensitivity checks for published Accuracy, and a budgeted second-stage Stockfish/Maia review on truly difficult non-forced positions. These remain **proposals**, not newly deployed detectors. A peer-reviewed-style code review is not external peer review or proven detection performance.

Relevant implementation: `fairplay_method3.py`, `fairplay_method3_reliability.py`, `fairplay_validation.py`, `fairplay_method3_ui.py`. The prior `FAIRPLAY_METHOD3_V65_RESEARCH.md` remains a historical account of v65, not a claim about v66.
