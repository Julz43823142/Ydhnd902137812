# Fair Play v86 — sample support and readable chart denominators

## Scope
- The 100-played-game official Accuracy-frequency trend now requires 60% measured coverage. Missing or player-selected published scores are never a synthetic zero or a one-game 100% streak.
- Page 8 still has one pooled win-rate curve. A compact 50-played-game format-mix strip reveals Bullet/Blitz/Rapid composition changes; a chess points percentage makes the distinction between wins and scored points explicit. The x-axis remains **game order** (no new calendar-date axis).
- The public Discord report uses the concise English label **Review Index (not a %)** instead of calling an uncalibrated 0–99 review index a suspicion probability. Sample support is now based on the selected period rather than inflated by unrelated history. Only genuine finite Accuracy and ratings count toward shown data coverage.
- The Daily Puzzle workflow does not cancel an active controller on a main push; deployment waits for a controlled worker rotation. A pending GitHub Actions job can be coalesced with later pushes, so production handoff remains an operational step to supervise.

## Unchanged
No modification to LOW/MODERATE/HIGH/VERY HIGH decision gates, game selection, chess engine budgets, API requests, deadlines, or the nine-page chart order. No new prose sections or lengthy report messages were added. Do not infer a probability of cheating from an index, result tail bound, official Accuracy or win-rate graph.

## Validation
Unit tests cover low coverage, window sampling, pooled-game composition, genuine point percentages, selected-period support, PNG rasterization, and non-cancelling deployment concurrency. CI and runtime behavior must be rechecked before any production merge.

## Deployment rule
Keep this PR draft and off main until CI succeeds and the live controller is safe to rotate. A green CI run is not empirical detection validation.
