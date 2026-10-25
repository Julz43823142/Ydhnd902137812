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
