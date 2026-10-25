# Fair Play Method 3 v83: landscape readability, quality audit and controlled cutover

## Root cause: making an image 1800px wide is NOT enough

v82 multiplied horizontal SVG coordinates but kept the tall canvas. In the Discord viewer this compressed portraits into narrow previews. The actual old/post-v83 **PNG** dimensions, with all nine outputs 1800 pixels wide, are:

| Image | Before v83 height | v83 height | Source/layout |
|---|---:|---:|---|
| 1 Rapid | 1390 | 1390 | Full-width time-series |
| 2 Blitz | 1390 | 1390 | Full-width time-series |
| 3 Bullet | 1390 | 1390 | Full-width time-series |
| **4 Accuracy frequency** | **up to 2110** | **1290** | Two-column rolling class trends and exact-control comparison |
| 5 Rolling expected / observed | 1110 | 1110 | Full-width reference chart |
| 6 Selected window | 1480 | 1480 | Four unabridged signals |
| **7 Openings and rating** | **2980** | **1350** | White/black history side-by-side, three compact rating charts |
| 8 Clock convergence | 1040 | 1040 | Full-width UTC timeline |
| **9 Phase / pressure** | **2020** | **1175** | Five panels in a two-column grid, matched time-pressure summary in spare cell |

Height improvement alone is not a guarantee of device-specific full-width display, but reduces the excessive portrait aspect ratios. All original game observations, counts, 100%-normalized opening bars, marked-period overlays, UTC time axes, coverage gates, unfilled gaps, matched before/during/after comparisons and per-time-class rating scales remain.

Chart 7 abbreviates very crowded per-bin period markers to `C1:5/10 +2`, explaining the abbreviation on the figure. It does not pretend that a bin's entire opening distribution was flagged; labels count only the relevant games. The full `marked_periods` diagnostic remains available for detailed audit. Long opening names are truncated in the dense legend so they cannot obscure the neighboring panel. This affects only the drawing, not the opening calculation.

## Performance and missing data

Published Accuracy rolling `≥80%` / `≥90%` now uses cumulative prefix counts with a **linear-time** pass per threshold instead of repeatedly scanning the previous 100 games. It preserves exact played-game windows, 10-game visual stride plus final point, available-only denominators, missing-data gaps, and no false zeroes. A regression test compares thousands of rows, including missing, `NaN`, boolean, out-of-range, and final off-stride windows against the original naive formula.

Further scope review found that the biggest remaining runtime cost comes from collection and rendering of thousands of PGN-derived samples. This version limits changes to measured, auditable rendering work rather than changing method/thresholds or removing underlying games to make the scan appear faster. Nine actual CairoSVG PNG renders are tested with width/aspect assertions rather than substituting mock image bytes.

## Detector validity and deployment

- No new engine, model, Stockfish, Lc0 or PGN/API fetch has been added to Method 3.
- No automatic misconduct action or new, unvalidated suspicion rule has been added. The v82 fix against absolute Accuracy false positives remains active. v83 **does not change the review classification or 0–100 ordinal score**.
- Published Accuracy and actual game results remain separate evidence; opening/timing visuals are context only.
- All nine numbered images and the shortened Discord review retain a clear version: `fairplay-v83-landscape-and-coverage`.
- Normal Discord Daily Puzzle workers continue to rotate gracefully after syncing critical state. The GitHub Actions concurrency group is unchanged. Its **cancel-in-progress is TRUE only on an explicit main-branch push commit containing the operator tag `[replace-daily-run]`**, which is used for this owner-authorized v83 merge. It cancels only the old Daily Puzzle run so the new main worker can start without waiting hours. Other unrelated persistent workers (Guess, Survival, YouTube, Minigames) are deliberately not interrupted.
- **Cutover risk:** the old process does not have an out-of-band gracefully-shutdown endpoint. Immediate GitHub cancellation can interrupt an active review and lose unsynced in-memory progress. Normal encrypted checkpoints and GitHub sync limit but cannot eliminate this risk. The alternative is to let the old process finish its roughly 4h20 planned clean rotation. The user explicitly authorized immediate stopping; the tag is intentionally opt-in and not used on ordinary pushes or cron schedules.
- Verify the new main-branch Daily Puzzle run starts and reaches its bot-execution step; a merged commit or queued Actions run alone is not evidence that the Discord bot is online.

## Evidence reviewed

The three uploaded, privacy-sensitive v81 compressed evidence files were examined **locally** (not committed). Each had `priority_basis=absolute_accuracy_frequency`, `selected_period_priority=LOW`, no historical Accuracy increase signals, zero highlighted periods and fewer actual points than Elo expectation. All three were promoted under the v81 absolute-frequency bug fixed in v82. Keeping that fix is essential, but neither LOW nor HIGH is a proof of fair or unfair play. No players receive name-based exceptions.

CI covers the full bot suite, Method 3 charts, numeric parity, actual PNG signatures/dimensions, version guard, and deployment-concurrency marker semantics.
