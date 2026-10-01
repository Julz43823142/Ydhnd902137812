# Permanent pets

Open `!pet` or `!pets` in a chess channel or the Minigames channel. All controls
are owner-only. Choose a pet from the Collection dropdown to feed, rename,
solve its daily puzzle, or make it active. Only one pet grants a bonus at once.
The profile shows care availability and the next reset with Discord timestamps.

## Adoption and care

A Pet Egg costs 10 coins, with a separate purchase confirmation. The server
selects species and rarity at purchase, but the Discord page, artwork and
collection hide both until Level 1. There are at most 10 living pets per player;
the permanent memorial is not limited to 10.

| Rarity | Chance | Species |
| --- | --- | --- |
| Common | 55% | Dog, Cat, Rabbit |
| Uncommon | 25% | Fox, Panda, Penguin |
| Rare | 13% | Shark, Wolf, Owl |
| Epic | 5% | Lion, Tiger, Eagle |
| Legendary | 2% | Dragon, Unicorn, Phoenix |

Each species within a rarity is equally likely. There are no event pets.

Feed is free, restores hunger and grants 5 Pet XP once per Amsterdam calendar
day. Pet Puzzle offers a dedicated chess puzzle from the existing offline pool:
submit successive SAN/UCI moves, with automatic opponent replies. A wrong move
does not consume the care reward. Completion grants 20 XP and restores 35
happiness (maximum 100), once per pet per day. The puzzle and move progress
survive worker restarts. Pet Puzzles do not affect ratings, competitive scores,
quests or ordinary puzzle payouts.

All living pets need food, including eggs and inactive pets. Exactly 168 hours
without food causes irreversible death. Happiness does not cause death: it
decays by 15 per full elapsed day since its last restoration. Death is evaluated
on reads and transactions, even after worker downtime; memorial information is
saved with subsequent pet updates. Feeding an expired pet cannot revive it.

## Progression and bonuses

Level 1 needs 20 total XP. Each subsequent level needs `30 + 5 × current level`
more XP; the displayed maximum is Level 50. Evolution is Egg (0), Baby (1),
Young (10), Adult (25) and Evolved (50), with stage-specific vector portraits.
Species-specific artwork can be added later.

Reward/activity hooks grant 5 XP to the active living pet per unique event,
with up to 50 activity XP per pet per day. Sources include puzzle rewards,
chess/Chess960 activities, Rush, Survival, minigame wins and quest completion.
Some activities have both a gameplay payout and a quest-progress event; the
daily cap includes both. Feed and Pet Puzzle XP are separate from that cap.
Old/replayed events never grant XP twice, or to a newly adopted pet.

Bonus families rotate across species in the table order: cosmetic shop
discount, quest coins, puzzle coins, Pet activity XP, then repeat. Rates are
1% / 2% / 3% / 5% at Baby / Young / Adult / Evolved. Happiness of 70+ grants
the full rate, 30–69 half, and below 30 none. Egg/dead/inactive pets grant none.
Coin bonuses never change Shared Points or competitive scores. Refunds,
administrative adjustments, wagers and weekly prizes receive no pet bonus.
Cosmetic discounts cover boards, pieces, arrows, name colors and profile
themes; boxes retain their advertised fixed prices. Small coin amounts retain
the ledger's existing three-decimal precision; Pet XP retains hundredths.

## Persistence and deployment

`pets_state.json` and immutable `pet_events/` audits are committed using the
shared repository lock, fresh origin state, push-conflict retries and remote
acknowledgement verification. Egg purchases commit the wallet, pet and audit
in one Git commit. Pet data is separate from profile rows, so an older profile
writer cannot erase a player's collection. Failed optional Pet XP writes do
not roll back a gameplay reward; replaying its original transaction retries XP.

Push triggers restart Daily, Guess, Survival and Minigames when `pets.py`
changes. Pet state writes themselves do not trigger those workflows. Verify
`!pet`, a confirmed purchase, daily care, puzzle completion and admin ticket
controls in Discord after deployment. Offline tests use temporary local Git
remotes; they never write production state or send Discord messages.
