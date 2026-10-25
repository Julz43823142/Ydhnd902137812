# Method 3 statistical/evidence audit — 2026-10-25

## Scope and change control

Comprehensive read-only audit of Method 3's 0–100 Suspicion Score, its risk interpretation, all three engine-free behavior graphs (7–9), PGN clock reconstruction, overlapping flagged periods, and rendering cost up to 5,000 games. This change is **stacked on draft PR #286**, which corrects the earlier Suspicion Score evidence-guard bugs. Neither PR is merged/deployed by this audit.

**Non-negotiable:** no Stockfish, Maia, additional chess-position evaluation, external PGN downloads, or additional misconduct votes from opening/timing patterns.

## Confirmed implementation defects and corrections

1. **Selected short periods vanish in coarse graph 8 summaries.** The old "Bullet–Blitz before/during gap" used only chart bins (up to 30 across all 5,000 games), and required bins to fall wholly within a selected HIGH period. A 30-game interval inside 5,000 games can be smaller than one bin. Even with ample valid clock data the summary incorrectly said coverage was insufficient. Fix: compare medians from **exact timestamps**, using before/during/after windows directly from the selected period dates; preserve five *games* per class as the minimum. Chart bucket lines remain descriptive and may smooth spikes. The separately labelled exact-period comparison is authoritative for its sample window.
2. **Graph 8 silently excluded recovered time-pressure clocks.** The per-game series recomputed only strict `clock_valid` measurements, whereas graph 9's context builder includes verified time-pressure clocks reconstructed from before/after + increment. Fix: reuse the same validated per-game medians constructed in one bounded pass over PGN clocks. Never turn missing clocks into zero, and never pool different exact controls.
3. **Opening highlight overclaim.** The old graph 7 highlighted an *entire* bar merely when the bar's first/last timestamps overlapped a marked period, even if no games in that bar had the flagged time class or belonged to the period. Fix: count actually overlapping games of the **same time class** within the period and show `Context period-ID:matching/total` on each bar; shading intensity scales with this observed fraction. An opening bar is not itself a cheating verdict.
4. **Repeated full-history sorting and scans.** Generating each graph repeatedly recomputed marked periods (once per opening bar, class rating panel and phase panel) and graph 8 redundantly rescanned decision clocks. Fix: calculate marked chronology once per three-graph report and cache per-game clock medians for visual plots and the exact-window comparison. Direct calls to individual renderer functions remain supported.
5. **Short time-pressure episodes omitted.** Graph 9 formerly required at least five timed moves **inside the same game** in each time-pressure category. This systematically omitted games with only one to four valid <10-second moves, even when many distinct games provided observations. Fix: permit a single verified clock observation to define that game's median in Start <10s / Start ≥10s, but keep the graph-level requirement of at least five contributing games per plotted point. This prevents systematic loss of short episodes without treating many moves in one game as independent games.
6. **Opening/control composition confounding.** Graph 7 pools bullet/blitz/rapid; a change in the time-control mix may mimic a repertoire switch. Fix: annotate this limitation directly on the chart and retain the separate exact-control opening-switch evidence path. Later study could stratify by `(player color, opponent first move, time class, exact control)`, but only if sufficient observations exist.

## Resolved in the parent draft PR #286

- Accuracy-only MODERATE scores must not gain a corroboration bonus from results that failed the results review gate.
- The Suspicion Score must recheck actual published-Accuracy coverage (80%), matched earlier references, consistency/effect thresholds and adjusted search bounds instead of believing stale cached success flags.
- Malformed supplemental review routes must not produce a numeric score.

## Important residual limitations (not bugs to "fix" by increasing scores)

**An ordinal heuristic is not a calibrated probability.** The displayed ranges 0–24, 25–49, 50–74 and 75–99 are tied to the existing safeguarded LOW/MODERATE/HIGH/VERY HIGH review tiers. They are not percentages of cheating. The 100 endpoint is intentionally unused; this must not be mistaken for an evidence-based percentile. More data does not by itself prove a player cheated.

**Chart 8:** the numerical "gap" is the maximum minus the minimum of the supported per-class *median per-game* think times. The same classes and exact controls are used before/during; at least two classes must have five valid games in each. Before/after flanks have the same calendar duration as the selected period, with a 24-hour floor. Tiny flagged intervals or gaps in the player history may not provide enough coverage. Five games is a *minimum visibility guard*, not a validated significance threshold. Before/after sample sizes can differ considerably: interpretation requires reading n. The graph's visual lines are globally binned and therefore cannot prove a transient effect.

