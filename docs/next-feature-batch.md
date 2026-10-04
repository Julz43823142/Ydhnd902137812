# Market, reports, games and Live Mode

All new UI is English. Existing wallet/pet transactions, receipts, owner history,
capacity checks, trade pricing and encrypted game state remain authoritative.

## Navigation

- Main `!m` / `!menu`: **Economy**, plus **Admin Stats** (Shark-only access).
  Both main workers and the Minigames menu include Economy.
- `!economy` / `!eco`: previous complete week's economy, with the Economy menu
  (main and Guess channels). Existing `!week` remains the personal recap.
- Economy: This Week, Last Week, Monthly Recap, Historical Graph, Wrapped,
  The Fin Report, Monthly History and Weekly History.
- Trade: existing actions plus Open Listings, Matches and Expired Listings.
  Matches belongs to the Wanted buyer; Buy is explicit consent, not automation.
- Event Hub/Community: Previous Event Recaps, plus existing Next Event.
- Annual server Wrapped: **My Wrapped** returns the clicking player's own
  frozen card privately. No unsolicited DMs or personal recap channel spam.

## Listings and matching

`market_listings.py` indexes existing Open Trade listings inside
`pet_market.json`. Daily/Guess startup registers existing rows; original cards
are retained and synchronize closure/receipt status. All public listings show
age/expiry on their detail card. New listings live 14 elapsed days. The one-time
`listing-lifetime-v2-14-days` migration sets still-open Wanted rows to their
original creation time + 14 days, without renewing them from migration time.
Already closed rows stay closed. Expired rows remain available to their owner
in history. Relist creates one fresh identity, rechecks balance/ownership and
limits, and never moves coins or pets merely by relisting.

Matching compares a hatched, alive, tradable offered pet with species and
optional rarity/evolution filters. It checks the asking price against both the
Wanted maximum and current wallet, and buyer living-pet capacity. Eggs cannot
leak their concealed identity via matches. Notifications have one durable key
per Wanted/Open pair and can be dismissed. Settlement revalidates both listings
and their exact terms, wallet, ownership and capacity against the current Git
snapshot. The existing Open Trade acceptance and Wanted closure are staged in
one outer repository transaction, with one verified receipt. Concurrent
buyers/conflicts/lost acknowledgements cannot create a second transfer.

## Persistent reporting

`economy_analytics.json` now retains daily wallet samples, audited daily flows
and immutable closed `weekly_snapshots`; existing frozen weekly `reports` are
preserved. `economy_health.py` explains labels using observed mint/burn,
opening/closing wallet supply, activity, turnover, concentration and available
prior-week volume. Inflation thresholds use net recorded creation relative to
opening supply, not temporary wallet movement into wager escrow. The labels
are descriptive indicators, not a complete economic model. Public wallet
supply always excludes locked escrow.

`shark_activity.json` stores audited daily game/economy totals, public game
participation summaries, dated personal/server records and safe hatch metadata.
Hooks attach to existing Git commits. Minigame start/finish records come from
actual state transitions, not repeated button presses or quest-win callbacks.
Completed chess, Puzzle Battle and Survival summaries have stable audit IDs.
Record comparisons keep settings/duration buckets distinct. No private hands,
solutions, tokens or linked Twitch identities enter these summaries.

`shark_reports.py` schedules in Europe/Amsterdam:

- Monthly recap after the month closes, including saved supply, audited flows,
  trades/pet coin sales, Shelter activity, species, health trend and sample graph.
  Missed completed tracked months are recovered after downtime.
- **The Fin Report** on each completed tracked week: actual recorded high scores,
  major trades, rare hatches and relevant event/Twitch activity. Empty sections
  are omitted rather than filled with randomly chosen ordinary games.
- **Wrapped on December 5**, using January 1 through December 4 inclusive.
  Server and eligible personal snapshots persist. Missed years are recovered
  after downtime. Unknown/unsupported historical metrics are omitted.
- Event recap when each existing-calendar event ends (even without challenge activity): actual contributors,
  progress, tracked Holiday Box spends/badges/rarity, records and pet sales.
- Server milestones at 100/500/1,000/5,000/etc. recorded activity tiers.

`shark_publications.json` permanently preserves frozen payloads and delivery
state for these cards, market matches and milestones. Publication claims use
optimistic Git transactions and stable Discord nonces. Lost acknowledgements
are reconciled by unique message footer; unknown delivery is not permission to
post a duplicate. Permission failures can retry. Cards/graphs are built before
claiming delivery. Restart restores persistent Match and My Wrapped controls.

History stays honest: new activity and daily-flow tracking starts at rollout.
Partial months/years show coverage; missing metrics are not reported as fake
zeros. Previous week's legacy read-only Git reconstruction still works.
Graphs plot saved samples and never backfill guessed datapoints. No player
balances or rewards are migrated or backfilled for reports.

## Consent-based rematches

Finished supported multiplayer minigames offer Rematch. The new lobby copies
mode/settings/stake, admits only prior participants, and requires every one to
join/accept before the host can start. Decline closes without spending. A
stable rematch identity, original-game marker, transaction ID and revision
checks prevent duplicate sessions. Solo Blackjack retains its existing replay.

Puzzle Battle reuses existing invite/lobby code with a serialized persistent
rematch marker, bound to the clicked finished card. Multiplayer rematches are
restricted to the same players and require all to join. PvP chess reuses the
existing opponent acceptance flow and preserves variant/stake/time control,
including clock labels on older saved games. It checks pending invitations
and serializes duplicate requests. A rematch never silently starts a game.

## Twitch presentation

`twitch_live_mode.json` contains only the verified broadcaster live flag,
public stream ID, checked time and previously recorded stream IDs. Only a
successful existing Twitch API check changes the state; API failures preserve
it. Profile renderer cache keys include the live flag. The dedicated
`assets/twitch-live-overlay.svg` adds Twitch purple lighting/logo accents,
Shark artwork and LIVE badge. Equipped/holiday artwork remains primary;
Live Mode is a temporary overlay. It never equips or grants a cosmetic.

## Admin product analytics

`feature_usage.json` contains aggregate daily/all-time counts and 256-register
HyperLogLog sketches (~6.5% standard error) for approximate unique users.
It contains no raw user identifiers, command arguments, message text or
individual interaction timelines. Unknown command names are grouped as Other.
Admin access calls the existing `shark_admin.require_admin` before reading.
7/30-day views compare equal preceding windows; all-time observed start/finish
counts show completion ratios when meaningful. Least-used recorded features
are shown, not guessed counts for uninstrumented features.

Command/button counts buffer for at most a minute to avoid a Git push per
interaction. Abrupt worker failure can lose the unsaved buffer; financial and
audited game statistics do not depend on it. Once a batch starts, its receipt
identity is retained through uncertain acknowledgements. Game start/completion
counts are attached atomically to their audit commits.

## Validation

Full root/minigame suites, compile, JSON/YAML and diff checks are required.
New disposable-Git tests cover matching criteria/capacity, concurrent buyers,
changed listing terms, expiry/relist/migration, conflict/uncertain ACK, frozen
weekly history, outbox claims/recovery, deduplicated scheduled content, monthly
DST/year boundaries, December 5 cutoffs/recovery, private admin access,
approximate unique counts, Twitch transitions/cache and stale racer cards.
Pure minigame tests exercise same-player consent, outsiders/decline and
repeated Rematch clicks. Tests mock new persistence hooks in legacy API-only
Twitch tests and never write simulation status to production state.
