# Interaction performance and recovery

Display reads across shared wallets, puzzle stats, Guess stats, pets and community pages share one successful Git refresh for up to two seconds. Each read still loads the current origin ref. Cache scope includes checkout and branch. Failed refreshes are never cached. Purchases, transfers, rewards, pet mutations and their confirmation fetches always retain the existing fresh-fetch/retry protocol.

Guess event archives are cached by their immutable Git tree, with at most two trees retained and copies returned to callers. Unrelated commits reuse the archive; new events invalidate it immediately. Offline leaderboard reads use the last fetched immutable events rather than discarding points in favour of the legacy baseline.

Leaderboard formatting runs off the Discord event loop after acknowledgement. Profile navigation/equip, trade decisions, donation submissions and the affected shop/menu controls also acknowledge before database work. Existing public/private visibility is preserved. Encrypted Guess chat loading runs in a worker thread.

Pet and accessory PNG bytes are cached by complete SVG content (64 entries). Changing XP, care display, species/evolution or accessory changes the content and renders a new image. Every Discord attachment gets a fresh file stream. Living pet collections paginate in groups of 25 to respect Discord limits; public navigation stays bound to the viewed player.

## Local measurements

Measured against already-fetched Git data in the development environment, with no production writes. Repeated timings are medians of 20 calls.

| Operation | First call | Repeated call |
| --- | ---: | ---: |
| Read 569 immutable Guess events | 21.18 ms | 3.42 ms |
| Render an unchanged pet card | 88.55 ms | 0.03 ms |

These measure local processing only. Discord delivery and GitHub network/transaction verification still take time; first requests after the display refresh window still perform network I/O.

Regression tests cover concurrent refresh sharing, expiry and checkout/branch scope, uncached fetch failures, strict transaction verification, immediate visibility of confirmed wallet changes, event-tree invalidation, offline scores, event-loop responsiveness, callback acknowledgements, larger pet collections, public target binding and render cache invalidation.

## Random Puzzle and chess follow-up

`!r` / Random Puzzle now selects a random real row ID from a compact, read-only per-band index, then reads that indexed row. The six-band shuffle, boss band, recent-puzzle avoidance, legality checks and ratings remain intact. Indexes warm in the background at startup and invalidate if the pool is rebuilt/replaced. No schema or puzzle data is rewritten.

Random Puzzle starts acquire their channel lock before checking Survival or closing the previous puzzle. Prefix commands, buttons and slash commands share one guard. Survival display reads reuse the existing successful two-second Git refresh, with failed fetches still blocking puzzle handling. Closing a puzzle uses the existing coalescing save worker rather than starting a competing raw Git push.

Random/daily boards, live chess, Game Review, Survival, Pet Puzzle, Guess chess browsing and chess shop previews reuse the bounded, full-SVG PNG byte cache. New moves, check highlights, orientation, cosmetics and arrows are part of the image key. Changed positions still render normally; each attachment has its own file stream.

Board edits with new images use Discord's partial-message API: one EDIT request rather than GET + EDIT. Text-only puzzle feedback can also use one EDIT, retaining the existing public image URL for up to five minutes in a bounded 128-message in-memory cache. Older cards/restarts fetch the current attachment URL. Move-to-bottom cleanup uses DELETE rather than GET + DELETE. Missing messages repost with a fresh attachment stream; temporary edit failures do not post duplicates.

Game Reviews use a separate lazily started Stockfish process/lock, so a long review cannot monopolize the live-move engine. Default additional review-engine resources are one thread and 64 MiB hash (existing environment overrides still apply). Live bot moves keep their existing Elo and thinking time; cosmetic lookup runs concurrently with engine thinking and is reused for the resulting board.

### Reproducible local benchmark

Run from the repository root:

```sh
python benchmark_puzzle_speed.py --baseline e6deffd9a202ea015a9bd7a5e590ff9438c0f626
```

This reads the real SQLite pool without writing it, evaluates the three previous functions from the trusted Git revision, and compares medians in the same process. Measurements from October 4, 2026:

| Local operation | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| RP selection (50,000 puzzles in band) | 2.294 ms | 0.284 ms | 8.08× |
| Repeated puzzle board | 49.046 ms | 1.475 ms | 33.24× |
| Repeated chess board | 47.259 ms | 1.409 ms | 33.55× |

Selection uses 60 calls; image timings use 15 calls after rendering the unchanged position once. These are local processing measurements, not total Discord command latency. New positions still incur first-render cost, and GitHub/Discord network latency varies. The script does not start a bot, fetch GitHub, read a wallet, or alter production data.

Additional regressions cover sparse row IDs, pool replacement, six-band rotation, concurrent RP starts, fresh attachment streams after 404s, temporary Discord failures, attachment-URL expiry, render invalidation, overlapping cosmetic/engine work, failed Survival fetches and review/live-engine lock isolation.
