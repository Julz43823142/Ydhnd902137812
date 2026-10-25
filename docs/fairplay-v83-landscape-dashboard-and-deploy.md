# Fair Play Method 3 v83 — readable landscape dashboards and deployment

## Root cause (actual images, not just pixel headers)

v82 forced all nine SVG-derived PNGs to **1800px wide** by changing horizontal
coordinates, but did not change **height**. The page 7 opening / ratings image
remained **1800 × 2980** (width/height = 0.60). Discord displays a tall image
at a narrow visible preview size, and opening / rating graph labels became
hard to read despite the nominal width. The same problem affected the 2020px
high five-panel time-pressure page and, depending on selected control
comparisons, the Accuracy-frequency page.

## v83 layout

- **Chart 7**: White and Black complete chronological opening frequencies
  appear side-by-side, with all 20 chronological bars and every legend entry
  retained. Selected periods still identify actual overlapping games. Matched
  before/during/after repertoire evidence remains full width. Bullet, Blitz
  and Rapid rated-Elo histories use three side-by-side panels with compact
  but complete first/last Elo, signed delta, sample size and range.
  Nominal image is **1800 × 1495** rather than 1800 × 2980.
- **Chart 9**: The five fixed-control chronological phase/time-pressure graphs
  use a **2 / 2 / 1** layout instead of five stacked panels. Date axes,
  independent y scales, intermediate 50% bands, verified time-pressure
  comparison, game counts and sparse gaps are preserved.
  Nominal image is **1800 × 1475** rather than 1800 × 2020.
- **Chart 4**: The three time-class Accuracy/coverage histories use two plots
  side-by-side and one full-width plot beneath. Exact-control
  before/during/after frequencies remain full-width and include original
  observed/missing denominators. Height is reduced by **420px**.
- **Charts 1–3, 5, 6, 8** retain their proven original geometry at 1800px.
- **Every image is now landscape (width > height)** under the supported
  selected-control comparison limit, and numbered 1–9. No image is squeezed
  by rescaling font sizes or vertical graph axes; shapes are translated into
  separate dashboard panels and x-coordinates use the available column width.
- Opening and timing legends now expose **actual collected game counts** and
  **valid/total clock coverage** for the shown exact time control, rather
  than forcing readers to guess whether missing series mean zero.
- Real CairoSVG→PNG output for charts 7 and 9, SVG geometry, all nine
  full report images, data element counts and text preservation are tested.

## Review-engine audit / runtime

- No modifications to safeguards, Accuracy thresholds, Method 3 results
  classification, fair-play bans, data fetching, PGN parsing, or chart
  scoring. v82's removal of standalone uncalibrated absolute Accuracy
  promotion is unchanged. **No named account is whitelisted.**
- The Method 3 observations function previously sorted up to 5000 collected
  games *twice* using the same key; v83 reuses the first sorted result.
  This avoids redundant O(n log n) work with identical plotted values and
  order.
- Descriptive PGN clock reconstruction now uses a bounded 128-entry cache of
  the pure time-control parser instead of parsing the same `180+2` /
  `60+0` value on every eligible move. The exact conversion and all
  existing clock-validity gates are unchanged; unit tests compare resulting
  semantics and assert one parse per distinct control.
- Discord report delivery previously caught every Method 3 graph-rendering error
  and silently replaced all chart attachments with an empty list. v83 still
  delivers the review card if charts fail but logs the exception and
  traceback to GitHub Actions, enabling diagnosis of missing images.
- No quantitative runtime gain is claimed without a benchmark.

## Deploy trade-off

The Daily Puzzle workflow previously had `cancel-in-progress: false` for
**all** triggers. A Fair Play code push to `main` could leave an old active bot
running for hours, while the new one waited. v83 uses push-only
`cancel-in-progress: ${{ github.event_name == 'push' }}` in the existing
`daily-puzzle` concurrency group:

- A new push to main that touches the Daily Puzzle dependency paths cancels
  the previous Daily Puzzle run and starts one fresh controller with the new
  SHA. Discord receives at most one Daily Puzzle bot controller.
- **Scheduled/dispatch rotations still use graceful mode**, with their
  state-synced self-handoff and `cancel-in-progress: false` on those triggers.
- **Important trade-off:** a push cancelling a runner can interrupt an active
  Fair Play job and may lose uncheckpointed progress. Existing checkpoints
  and periodic state synchronization reduce this risk but do NOT guarantee
  zero loss. This deploy policy is used only because immediate replacement
  of the outdated bot run was explicitly requested.
- Independent Survival, Minigames, Guess Chatter and YouTube Notification
  controllers are *not* killed as collateral damage.

## Validation and sign-off

v83 bump: `fairplay-v83-landscape-and-runtime`. GitHub CI must pass before
merge. Verify new Daily Puzzle run after merge against the exact merge SHA,
plus the previous Daily Puzzle run status. Do not claim completion of the
rollout unless GitHub reports it.
