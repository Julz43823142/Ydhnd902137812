# Fair Play Task Force

The existing Daily SharkBot process serves channel **1311445685492781186**.
It is not in `CHESS_CHANNEL_IDS`. No additional token/application is used.
The public Discord review is a screening aid for moderators, never a verdict
or probability of misconduct. Every public card and detail view carries the
screening disclaimer. No ban, report, role, reward or wallet APIs are called.

## Architecture and navigation

- `fairplay_config.py`: versioned thresholds and runtime budgets.
- `fairplay_data.py`: serial PubAPI client, bounded PGN collection and clocks.
- `fairplay_analysis.py`: independent engine, two passes, statistics and gates.
- `fairplay_ui.py`: single idle panel, modal, progress/report cards and queue.
- `fairplay_routing.py`: pre-dispatch channel quarantine in the existing client.
- `bot.py`: startup, human-activity routing and optional `/fairplay username:`.
- `chess_play.py`: backwards-compatible discovery / engine-class options.
- `minigames.py`: its channel worker ignores Fair Play interactions.

After one hour without human activity, the single **Submit Chess.com Account**
panel becomes the last visible message. Existing panels are deleted before
moving it; if deletion fails, a replacement is not posted. If already last,
it is retained and duplicates in recent history are removed. Startup streams
Discord history until the latest panel is found, then restores recent human /
submission timestamps with a restart grace period. This runs separately from
normal chess idle state. Fair Play button/modal interactions reset its timer.
Completing a report does not immediately post another panel.

Report buttons: **Highest-Signal Games**, **Timing**, **Performance**,
**Engine Analysis**, **Chess.com Profile**, **Re-scan**. Detail views are private.
Persistent submit/report views are restored at startup. Detail caches expire
after two hours or restart; the public card remains in Discord and Re-scan can
rebuild details. An outstanding modal lost during restart asks the user to
reopen the submission form. Re-scan reuses concurrent work / a 30-minute cache.

The quarantine runs before the gateway parser invokes command, view or modal
handlers. `on_interaction` alone cannot prevent side effects: discord.py has
already dispatched callbacks by then. The adapter is covered by tests against
the repository's pinned discord.py 2.7.1; review it when upgrading Discord.
Only `/fairplay` and `shark:fairplay:*` are admitted in this channel. Outside
the channel, Fair Play commands/buttons refuse submission privately. Existing
interactions in every other channel pass through unchanged.

## Eligibility and collection

Only constructed `https://api.chess.com/pub/player/{validated-username}`
endpoints are fetched; usernames are case-folded, 2–25 characters and limited
to alphanumeric/underscore/hyphen. URLs, paths and query strings are rejected.
API archive links are parsed only for matching username/year/month, not fetched
as supplied URLs. Redirects are disabled. Public profile identity is checked.

Requests are serial, at least 0.5 seconds apart, with connect/read timeouts,
three transient retries and Retry-After backoff (numeric or HTTP-date). A wait
over 30 seconds stops the request with a retry-later message instead of sending
another request before Chess.com's indicated time. Responses are bounded
(1 MB profile/index, 16 MB archive). Up to 36 monthly archives are visited,
newest first. Completion time, not API array order, selects the latest **100**
eligible games, then analysis uses chronological order. UUID/URL deduplication
prevents overlap double-counting. Partial archive coverage is disclosed.

Eligible games are completed standard live Rapid/Blitz/Bullet games with valid
PGN, a matching player/result, standard initial position, 24–600 plies and at
least eight structurally useful subject decisions. Daily, variants, abandoned,
extremely short, custom-start, malformed and oversized PGNs are excluded.
python-chess parsing errors are collected quietly, not logged with headers.
One malformed game never aborts the rest. Rapid/Blitz/Bullet stats stay separate.

## Move and engine features

Only the subject's decisions are analyzed. Each initially records ply/fullmove,
FEN, actual move, clocks, estimated time, legal choices, phase, check/capture,
whether it gives check, and structural usefulness. First **20 plies**, positions
with ≤2 legal moves, check responses and immediate recaptures are excluded.
This intentionally sacrifices sensitivity to protect against forced/theory
false positives. Scores beyond ±600 CP are also weak evidence and excluded
from statistical aggregates.

