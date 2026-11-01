# SharkBot Insights v1

`/insight username:...` is registered on the normal bot command tree and included
in the existing guild synchronization. Only the configured Chess Bot / Chess Bot 2
channel IDs from `shark_commands` are accepted; threads, DMs and Taskforce are
excluded. Navigation is restricted to the requester. No new engine analyses,
Fair Play scoring changes, checkpoint changes, tokens or premium cookies.

## Controls and pages

Defaults: rated blitz, all colors, last 365 days, UTC. Optional bullet, rapid,
daily; casual; white/black; 1–730 days; IANA timezone. Arbitrary start/end dates
and simultaneous multi-control reports are not implemented. End timestamps
are used for calendar statistics, explicitly labelled (important for Daily).

13 pages: Overview, Game Results, SharkBot Game Shapes, Game Phases, Opening
Performance, Opening Mastery, Tactics, Move Quality, Pieces, Castling, Calendar,
Geography, Trends & Opponents. One message, first/previous/next/last buttons,
section select, ephemeral Details. Controls expire after 30 minutes and are
then disabled. Rendered PNGs are 1400×2000. Charts display counts/percentages;
no hover is required. Monthly trends show the latest 13 months for longer
selections; the selection's full data still contributes to other summaries.

## Meaning and limits

- API `accuracies.white/black` are the only accuracy source; each metric uses
  only available scores, never zero-fills. Overall coverage is explicit.
- Per-move quality, piece/phase accuracy, Brilliant, missed tactical opportunity
  percentages and Similar Players are unavailable, not estimated. No hidden
  Game Review endpoint is accessed. PGN annotations are not official evidence.
- Game shapes are ordered: ≤15 full moves Short Game; ≥60 Long Battle;
  ≥8-pawn-unit material balance range Material Swing; final Endgame Endgame
  Battle; ≥8 checks Check-rich; otherwise Other Game. Promotions and captures
  affect material but are not engine quality measurements.
- Phase: total non-pawn material ≤20 points is Endgame; otherwise fullmove
  number ≤10 is Opening; otherwise Middlegame. N/B=3, R=5, Q=9.
- The existing pinned CC0 Lichess opening-family reference is reused read-only.
  Longest exact prefix identifies a family, not an exact variant/transposition.
  Reference mastery counts own moves while the full prefix is in that database.
  First missing prefix is a reference deviation, not proof of leaving all theory.
  No deviation means censored, not player never deviates. Nonstandard starts are
  excluded. Opening examples are observed sequences, not canonical definitions.
- Tactics: after an own move, geometric attacks on ≥2 enemy non-pawns count as
  forks; newly absolute-pinned enemy pieces count as new pins. Undefended
  captures have no opposing geometric defender before capture, excluding en
  passant. Actual checkmates are counted. None implies a winning tactic or
  missed chance. Piece moves include only this player; promotion is a pawn move
  and castling a king move.
- Geography uses public profile country codes, not verified location. At most
  100 most frequent opponents are queried; unavailable, unknown, international
  and not-fetched profiles remain distinct. Chart shows top 16 categories.
- Previous period is the immediately preceding, nonoverlapping equal duration;
  sample sizes and wins are shown, not claimed comparable player populations.

## Collection, bounds and recovery

Official Published Data API: https://api.chess.com/pub/player/{username}/games/archives,
monthly game URLs, /player/{opponent}. Documentation:
https://support.chess.com/en/articles/9650547-published-data-api

Sequential requests, 5s connect /25s read timeout, three attempts, bounded
backoff and Retry-After on 429/5xx. At most 60 archives and 10,000 eligible game
parses across both periods; selection is newest first. Counts distinguish
filtered, invalid, duplicate, missing-date and unavailable archives. Invalid
PGNs, illegal histories, mismatched users/results and variants are rejected.

One Insights job per process; concurrent/duplicate requests get a clear retry
message. Work is offloaded from the Discord loop. Real archive counts are
reported every eight seconds. Reports cache four parameter sets for 15 minutes;
view caches at most three PNGs. API JSON cache is process-local filesystem,
1h archive /24h profile TTL, seven-day /150MB eviction on next collection.

A separate temporary job journal marks interruptions on restart **if the same
filesystem survives**. A fresh GitHub runner cannot restore this cache or job
journal: the initial progress message explicitly instructs rerunning after a
restart. This is not durable distributed resumption. Collection is deterministic
and restartable from surviving archives; no dependency on the Fair Play ledger.
Persistent Discord controls across bot restarts are not claimed.

## Validation

`python -m unittest -q test_shark_insights test_shark_commands test_shark_dispatch`
Offline PGN, missing data, collection filters, retries/cache, channel gate,
command registration, page replacement, job failure/cache and restart tests.

Live public API benchmark on 2026-11-01: 24 monthly archives, 7,780 entries,
425 current rated blitz games +4 previous-period games. No invalid PGNs or
failed archives. 113/425 published accuracy scores. 125 requests including
100 opponent profiles. Collection/processing 31.22s; twelve initial PNGs made
total 32.66s. Final thirteen-page rendering separately 1.61s. These are local
measurements, not a Discord deployment or runtime guarantee. PNG layouts were
inspected locally. Private account data/results are not committed.

Deployment is intentionally left to the PR reviewer. Live Discord command
availability is unverified until merged and the normal worker is restarted.