**Chart 9:** early/middle/late are *move-number buckets* (1–10, 11–30, 31+), not chess-theoretic phases; there is no content/engine-based phase recognition. Time pressure means the clock at the *start* of a player's move is <10 seconds, not after the move. Increment, premoves, forced moves, missing clocks and remaining move count can change phase medians even in fair play.

**Chart 7:** first-five-own-move SAN patterns and opponent first responses are descriptions, not chess-position equivalence. The full chart pools time controls, even though individual opening-switch detection is class/exact-control stratified. A time-control-mix warning does not statistically normalize the chart.

**Missing published Accuracy:** player-selected Game Review is potentially nonrandom. Missing scores cannot be imputed as 0 or treated as typical. Chess.com Accuracy is an educational score, not an independent misconduct likelihood.

**Multiplicity and selection:** Method 3 explores many overlapping candidate windows and has explicit search corrections. A post-hoc opening or timing coincidence with the winning selected window is exploratory context, **not an independent corroborating p-value**. Multiple correlated evidence channels must not be combined by naïvely summing apparent significance.

## Evidence standards and independent research

- [Chess.com Fair Play Help](https://support.chess.com/en/articles/8568369-what-do-i-need-to-know-about-fair-play-on-chess-com): enforcement considers multiple factors, and human review is necessary; high Accuracy is neither proof nor disproof.
- [Chess.com official Accuracy definition](https://support.chess.com/en/articles/8708970-how-is-accuracy-in-analysis-determined): CAPS2 is a game-review score and is not a cheating probability.
- [American Statistical Association (2016), DOI 10.1080/00031305.2016.1154108](https://doi.org/10.1080/00031305.2016.1154108): p-values do not measure the probability that an allegation is true; selective window choice and undisclosed multiple testing produce misleading findings.
- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework): evaluate risk, data quality and trustworthiness, especially when automated scores affect real people.
- [TRIPOD+AI transparent reporting, BMJ 2024](https://www.bmj.com/content/385/bmj-2023-078378): report development/evaluation cohorts separately and evaluate generalization; this guidance concerns prediction models and is applied here by analogy, **not** as chess-specific validation.

## Recommended next research (no automatic scoring changes)

1. **Freeze the score definition** before independent validation. Assemble account-disjoint control cohorts with independently adjudicated or reliably confirmed labels, ethically/legally obtained. Do not count thousands of games from one account as thousands of independent persons.
2. Hold out accounts and time periods never used in tuning. Evaluate precision (positive predictive value) at realistic prevalence, false positives per 1,000 screened accounts, sensitivity/specificity, unknown-score rate and subgroup behavior by rating, format, increment and archive/clock completeness.
3. Quantify natural **clock convergence** in fair-play controls; compare changes within stable exact time controls and opening habits, not just before/after a suspicious window. Check session-length and time-of-day confounding.
4. Quantify normal opening-pattern entropy and repertoire concentration separately by color, opponent first move, class and control. Do not use unvalidated differences to raise priority.
5. Assess graph-level visual integrity: zoomed axes, mixed-control caveats, numbers of observed games, nonrandom missing values, short-period coverage and consistent "not proof" language.
6. Benchmark actual **end-to-end** wall time for 1,000 and 5,000 games including PNG conversion and Discord uploading on comparable hardware. No performance speedup is claimed by this PR until such measurements exist.
7. Continue mandatory human review, preserve audit trails and avoid automatic bans from ordinal heuristic scores.

## Acceptance tests in this PR

- 5,000-game synthetic history with a 30-game short selected interval that earlier coarse bins missed: exact date-based gap is still computed from the correct 15 Bullet and 15 Blitz games.
- Time-control mismatches and insufficient same-date samples produce unavailable comparisons rather than synthetic zero values.
- Recovered, internally verified time-pressure clock measurements are reused by graph 8.
- Marked periods are computed exactly once for the three charts and preserve stable numbers.
- Short time-pressure episodes (even one eligible move per game) contribute across five or more games; missing/invalid clocks still do not.
- Opening rows disclose the actual flagged-game numerator and the total in the displayed group.
- Existing production Method 3 logic, thresholds, review priorities, and Suspicion Score computation remain unchanged by this PR.