Fast pass: **8,000 nodes**, MultiPV 3, on every useful decision until deadline.
Actual moves outside the top three are evaluated by a separate root-restricted
search with the same budget. Both scores use player POV. Mate scores normalize
to ±10,000 CP adjusted by mate distance; CPL is nonnegative, capped at 1,000.
Hash is cleared per position/search, and one thread is used for reproducibility.

Deep pass: top **10** review-interest games at **64,000 nodes**, MultiPV 3.
Interest ranks critical-hit evidence, generic precision and repeated cadence.
Deep results replace a game's fast data only after its complete deep pass;
a timed-out deep search cannot leave mixed/shallow confirmation markers.

Critical positions require ≥6 legal choices, best–second gap ≥100 CP,
top-three spread ≥180 CP, and a quiet non-check/non-capture useful decision.
Unique-best uses gap ≥180 CP. Aggregates include top-1/top-3, median/p90 CPL,
trimmed robust CPL (lower 90%), losses ≥100/200 CP, critical CPL/mistakes,
unique-best hits and consecutive critical-opportunity hits. This is a
reproducible heuristic, not a validated scientific definition of difficulty.
Chess.com accuracy is optional context and never an input to review priority.

## Timing and performance methodology

Clock reconstruction: previous clock on that side + increment − new clock.
The first move has no invented baseline. Missing clock data breaks the chain;
unsupported time controls and negative/impossible values remain unavailable.
Only non-opening useful decisions with >0.5 and <120 seconds think time and
remaining time >max(10 seconds, 5% of base time) contribute.

Cadence measures a modal ±1-second band, coefficient of variation and
two-second-bin entropy. An elevated cadence requires ≥15 usable moves,
≥80% in the band and CV <0.30. Also shown: ordinary-vs-critical median time,
gap–time correlation, critical-hit cadence and a within-game variable-to-regular
timing/quality shift. No particular number of seconds is itself suspicious.
Recurrence in multiple games is required for the timing family. Bullet evidence
is weighted **0.35** and its clock reliability remains low; lag, rounding and
premoves are explicit alternatives.

Performance change compares adjacent chronological **10-game windows within
each time class**. A sustained elevated change requires CPL reduction ≥35 CP
and ≥3 pooled MADs (MAD floor 10), later robust CPL ≤20, top-1 gain ≥0.20,
≥8/10 consistently precise later games, plus blunder-rate improvement ≥0.03
or critical-hit gain ≥0.25 with sufficient critical games. A single good game
does not pass. Win/loss contrast needs ≥8 of each, median win CPL ≤15,
loss CPL ≥70 and at least eight clean wins. Draw quality is reported separately.

Rated results use the Elo expected-score formula. A standardized excess is
reported only with ≥20 rated games; rating gain/volatility need ≥10 ratings.
Account age, win streaks, results against ≥200-point stronger opponents and
rolling expected-score excess are context. Missing ratings do not create zeros
that are then interpreted as anomalies.

## Exact priority model and protections

Families use heuristic 0–1 scores, **not cheating probabilities**:

| Family | Weight | Score criteria |
| --- | --- | --- |
| Engine | 0.30 | In a class with ≥10 games / 200 decisions: 0.85 for top-1 ≥90%, top-3 ≥98%, p90 CPL ≤30; 0.55 for top-1 ≥80% and median CPL ≤15; otherwise 0.10. Without coverage: 0. |
| Critical | 0.30 | ≥10 games / 30 critical opportunities: 0.95 for ≥90% hits and ≥8 strong critical games; 0.70 for ≥80% and ≥5; 0.40 for ≥65%; otherwise 0.10. A strong critical game has ≥3 opportunities and ≥80% hits. Without coverage: 0. |
| Timing | 0.15 | Recurring cadence/critical cadence/regime changes: 0.50 for ≥5 games, 0.75 for ≥10. Without coverage: 0. |
| Performance | 0.15 | Sustained change: 0.85; qualifying win/loss contrast: at least 0.55. Bullet change/contrast: 0.30/0.20. |
| Context | 0.10 | Age <30 days: 0.10; ≥300-point sample rating gain: 0.35; rating-result excess ≥3 standard deviations: 0.55. |

Engine/critical/timing/context take the highest qualifying class score; Bullet
class scores receive 0.35 weight. Titled players reduce engine/critical/context
scores by 0.65/0.85/0.50 respectively. Ratings, age and accuracy cannot substitute
for meaningful engine evidence. These thresholds need human interpretation;
strong, underrated, returning, improving or alternate-account players can
produce notable metrics without misconduct.

