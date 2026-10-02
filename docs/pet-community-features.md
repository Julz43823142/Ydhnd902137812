# Pet and community feature round

All interface text is English. Existing pet/wallet/quest records remain authoritative. New owner fields are optional; no migration or production state reset is needed.

## Access and previews

- Pets → Accessories / Expeditions. Shop → Pet Accessories.
- Profile → View Pet / View Pets / Collection Book / My Week / Community.
- `!week`, `/week`: personal recap, only on request.
- `!collection`, `/collection user:`: completion book using actual catalog totals, unique known unlocks and hatched species (including memorial pets). Eggs never disclose species/rarity.
- `!community` / `!challenge`, `/community`: current challenge and pending reward claims, including completed older challenges.
- `!event`, `/event`: Event Hub. The hub button appears only during Holiday Events; a stale button reports that no event is active. Overlapping events have independent challenges; neither advances a normal weekly challenge.
- Shop character/cosmetic selection already previews using player stats. Explicit Preview re-renders without writing ownership/equip. Back returns to Shop. Guess's boards/pieces/themes reuse this browser.

Profile pet artwork uses a portrait thumbnail beside the header and a compact name/species/rarity/level field. Follow-up pages keep the viewed user ID. Public pet controls remain read-only.

## Accessories and expeditions

Crown 30 coins, Glasses 15, Bandana 15, Hat 25, Necklace 20, Bow 15. Permanent account unlocks can be worn by any owned hatched pet, one at a time. No bonuses. `pet_accessories.py` keeps the catalog/art layers separate from the card renderer so artwork can be replaced later.

An active hatched pet needs at least 60% Hunger and 50% Happiness to depart. One expedition per owner; completed expeditions must be claimed before departure again. Normal pet care and the seven-day death rule remain in effect. Changing the active pet does not change the expedition's recorded traveller.

| Duration | Guaranteed coins | Pet XP | Accessory chance |
| --- | ---: | ---: | ---: |
| 2 hours | 1 | 10 | 10% |
| 6 hours | 3 | 25 | 20% |
| 12 hours | 6 | 50 | 35%, including a 3% Starlight Crown chance |

Rewards are chosen and stored at departure, hidden until claim. Duplicate accessories retain the existing unlock, without additional coins. A pet dead at claim cannot receive expedition rewards. Return timestamps persist; no timer process must survive a restart. Refresh the expedition page when it returns, then claim once.

## Challenges and accounting

Normal Monday–Sunday challenges rotate between 3,000 puzzles, 500 minigame wins and 250 completed Rush runs, with a 500-coin pool. Each Holiday has its own larger multi-goal challenge/pool in `community_progress.EVENT_GOALS`. Animal Day uses 5,000 puzzles + 500 feeds + 300 Pet Puzzles with a 1,500-coin pool. Every goal must complete within its event window.

Contribution weights normalize each action by that goal's target, so different activities can contribute equally to completion. Excess actions beyond a completed goal do not add weight. Completion freezes allocations; later actions still count in My Week. Only actual contributors are eligible.

Rewards reserve up to 2 coins per contributor (scaled down for very large groups), distribute the remaining pool proportionally, and cap each player at 20% of the pool. Coin calculations use thousandths to support large groups. Any remainder caused by caps/rounding stays unminted; a solo participant cannot collect the whole server pool. Claims never expire and are available on demand; no channel spam or automatic payouts.

`community_progress.json` is created lazily in the same Git commit as the existing action/coin/pet audit. Quest actions count even when no selected personal quest matches. Wallet earned-coin deltas, new unlocks and pet level changes populate weekly recaps. Refunds/admin/transfers do not count as earned gameplay coins. Recaps track from this release, without inventing historical activity.

Challenge payouts atomically commit wallet + paid marker. Expedition claims atomically commit wallet + pet XP + accessory + claimed status + audit. Optimistic retries reload origin; lost acknowledgements are verified by the durable marker before retry. Existing local Git integration tests simulate uncertain push responses and repeated claims.
