> Current v41 changes are documented in [fairplay-v41.md](fairplay-v41.md).
> The original v40 design below is retained as historical context.

# Daily / correspondence review (v40)

Completed standard Daily games now enter the existing collection, bounded
primary engine screen, local Maia inference and distributed depth-18 review.
Their fixed Chess.com daily game links are preserved. Variants remain excluded.
All finished Daily games, including short games, retain their chronological
place; opening-only games cannot contribute difficult-move evidence.

Daily clocks are not reconstructed. The correspondence time control is
normalized separately (`1/86400`, for example); missing/invalid controls cannot
qualify the Daily HIGH route. Daily games never enter live timing, live-human
policy HIGH, live chronological-half HIGH or published-Accuracy surge routes.
Maia inference is descriptive for Daily because the live-trained reference
has not been calibrated for correspondence play.

Up to 30 recent rated Daily games are reserved within the existing deep-game
capacity, after the latest-fifty coverage guarantees. This prevents large
Bullet volumes from completely displacing the correspondence comparison set.
The primary/deep capacity is unchanged. Daily games use depth 18, not Bullet's
reduced-depth setting.

`fairplay_daily.py` compares contiguous five-game Daily completion windows
with up to 30 prior same-control Daily games at comparable player/opponent
ratings (within 200/250 Elo). Original class indices protect against gaps in
selected engine coverage. All qualifying opportunities include misses, with a
chronological 12-opportunity cap per game. An opportunity must be useful,
competitive, critical, difficulty >= .65, non-book, non-forced, non-conversion,
non-automatic-material-gain, and completed at depth 18.

The initial Daily HIGH route requires:

- every candidate game supplies at least five opportunities and >=85% near-best decisions;
- at least 40 total opportunities;
- the one-sided 95% Wilson quality lower bound is >= .75;
- >=95% measured paired fast/deep coverage in both groups;
- >=75% objective-quality preservation, separately in every candidate game and the baseline;
- at least ten prior Daily reference games with >=40 opportunities;
- the prior group's Wilson upper bound is <= .55;
- the median per-game near-best rate improves by >= .30.

These are transparent heuristic review thresholds, not calibrated misconduct
probabilities. The route caps at HIGH with MEDIUM confidence. Completion order
is not proof of independent play sessions: correspondence games can overlap.
Study resources and long deliberation must be considered during human review.
Missing baseline evidence is not a claim of fair play. Continuous assistance
without a historical change remains a limitation of this initial Daily route.

The report exposes Daily collection/deep counts, limits and individual route
PASS/FAIL gates. Runtime evidence remains private; no subject usernames or
closure labels are stored in this document or production scoring code.
