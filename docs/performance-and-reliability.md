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
