# v68 follow-up: visible delivery and published-Accuracy frequency

This focused follow-up preserves `fairplay-v68-evidence-integrity`, Method 3's
5,000-game limit and all scoring/coverage thresholds. Draft PR only; no production
restart, repository-variable change or deployment is part of this work.

## Incident findings

The previous owner diagnostic always used the legacy `VERSION` constant and the
literal model description “Stockfish 19 + Maia-3”, even for Method 3. Its phase
mapping omitted Method 3, chart rendering and final publication. A failed
Discord send after completed analysis was classified as
`review_coverage_or_data_gate`. Those fields therefore could not distinguish a
Method 3 publication failure from a legacy/data failure.

The reported workflow commit already selected v68 when `FAIRPLAY_METHOD3=1`.
Its optional Maia provisioning steps were skipped; their workflow conditions
establish that Method 3 was enabled in the job environment. The bot step uses
the same repository-variable expression. No evidence justified changing the
routing flags. Direct repository-variable reads returned HTTP 403, and the
running job's logs were unavailable through the log API. The original sanitized
failure record contains no underlying Discord status: **the exact original
HTTP failure cannot be recovered from that record**.

Two silent UI paths are reproduced by focused tests: there was no initial card
while the profile request ran, and a failed terminal embed edit could leave no
visible error. A checkpoint-suspension failure could also interrupt error
handling and terminate the queue worker.

## Repairs

- Attempt a starting card in the original permitted channel before starting
  analysis. A confirmed progress rejection aborts expensive work and attempts
  one plaintext explanation in that same channel.
- On failed terminal embed updates, use one plaintext fallback after a confirmed
  rejection. Do not blindly repost after an uncertain acknowledgement.
- A slash requester can receive a private interaction notice when channel error
  delivery cannot be confirmed. Resumed jobs have no interaction token and retain
  the existing owner-only diagnostic fallback. Discord permission/outage failures
  can still prevent delivery; the bot cannot bypass Discord permissions.
- Keep staff destinations, cancellation checks, final-message reconciliation and
  the existing no-duplicate report behavior. A failure to suspend a checkpoint
  no longer prevents the visible error path or kills the queue worker.
- Failure diagnostics now use the same active-version resolver as progress/UI,
  distinguish report/progress delivery errors and store bounded HTTP/status fields.
  Method 3 diagnostics contain no stale engine failure or engine-search claim.
  Raw exceptions, account names and game data remain excluded.

## Frequency chart and image budget

The new descriptive image reuses collected games and existing published-score
summaries. It shows cumulative >=80%, >=90% and >=95% counts and percentages,
available/missing scores, separate Bullet/Blitz/Rapid sections, and UTC calendar
buckets. Short histories use months; long histories combine months into at most
four readable buckets per class. Missing scores never enter a percentage's
observed denominator as zero.

Outlined buckets overlap the selected period's dates; the caption makes clear
that the bucket can include other games. An exact-control comparison shows the
selected games and up to 50 played games immediately before/after each displayed
control, preserving unavailable scores. At most the three most represented
selected controls are shown, with any omission disclosed. A LOW results window
used only as context is labelled as such.

The complete report has **at most six images**:

1. Up to three existing class overview pages.
2. One published-Accuracy frequency page.
3. The existing history-change page.
4. The existing selected-period zoom.

The standalone opponent-comparison and rolling expected-points pages are
replaced. Opponent information remains in class overviews; selected-period
expected/observed results remain in the zoom. This is explanatory output, not
a new independent signal or any change to HIGH/VERY HIGH.

## Verification and cost

The combined focused run passed **118 tests**. They cover actual Method 3 routing without engine/model execution,
private routing, immediate progress, confirmed/uncertain delivery failures,
cancellation, checkpoint exceptions, diagnostic privacy, graph denominators,
UTC boundaries, exact-control selection, missing values, six-image progress and
unchanged analysis results. Final counts and repository CI links are recorded
in the PR. The new image was rendered and visually inspected.

For the same 5,000-game synthetic chart input, three alternating measured runs
after a warm-up produced these chart-only results:

| Renderer | Images | Median | Range |
|---|---:|---:|---:|
| Previous v68 | 7 | 0.724 s | 0.713–0.727 s |
| Revised v68 | 6 | 0.697 s | 0.694–0.721 s |

The small difference is not a meaningful end-to-end speed claim. Scoring is
unchanged; collection, PGN parsing, scoring, queue/network and Discord time are
excluded from this comparison. No additional API calls or engine/model work
were added.

## Deliberately deferred

Opening/repertoire analysis, opening sacrifices and additional suspicion
features were not implemented. They are optional and would expand this focused
repair. No legacy engine rebuild, new calibration study or threshold tuning was
performed.