- Fewer than **15 meaningful games or 150 decisions**: INSUFFICIENT DATA.
- MODERATE: weighted score ≥0.30 or either precision family ≥0.65.
- HIGH: weighted score ≥0.58, engine and critical scores both ≥0.65,
  ≥30 critical opportunities, another family ≥0.50, successful deep confirmation
  and confidence above LOW. Otherwise cap at MODERATE/LOW.
- VERY HIGH: weighted score ≥0.78, engine ≥0.80, critical ≥0.85,
  another family ≥0.50, ≥30 games / 500 decisions / 60 critical opportunities,
  deep confirmation, HIGH confidence and a complete scan. Otherwise capped.
- Deep confirmation: ≥60 deep decisions / 20 deep critical positions,
  top-1 and critical top-1 ≥85%, deep median CPL ≤15.

Confidence is separate: HIGH needs ≥30 meaningful games / 500 decisions /
30 critical opportunities, ≥60 deep decisions, ≥10 games with usable clocks and
a complete scan. MEDIUM needs ≥15 games / 150 decisions. Otherwise LOW.
Unknown data never increases a family. Timing, account age, rating gain and
generic high agreement alone can never produce HIGH or VERY HIGH.

## Runtime, privacy and environment

One dedicated single-worker executor and one waiting slot; duplicate targets
reuse existing work. The entire fetch/parse/engine/statistics pipeline runs off
Discord's asyncio loop. One separate full-strength Stockfish process per scan,
**Threads=1 / Hash=64 MB**, best-effort OS priority 10, bounded engine commands
and clean quit/close. It uses shared discovery but not the normal chess engine
or its analysis lock. No engine download/build is started by a submission.
The node-budget engine explicitly applies an eight-second command timeout;
python-chess otherwise disables SimpleEngine's timeout for node-only searches.
Reviews wait at most five seconds behind the shared startup/discovery lock.
The review deadline is **600 seconds**; partial evidence is labeled and cannot
be VERY HIGH. Bounded network/engine commands and cleanup can extend elapsed
time slightly beyond the deadline. Cancellation interrupts backoff and terminates the engine.

Progress edits are throttled to three-second intervals, always on the same
card. Deleted cards are not recreated. An uncertain send acknowledgement is
reconciled through a random public Review ID and never blindly reposted.

Runtime engine cache is limited to 200 games, keyed by game/side/engine/version/
full analysis configuration, with one-hour expiry. UI summaries are capped at 50 reports (two-hour
expiry) and 20 recent accounts (30 minutes); position/FEN arrays are discarded
before UI storage. No case/report/username files are written or committed.
Discord is the public history. Logs contain no targets or reports. Analytics
count only generic **Fair Play Scans**, never the account.

Required: existing `DISCORD_TOKEN`, permissions to view/history/send/edit/delete
the bot's own messages in the target channel, outbound HTTPS to Chess.com
PubAPI, existing python-chess/requests/discord.py dependencies, and a compatible
**Stockfish 19+** binary discovered by `chess_play.py` / `STOCKFISH_PATH`.
No new secrets or dependencies. Missing/crashed Stockfish produces a clear
failure without a priority. The development tests mock the engine/API and do
not establish classifier accuracy or end-to-end production engine readiness.

## Daily Puzzle change

Automatic Daily Puzzle checking/posting, expiration and mirroring now apply
only to **ChessBot 1 (1468320170891022417)**. Secondary Daily pointers no longer
select the parser. On startup, only the tracked bot-authored unsolved Daily
card in ChessBot 2 is retired; completed history, RP/Practice, Daily Chess,
normal chess and both original chess channel IDs are retained.

## Validation

Synthetic PGNs/fake PubAPI and engines cover ordering/filtering, player POV,
clocks/increments/missing chains, forced/theory exclusion, mate scores/CPL,
critical positions, sustained changes, small samples and conservative gates.
Async tests cover routing before side effects, persistent views, idle movement,
duplicate/full queues, progress acknowledgement recovery, missing accounts,
deleted messages, responsive Discord loop and shutdown. Existing root and
minigame suites, Python compile, JSON/YAML and diff checks also run.
