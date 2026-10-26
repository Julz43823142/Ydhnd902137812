# Fair Play Method 3 v85 — rolling context and win-rate charts

**Scope:** presentation only. All review/priority thresholds, earlier-reference tests,
period selection, Chess.com collection, and engine budgets are untouched.
The version string changes from v84 to v85 because the visual output changes.

## Nine-image order

1. Rapid: official published Accuracy per game and per-game median think time
   with green moving-average overlays; opponent Elo and Accuracy/Elo cohorts remain.
2. Blitz: same.
3. Bullet: same.
4. Selected results period and up to 100 neighbouring games on each side
   (formerly page 6).
5. Elo-expected vs observed outcomes (10-game rolling), by class (unchanged).
6. White/Black opening-choice panels; rating histories are no longer attached
   under the opening plots (formerly page 7).
7. **Combined page:** Bullet, Blitz and Rapid published Accuracy frequency
   across the top, followed by the pre-existing clock-convergence comparison
   across the bottom. The old frequency page's bottom *exact-control
   Before/During/After* table is removed because it duplicated additional
   context while consuming most of the image. It is still available to
   offline helper callers, not rendered. The clock comparison still includes
   actual exact-control matched period diagnostics and coverage limitations.
8. **Rating and total win rate:** observed pre-game rating histories in three
   distinct Bullet, Blitz, Rapid panels, then **one combined chronological
   win-rate curve** covering all three classes, with absolute wins, draws,
   losses and percentages both overall and for the selected class-specific
   period. Named priority dates and the selected period appear as shaded
   historical context.
9. Thinking-time phase and pressure chart (formerly page 9, unchanged).

All final images are widened to a uniform 1800px with a landscape ratio.
No ninth/tenth Discord attachment is added: nine files remain the maximum.

## Definitions and data quality

### Green moving averages on pages 1–3

- **Official Accuracy:** rolling arithmetic mean of Chess.com's available
  *published* per-game Accuracy values. This is not a computed engine cheating
  signal or a matched population benchmark.
- **Thinking time:** rolling arithmetic mean of the **per-game median**
  seconds from valid PGN clock observations, not a move-weighted mean across
  all games.
- Both use the previous **30 played games** of that class, including the
  current game; require at least **5 valid values** and **60% coverage**
  among games in the window. Starts are shown after sufficient coverage.
- Missing/invalid observations cannot become zero, and whole windows with
  inadequate observations appear as line breaks. Raw game plots and the
  published Accuracy orange-dashed series remain visible.
- Moving-average lines are descriptive; they do not change Method 3
  classifications or indicate a calibrated cheating probability.

### Page 8 win rate

- Scope: **all collected Bullet, Blitz and Rapid games** in chronological
  order, including eligible unrated games. Daily correspondence chess is not
  silently combined with live time classes.
- A valid game with result `1` is a win, `0.5` is a draw, and `0` is a loss.
  **Win rate = wins / (wins + draws + losses)**. Draws are in the denominator
  and are not treated as half-wins.
- Missing/invalid results are excluded from that percentage and visibly
  accounted for in games-versus-valid counts. They are **not** losses.
- The blue trend uses the most recent **50 played games** (all classes
  mixed), sampled every five played games plus the final point. It requires
  at least **10 valid completed results** and **60% coverage**; gaps remain
  gaps.
- Selected-period absolute W/D/L are computed from the exact selected class
  and the selected **class-specific** ordinal interval, not from a mixed
  calendar span. Shading by actual dates on the pooled x-axis is explicitly
  *context*, not evidence or a claim that other-class games belong to the
  selected period.
- Rating histories use observed pre-game Elo and their original separate
  scales; missing Elo is not extrapolated. Rating fluctuations and win rate
  are not detection features.

## Performance and validation

Incremental sliding numerators/denominators are **O(n)** over at most the
collected Method 3 history. Reuses existing opening and timed-game context
within one render; does not run Stockfish or retrieve more games.

Regression tests cover 0–5,000 games, sparse/malformed values, mixed classes,
win/draw/loss/missing semantics, selected windows and shared context blocks;
they verify all nine SVG titles/order, 1800px landscape dimensions and
rasterizability, preserving source observations without mutation.
GitHub Actions is the authoritative CI before any merge.

## Deployment

Keep PR #293 draft until validated and safe to merge. A push to `main` can
restart/cancel the Daily Puzzle controller while it is doing work.
No current production run is stopped by this PR.
