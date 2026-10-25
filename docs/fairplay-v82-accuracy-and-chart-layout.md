# Fair Play Method 3 v82 — absolute-Accuracy false positives and 9-chart layout

## Evidence reviewed

Three user-supplied **v81** evidence snapshots (not committed because they contain full game histories) independently demonstrate the same classification defect:

| Snapshot | Pre-frequency results priority | Historical Accuracy increase | Absolute-frequency descriptive signals | v81 final priority | v81 score |
|---|---|---|---|---|---|
| A, 5,000 games | LOW | none | Blitz 300+0: 31/60 ≥90, 14/60 ≥95 | MODERATE | 37 |
| B, 5,000 games | LOW | none | Blitz 180+0: 571/1207 ≥90, 198/1207 ≥95 (plus two other controls) | MODERATE | 37 |
| C, 2,736 games | LOW | none | Rapid 600+2: 28/43 ≥90, 11/43 ≥95 (plus another control) | MODERATE | 37 |

Their selected *results* windows were below their own Elo expectations. The historical change route was empty. The literal v81 line
`elif priority=='LOW' and absolute_signals: priority='MODERATE'`
promoted these accounts because a fixed fraction of high published Accuracy scores in a comparable rating band exceeded static thresholds. **There is no calibrated frequency distribution for comparable strong players and no before-versus-after increase.** Arbitrarily high Accuracy frequency is not proof of misuse.

The independent score presentation then mapped every alternative MODERATE route to **(25+49)//2 = 37**, without examining its effect size, sample or guard; therefore distinct accounts got exactly the same number by design. The uniform number conveyed a specificity that the evidence did not possess.

## v82 changes

1. **No hardcoded account names or LOW overrides.** Absolute frequency continues to be measured and stored as a descriptive chart/diagnostic but may **not independently promote LOW to MODERATE**. Confirmed earlier-history Accuracy frequency increases (matched exact control and opponent rating, completeness, multiple-comparison tail guard) can still promote a LOW review to MODERATE. Existing results-based MODERATE/HIGH/VERY HIGH and conservative combined-review route remain unchanged. The three real archived snapshots independently reproduce the pre-frequency LOW state; their v82 policy replay is LOW. This is a policy replay of prior evidence, **not an independent proof of innocence nor a brand-new live scan**.
2. **Remove the score category midpoint.** Score bands remain 0–24/25–49/50–74/75–99 as ordinal manual-review categories. The historical-frequency route now uses its own numeric observed-versus-baseline excess beyond the existing 15-point gate and adjusted comparison-tail bound. Missing/invalid evidence yields **Unavailable**, not invented 37. The combined-review route uses only its associated matching standardized results effect, preserving its existing ≥100-game, ≥3.0-residual gates. A legacy v81 `absolute_accuracy_frequency` result yields Unavailable if reevaluated directly, not an invented score. LOW non-positive verified results evidence can naturally yield **0** across multiple players; avoiding identical numbers by manufacturing artificial distinctions would be scientifically wrong. No score is a calibrated cheating probability.
3. **Version:** `fairplay-v82-evidence-and-wide-charts` (Method 3 only; legacy review versioning unchanged).
4. **Condensed Discord first card:** `Suspicion Score` followed only by `NN/100` (or Unavailable), no bracketed scale/repetitive explanatory paragraph. Retains Results confidence, Sample / coverage, Performance levels, Why HIGH was not reached, Selected period, essential safeguards and the human-review warning. Footer shows short identifiable version.
5. **All nine chart PNGs:** explicit stable numbers 1–9, each rendered at **1800 native pixels** wide, including formerly 1200px graphs 4, 7, 8 and 9 and formerly 1440px graphs 1–3, 5, 6. Horizontal plot positions, bar lengths, polygons, and date/axis labels expand into real content area; **font sizes and heights are unchanged** so text is not horizontally distorted. This particularly reduces the portrait aspect of image 7 and improves horizontal room. PNG filenames, count, statistics, periods and graphic semantics stay the same.
6. Regression tests replay anonymized *distinct aggregate evidence* from the three attached v81 results, test absence of a username allowlist, check high-Accuracy proportion alone never increases priority, guard and vary non-absolute MODERATE scores, and parse all nine final SVGs to ensure same size and unique numbers.

## Limits / validation

- High published Accuracy is correlated with chess strength, game length, play style, and opponent context. Published data can be selectively available. An individual high proportion must not automatically trigger suspected misconduct without a validated reference.
- The within-player matched historical signal remains an **exploratory** comparison with an independent-game modeling assumption. LOW does not prove the absence of cheating; HIGH does not prove cheating.
- This PR does not use chess engines, Stockfish, local model move evaluations, new PGN downloads, model training, or automated sanctions in Method 3. It does not change the chart 7/8/9 descriptive data analysis and does not score timing or openings.
- No attempt was made to force three public figures into LOW based on identity. They are natural regression cases from supplied evidence precisely because the existing result evidence does not support independent MODERATE priority.
- Manual-review index remains an uncalibrated ordinal **0–99**, displayed as NN/100, *not* a probability. A future validated holdout cohort is required to quantify actual false-positive rates or sensitivity.
