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

Board edits with new images use Discord's partial-message API: one EDIT request rather than GET + EDIT. Text-only feedback references the retained board by `attachment://<filename>`, so Discord does not show that file again as a standalone image above the embed. Its filename is stored per channel; legacy cards discover it once. Move-to-bottom cleanup uses DELETE rather than GET + DELETE. Missing messages repost with a fresh attachment stream; temporary edit failures do not post duplicates. Per-channel card locks serialize send/ID/delete operations so overlapping moves do not leave orphan boards. Daily mirroring runs after releasing the source lock to avoid cross-channel deadlocks.

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

Additional regressions cover sparse row IDs, pool replacement, six-band rotation, concurrent RP starts, fresh attachment streams after 404s, temporary Discord failures, retained attachment references, overlapping move cleanup, render invalidation, overlapping cosmetic/engine work, failed Survival fetches and review/live-engine lock isolation.

## Completion stats and rewards

A correct final move immediately moves one solved result card to the bottom. It displays **Puzzle solved!**, without an “Updating stats and rewards” placeholder. The confirmed points, ranking, solution, Puzzle Elo and applicable bonuses/achievements/quests edit that same card without another board render or upload. “Puzzle solved” appears once.

The completion worker reuses the existing stats, point/coin, achievement, first-solve, quest, community and pet writers inside one opt-in `repository_transaction` group. Staged reads/writes are visible only to that worker thread, under the existing repository lock. Ordinary writes in other modes retain their strict fetch/push/verification protocol. The outer group starts from one fresh immutable Git base, publishes all files in one ordinary fast-forward push, then fetches and verifies an immutable completion receipt. Existing individual audit IDs are retained.

Conflicting remote updates rebuild the group from the latest base. Lost acknowledgements are resolved by the receipt before replaying any reward. Failed builds restore display caches and never write local stats files or expose tentative balances. A failed confirmation leaves **Retry Rewards** on the public card; its persistent button remains bound to the original solver after restart, regardless of who clicks it. Exact-rating training remains reward/stat-free and does not create an unnecessary completion commit.

### Completion benchmark

```sh
python benchmark_puzzle_completion.py --baseline f47d623b6693c3374f2da239bc0025cb4cebfe4d
```

Both implementations run their real writers against disposable local Git repositories. The scenario uses an existing player with an active pet. A controlled 200 ms delay is added to each actual Git fetch/push; background Daily-state sync and Discord delivery are excluded. Medians of three runs:

| Completion work | Before | After |
| --- | ---: | ---: |
| Stats and rewards | 5.034 s | 0.710 s |
| Fetch/push requests | 23 | 3 |
| Pushes / commits | 7 | 1 |

This is **7.09× faster in the controlled benchmark**, not a measured live Discord guarantee. The script never starts a bot or pushes production state/GitHub. Remaining live latency includes Discord requests, lock waits, GitHub round trips and contention/retries.

Regression coverage verifies one completion commit, wallet/stat/quest/community/pet outcomes, concurrent duplicate workers, uncertain acknowledgements, conflict preservation, cache/local-file rollback, quest payouts plus paid markers, rated/exact practice rules, one final board upload, persistent retry target binding and optional capture markers with mandatory disambiguation.
