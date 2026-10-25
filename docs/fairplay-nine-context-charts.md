# v75 rating history below openings

Version: `fairplay-v75-rating-context`. Builds on the v74 readable charts.
Page 7 retains opening-share bars above three separate Bullet, Blitz and Rapid
rating panels. All available valid rated-game observations are drawn, without
smoothing or downsampling. Each panel has its own padded rating scale and shows
first/last observed rating, signed difference, range and sample size.

The shared x-axis is chronological game position across the collected sample,
identical to page 8. Numbered priority dates are mapped to those positions; the
originating class uses its priority color, other classes purple Context. Ratings
are recorded at games, not inferred after games. No one-game shift is applied:
incomplete history and mixed classes make that inference unreliable. Missing
ratings break lines; unrated games do not enter rating changes. Lines connect
observations and do not assert that intermediate games or rating updates were
collected. Rating history remains descriptive and cannot promote priority.

Validation includes sample immutability, missing/unrated filtering, unsmoothed
rating extrema, identical period positions across classes, XML validity and
visual inspection of the combined PNG with 900 synthetic games. There are still
nine attachments; page 7 is taller, not a new tenth image.

---

# v74 readable context charts

Version: `fairplay-v74-readable-context`. This update includes the shared numbered
period markers from PR #276. Classification thresholds and engine budgets are unchanged.

- Page 7: 100% stacked opening-choice bars across four calendar intervals, within
  matched color / exact control / opponent-first-move groups. Top choices have
  consistent colors within each group; remaining choices are Other. Small samples
  are marked. These are first-move families, not full opening identification.
- Page 8: separate Bullet, Blitz and Rapid panels with the same seconds scale and
  shared chronological game-number axis across all collected games. Each line uses
  the most-played exact control, with at most ten recent game medians and at least
  five games within sixty shared positions. Sparse samples remain gaps. Numbered
  priority bands use original period dates mapped to the shared game positions.
- Page 9: grouped seconds bars for early/middle/late moves and clock pressure,
  comparing before/during/after selected dates. Without a selected period, blue
  shows all history. Missing samples are dashes, not zero. Each cell requires five
  contributing games with at least five valid moves per game/category.
- Every page retains numbered shared calendar strips: original-class HIGH is
  orange; matching dates in other classes are purple Context, not extra evidence.
- LOW / Within Typical Performance Range is green; Insufficient Data is white.

The pressure graph previously inherited an engine timing filter that excludes
clock trouble. Its descriptive clock extraction now recovers those observations
only when raw before/after clocks and increment reproduce the stored think time.
Engine/scoring timing flags are unchanged. Each game still contributes one median.

Validation: regression tests and visual PNG inspection with synthetic games.
The previous implementation and benchmark below describe the initial v73 layout,
not the redesigned v74 presentation or its runtime.

---

# Nine-image Method 3 report

Three descriptive images join the six existing pages. No priority, engine,
API or collection changes; the 5,000-game limit remains. No automatic promotion
to VERY HIGH from opening changes or similar thinking times.

7. Opening choices: reuse `opening_choice`, splitting color, exact control and
opponent's first move. Show counts, dominance, distinct choices and effective
breadth (exp(entropy)) through four equal calendar intervals and before/during/
after the selected dates. Display up to three largest strata, ensuring both
colors are represented when available. Other strata and unrecognized games are
explicitly counted. Opponent median Elo is descriptive context, not a statistical
rating adjustment. These are first-move choices/families, not full ECO variation
identification; transpositions and memorized variations are not resolved.
8. Timing: six shared UTC calendar intervals; fix each class's most-played exact
control over the whole sample rather than mix increments. Plot medians and IQRs
of game medians; gaps for fewer than five eligible games. Each game needs five
valid clock moves. Show Bullet–Blitz distance and three-class range, with coverage.
Whiskers describe dispersion between game medians, not individual move peaks.
Historical marked periods use the existing overlay colors/numbers. Changes are
not a validated assistance signature. The selected control may miss activity in
other controls, so coverage is printed.
9. Phases: move numbers 1–10, 11–30, 31+; no positional evaluation. Pressure uses
clock BEFORE the move, strictly less than 10 seconds (10 belongs to normal),
without adding increment to that threshold. Valid think times already account
for increment. Zero-second premoves remain. At least five valid moves per
category/game and five contributing games per displayed value. Counts make
unequal availability visible. Before/after uses all matching games, not causal
matched controls; phases are calendar context around the selected period.

Missing values are never zero-filled. Each game contributes one median, avoiding
an oversized influence from long games. Opening and timing observations remain
correlated context; no new evidence votes are added. Calendar aggregation can
hide short spikes; the existing selected-period zoom remains available.

Discord delivery remains one message with up to nine separate PNG attachments
(under its ten-attachment ceiling). The client chooses gallery layout; a literal
3×3 grid is not guaranteed. Do not combine the nine pages into an unreadable
thumbnail. New filenames/titles are numbered 7–9. Fewer classes/selected periods
can mean fewer than nine total attachments.

Validation: synthetic clock-start/end boundary, increment/control separation,
invalid clocks, premoves, minimum samples, equal-game weighting, known convergence,
color/opponent conditioning, XML validity, read-only result invariance and nine
attachments/progress totals. Images rendered and visually inspected.

Benchmark: 5,000 synthetic games with 200,000 clock decisions, three repeated
renders on the same machine/input: previous six-page renderer median 0.783s,
new nine-page renderer 1.241s (about +0.46s). Includes context computation and PNG
rendering; excludes fetching, scoring, queue and Discord. Not an end-to-end SLA.
No merge, deployment or live Discord test was performed for this draft.
