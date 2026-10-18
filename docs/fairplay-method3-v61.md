# Fair Play v61 — equal live-game treatment and whole-history periods

The fast adapter collects up to 1,000 eligible standard live games in total.
Rated and unrated share the same chronological class periods, earlier personal
references, thresholds and confidence rules. Neither is capped or preferred by
status. Bullet remains included; Daily remains excluded. Ratings can misrepresent
playing strength in either mode. No account closure metadata is used.

The latest-200 restriction is removed. Every collected game can belong to a review
period, whether at the beginning, middle or end of the collected history.
Search widths are 5/10/20/50/100/200 games plus available whole-class periods.
Widths up to 20 slide one game at a time; longer widths advance by one quarter of
their width, with an explicit final window. Windows are contiguous chronological
Rapid, Blitz or Bullet sequences, never sets of cherry-picked winners. References
for Accuracy remain strictly earlier, same control and rating/opponent matched.
An exact prefix cache reuses reference matching and quantiles across windows.

## Search guard

Searching thousands of windows increases opportunities for a chance streak.
Existing results requirements (coverage >=80%, descriptive residual >=2.4 and
points excess >=20 percentage points) remain. HIGH additionally requires a
Chernoff upper bound for bounded Elo-points sums, multiplied by the number of
distinct candidate windows, <=0.01. MODERATE can use the looser <=0.10 boundary.
Reference modes do not count the same physical window twice. Overlapping windows
are not independent confirmations. Five–nine-game results routes require full
result coverage and wins in every game; a HIGH selected from <10 games has LOW
confidence. One game cannot produce HIGH. Accuracy can still support MODERATE
with the existing >=8-point excess / >=60%-above-reference criteria. VERY HIGH
is unavailable in this metadata-only method.

This model guard assumes correct Elo expected points and independent games.
Actual chess histories need not satisfy either assumption. The bounds are not
calibrated real-world false-positive rates or cheating probabilities. Underrating,
training, rating lag and legitimate hot streaks remain alternative explanations.
No Stockfish/Maia gameplay confirmation is claimed and missing Accuracy is unknown.

The public report dates the selected chronological period. Graphs mark that actual
period wherever it occurred, instead of highlighting only a recent tail. Rated and
unrated share the same class graphs (at most six PNGs). Earlier/recent history
charts remain descriptive context, not additional priority votes.

## Stable entry panel

The entry text is short and generic. The existing versioned panel is reused even
when other messages follow it. If it falls outside the latest 200 messages, its
known ID is fetched and updated in place. Permission/read failures do not authorize
a replacement. Only a genuinely deleted/missing panel is recreated. Duplicate
recognized panels are removed; unchanged panels are not repeatedly edited.
A worker still running old code must be restarted to adopt this behaviour.

Regression tests cover early/middle/recent periods, a five-game exceptional
period, one-game/equal-rating short streak exclusions, rated/unrated invariance,
combined-status references, exact reference-cache equivalence, analytical bound
checks, search-count deduplication, panel reuse after other bot posts and panels
outside the history page. Legacy engine modes and offline study defaults remain
unchanged. Version: `fairplay-v61-all-live-periods`; SharkBot `2.33`.
