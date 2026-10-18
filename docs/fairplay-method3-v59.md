# Method 3 — v59: 1,000-game history

`FAIRPLAY_METHOD3=1` now collects up to the latest **1,000 unique eligible rated
standard live games** (Rapid/Blitz/Bullet), newest archives first. Variants,
unrated games and duplicates do not consume the quota. Smaller accounts use
what is available. The fixed Published Data API, five-minute collection deadline,
request pacing/retries, cancellation and one-review queue remain in place.
No Stockfish, Maia, sparse probes or invented Accuracy are added.

## History and current priority are separate

All collected games appear in the class charts and opponent comparison. The
statistical adapter also retains all 1,000 games, rather than silently clipping
them back to 200. Only periods wholly inside the **latest 200 games overall**
can determine the current priority. Fixed chronological windows of 10, 20, 50,
100 and up to 200 games remain separate. Old normal games never dilute a recent
period by averaging account-wide. Older hot streaks do not create hundreds of
additional opportunities for a current HIGH.

Official Accuracy comparisons can use strictly earlier same-control,
player/opponent-rating-matched reference games anywhere in the collected history.
Reference games are never labelled clean/non-cheating. Missing scores stay
unknown. Results thresholds are unchanged; an additional fixed 100-game width
is evaluated alongside the existing widths. Windows are correlated and are
not counted as independent confirmations. These development rules remain
uncalibrated screening heuristics, not misconduct probabilities.

## Descriptive behaviour change

For each exact class/time control, compare its latest 10–20 games in the priority
sample with up to 200 strictly earlier rating-matched games. At least 20 valid
prior games and 80% result coverage are needed for a comparable result change.
Show score above Elo expectation, published Accuracy medians/availability and
medians of per-game think times/availability. A descriptive result increase
requires a rise of at least 15 percentage points and recent score excess of at
least 20 points; it is **support-only**, never an independent HIGH vote. Missing
clocks/Accuracy are not zeros. Rating lag, training and opponent changes remain
innocent alternatives. Timing does not change priority; VERY HIGH is unavailable.

Charts mark where the recent priority sample begins, and a separate PNG compares
earlier/recent behaviour. The short report states history size, priority sample
size and published Accuracy coverage. Details retain the selected chronological
period. New detector/footer: `fairplay-v59-method3-history-1000` (bot 2.31).

Collection, PGN parsing, comparisons and rendering remain off the Discord event
loop. More archives/PGNs can increase latency; no five-times-speed estimate is
assumed. Runtime is benchmarked separately from label validation. Status/closure
metadata never enters these calculations. Existing engine modes keep their
previous limits; 1,000-game collection is an explicit metadata-only opt-in.
