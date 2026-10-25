# Follow-up substantive audit: matched behavior evidence, calendar time and false-positive controls

**Date:** 25 October 2026. **Scope:** Method 3, at most 5,000 games, existing Chess.com Accuracy/ratings/results/PGN clocks/opening prefixes. This work builds on unmerged draft PRs #286 (Suspicion Score safeguards) and #287 (chart coverage and period fixes). **No Stockfish, Maia, chess move-quality assessment, external PGN downloads, misconduct classifier changes or automated sanctions.**

## Research question
Can Method 3 describe actual *within-player* changes more faithfully, without confusing changes in time controls, opening opportunities, clock availability or game density with cheating evidence?

## Newly identified weaknesses and fixes

### 1. Historical modal controls hid the selected period
Graph 8's time-series originally chose each class's single most frequent exact control from the whole account history. A player with many 60+0 games historically but 60+1 during the selected period could show “no comparison”, even where there are plentiful 60+1 games both before and during. That is a sampling artifact, not normal timing.

**Fix:** compare exact controls separately **within each time class**, require ≥5 games with valid clock medians in both relevant eras, and choose an eligible exact control by balanced *coverage* (maximizing the smaller of the two counts), never by how striking a gap looks. If fewer than two classes have comparable controls, report “unavailable”. Report chosen exact controls, sample counts and a gap in seconds, not an invented probability. Visual lines remain modal class controls; the period-matched control can legitimately differ and is named.

### 2. Graph 8 and 9 used game-index X scales, not real calendar time
Equal-sized sets of chronological games can compress a multi-year absence and stretch one active afternoon equally. That distorts the apparent duration of an abnormal interval and calendar overlaps.

**Fix:** use 1–30 **equal-calendar-duration buckets** determined by the observed UTC time range, with at least five distinct clock-eligible games in each class/control/metric bucket. Gaps remain missing rather than interpolated. Overlay flagged periods at actual UTC timestamps, label calendar dates, and keep one shared temporal axis for each class and the phase/pressure panels. The exact selected-period comparison uses raw dates, not plotted bucket aggregates.

### 3. Opening proportions were not a measure of repertoire concentration
Graph 7 stacked the top move patterns but did not quantify whether a player's repertoire genuinely becomes more repetitive. Mixing White and Black responses or different controls confounds such claims.

**New descriptive metric:** **same-choice pair repetition**, the probability that two randomly chosen *different games* from the same sample have the same opening choice:

\[
R=\\frac{\\sum_{c} n_c(n_c-1)}{n(n-1)}.
\]

Unlike naïve `sum((n_c/n)^2)`, this does not add a spurious self-match component that increases apparent repetition for small groups. Compare the immediately preceding ≤50 comparable opportunities with the selected interval and next ≤50 opportunities, in the SAME (time class, exact time control, player's color, opponent's initial choice) stratum. Require ≥8 comparable games before and during; after is optional. Select the stratum by *sample support*, **not** by largest observed difference. Display percentages, n and distinct choices; no suspicion points, statistical significance claim or proof.

### 4. Graph 9 needed a within-game time-pressure reaction measure
A raw median under 10 seconds can vary with the player's normal pace, increments, mode and game mix. Comparing different games' medians can fake or hide a reaction to time trouble.

**New descriptive metric:** for games containing at least one verified `clock_before < 10s` move and one verified `clock_before >= 30s` move, calculate
`median(think seconds under 10s) / median(think seconds from >=30s)`.
Each game contributes **one ratio**, no matter how many moves it contains. A zero/near-zero normal-pacing denominator yields *missing*, not infinity; ratio <1 means the player played faster *in that game* with less than 10s. Compare before/during in the same time class AND exact control using a matching calendar flank (minimum 24h), with ≥5 independent games in each era. Show paired n; optionally show after if coverage is sufficient. These comparisons are uncalibrated and do not change priority.

## Why these are descriptive, not detection features
- All intervals were selected using existing result/Accuracy evidence. Looking at opening/timing variation *after* selecting an anomalous window can find coincidences by chance. Correlated exploratory observations are not separate independent votes.
- Time use is influenced by remaining time, increment, skill, game phase, premoves, session fatigue and opponent choices. Even within-game ratios do not hold phase/difficulty fixed.
- Opening repetition varies with opposing opening replies and repertoire familiarity. The 8-game minimum is a chart coverage threshold, not a statistically validated cheating cut-off; estimates have substantial sampling uncertainty.
- Published Accuracy is not a random complete sample and cannot be imputed for unanalysed games.
- The Suspicion Score is an ordinal review-priority heuristic derived from safeguards (0–24 LOW, 25–49 MODERATE, 50–74 HIGH, 75–99 VERY HIGH), not a percentage chance of wrongdoing. **This PR does not change it.**
- In particular, “flatter thinking time” cannot yet be treated as evidence: real work studying millions of honest chess decisions finds timing differences depend on computation incentives and available time.

## Supporting research (context, not permission for engine computation)
- Russek et al., *Time Spent Thinking in Online Chess Reflects the Value of Computation*, Cognitive Science (2025), https://pmc.ncbi.nlm.nih.gov/articles/PMC12553403/ — thinking time depends on situational incentives in a very large dataset. This research uses engine metrics; **this bot does not reproduce them**.
- Barnes and Hernandez-Castro, *On the limits of engine analysis for cheating detection in chess*, Computers & Security (2015), https://doi.org/10.1016/j.cose.2014.10.002 — statistical anomalies in chess can produce false-positive allegations.
- Laarhoven and Ponukumati, *Towards Transparent Cheat Detection in Online Chess* (2023), https://research.tue.nl/en/publications/towards-transparent-cheat-detection-in-online-chess-an-applicatio/ — transparency and formalized validation are major unresolved challenges.
- NIST AI Risk Management Framework, https://www.nist.gov/itl/ai-risk-management-framework — risk scores need validity, reliability, data lineage and real-world evaluation.
- American Statistical Association, statement on p-values (2016), https://doi.org/10.1080/00031305.2016.1154108 — a low p-value or high residual is not the probability that a user cheated.

## Additional opportunities NOT implemented as unvalidated suspicion bonuses
1. Analyze 5–10, 10–30 and 30–60 seconds separately by increment and format with enough per-game coverage; plot denominator coverage and balanced era counts. This needs UX space or a new graph; do not silently add axes or new detection votes.
2. Stratify a genuine holdout cohort by exact control, rating, account history and session patterns. Report sensitivity, false positives per 1,000 accounts, prevalence-dependent precision and unknown/missing-data percentages. Account-disjoint train/test splits are essential; 5,000 games from one account are NOT 5,000 independent players.
3. Benchmark end-to-end graph rendering and Discord image conversion on matched 1,000- and 5,000-game datasets before claiming a speedup. The implementation reuses validated records and avoids redundant clocks but does not include a production wall-time measurement.

## Regression contract
- Selected-period control matching can choose a globally rare but locally well-supported exact clock, never mix increments, and never impute missing classes.
- Repertoire finite-sample corrected pair-repeat probabilities are computed separately for matching color/class/exact-control/opponent context; unknown reference samples remain unavailable.
- Per-game under-10/30+ acceleration counts games, not plies; it excludes invalid/zero denominators; no engine position features.
- Graph 8 and 9 share UTC calendar axes with real gaps, while marked periods remain at actual timestamps.
- Existing 9 PNG charts, Method 3 thresholds and 0–100 Suspicion Score remain unchanged.
