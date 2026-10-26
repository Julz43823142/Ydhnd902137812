# Fair Play v87 — Review reliability

This release improves the live English Fair Play Review without adding extra prose to the Discord report.

## Changes
- The first review card now uses the field name **Review Index**. The parenthetical `(not a %)` is removed, as requested. The index remains an uncalibrated manual-review indicator, not a misconduct probability.
- All Method 3 Discord cards now enforce the individual field-name/field-value limits and a conservative total-embed limit. If diagnostic output is oversized, per-opponent descriptive rows are removed first, preserving the human-review disclaimer.
- The **Review Gates** card reserves space for its evidence-dependence warning even when many guard counters are present. This fixes a potential HTTP 400 on an overlong field.
- The temporary opening-switch field uses an actual newline rather than the literal characters `\\n`.
- Invalid numeric observations (NaN, positive/negative infinity, booleans and nonnumeric values) render as **Unknown**. Invalid historical Elo observations cannot authenticate a rated time-control baseline, and invalid score excesses do not create threshold-gap text.
- Empty review explanations show a valid, short fallback instead of sending an empty Discord field.
- Method 3's version marker is `fairplay-v87-review-reliability`. The legacy engine route is not renamed.

## Unchanged
No rating thresholds, statistical review priority cutoffs, game collection limits, model/Stockfish work, scan deadlines, or the nine-chart order change. No automatic sanctions are added. Synthetic regressions and green CI are **not** empirical validation of cheating precision/recall.

## Tests
Regressions cover the plain Review Index label, correct method version routing, many long opponent labels, too many guard counters, real opening-switch line breaks, empty explanations and non-finite rating/scoring values. Existing Fair Play tests and GitHub Actions must pass before a controlled deployment.

## Rollout
Keep the PR draft until its CI is reviewed; do not merge or restart the active Daily Puzzle / Fair Play controllers automatically.
