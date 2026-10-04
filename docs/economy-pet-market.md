# Economy & Pet Market

The existing Trade menu in both workers now includes Wanted Market, Pet Shelter and Trade Receipts. Pet Cards provide Trade (prefills the existing Open Trade modal), Owner History, Shelter surrender confirmation, and a market-value detail. Event Hub and Community pages provide Next Event using the existing holiday calendar.

## Transactions and recovery

Shelter and Wanted actions use `market_transactions.py`, the shared repository lock, freshly fetched immutable Git snapshots, atomic snapshot commits, immutable `market_events/` receipts and bounded conflict retries. A lost push acknowledgement is verified by the receipt before returning. Wanted fulfilment has one stable transaction ID per listing, so concurrent sellers cannot earn twice. Existing direct/open trades keep their existing receipt IDs, locks and checks.

Surrender costs exactly 15 coins and requires explicit confirmation. Adoption costs exactly 10 coins. Both fees are destroyed; no player receives them. All living-pet capacity checks happen inside settlement against current data. Dead pets and pets with running/unclaimed expeditions cannot move. Accessories are unequipped and unlocks remain with the previous owner. Shelter pets are removed from their owner's collection, and that owner cannot adopt their own current Shelter entry.

Shelter rows record the surrender time. Adoption shifts `fed_at` and `happy_at` by the Shelter duration, preserving the remaining feeding time and happiness. Birth date, ID, species, rarity, name, XP, daily-care fields and progression remain intact. Shelter never awards XP or advances evolution, and eggs remain visually and textually hidden. The active-pet fallback follows the existing expiration helper.

Wanted listings expire after seven days and are excluded from browsing/acceptance after expiry. Makers can cancel active listings. A maker has at most five active listings. Species is required; rarity and evolution are optional exact filters. No coins are reserved. Settlement rechecks buyer balance, matching pet, ownership, expedition and capacity; insufficient funds durably invalidate the listing. Stale, cancelled, expired and mismatching offers cannot settle.

## History, receipts and pricing

Bootstrap initializes existing pet history as **Tracking started** with the current recorded owner, without reconstructing an unknown past. New eggs record Created, hatching records Hatched, and ordinary trades, Wanted sales, surrender and adoption append ownership events. History is carried with the pet, never reset on transfer. Public history pages remain bound to the selected pet/user; the Shelter browser also provides its selected pet's history.

Successful direct trades replace their existing message with a gold receipt embed. Successful public open listings become that same receipt card. Inbox and Wanted settlements show receipt details in their existing flow. Restart reconciliation restores receipts from the existing technical audit. `economy_analytics.json` indexes each successful immutable receipt exactly once; refreshing a view does not create a second economic event.

Market value uses completed pet-for-coins player trades and Wanted sales observed since this release, within 30 days. Three sales are required. A species/evolution bucket is used when it has at least three sales; otherwise pricing falls back to the species median. Shelter adoption, pet swaps, badges and donations do not contribute. Egg coin sales are counted as Mysterious Egg without recording hidden species/rarity in public analytics; they do not price a revealed species. History and value details use stored data; they never change inventory or equip state.

## Weekly economy coverage

Primary-bot startup runs a verified bootstrap, then checks once per minute for the previous completed Amsterdam ISO week (Monday–Sunday), using the same week key as community progress. One report is frozen in a durable outbox before publication to the primary chess channel. The first tracked week can be partial and the card shows tracking start. Existing historical activity/sales are not guessed or backfilled.

The report contains current total public-wallet coins, mean/median, wallet count and public top wallets; tracked active wallets, coin creation/removal, wallet-supply change, classified sources/sinks, trade count/coin volume, pet sales/highest/median, all traded pet species, and Shelter counts. Current wallet distributions are labelled **now**, independently of the prior week's flow figures. Locked wager escrow is excluded from wallet totals and its reserve/refund/payout movements are excluded from coin creation/destruction. A reliable whole-history outstanding-escrow total is not reconstructed. Unobserved wallet changes from an older running writer are explicitly classified as unclassified changes rather than invented sources/sinks.

Analytics observes each existing wallet writer inside its atomic commit, including staged puzzle-completion writers; it performs no separate payout or additional Git push. Receipt IDs deduplicate sales and trade volume. Analytics is its own module and snapshot; transaction rules remain in the existing ledger and market modules.

Publication uses a stable Discord nonce with enforced deduplication and a persistent claim. Restart recovery scans messages for the report's unique footer instead of reposting. Confirmed permission/not-found failures may retry; unknown delivery is reconciled hourly and never blindly resent. A genuinely ambiguous delivery can therefore leave that week's outbox unresolved, while later weeks remain eligible. This chooses no duplicate public reports over speculative resends. Date ranges, Easter and New Year's wrap come from `holiday_events.next_holiday()` and the existing holiday definitions.

## Validation

Real disposable Git tests cover fees, timer freezing, capacity, dead/expedition exclusions, history, direct and Wanted receipts, non-coin exclusions, concurrent adopters/sellers, conflict rebuilds, uncertain acknowledgements, report claims and median pricing. UI checks cover pagination, confirmation ownership, hidden eggs, receipt identity, DST/weekly boundaries, Next Event and report recovery. Full root/minigame suites, Python compilation and JSON/YAML validation are required before merging.
