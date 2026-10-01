# Holiday collections

Eight annual collections with 85 distinct emoji badges in total. Both
Puzzle and Guess shops share the box picker, confirmation, wallet and categories.

| Collection | Available dates (inclusive, Europe/Amsterdam) | Preview name |
|---|---|---|
| New Year | December 29–January 7 | Party SharkBot |
| Valentine | February 7–14 | Cupid SharkBot |
| Easter | Seven days before Western Easter Sunday through Easter Monday | Bunny SharkBot |
| April Fools | April 1–7 | Jester SharkBot |
| Earth Day | April 20–26 | Eco SharkBot |
| Animal Day | October 1–7 | Wild SharkBot |
| Halloween | October 15–November 1 | Spooky SharkBot |
| Christmas | December 15–28 | Santa SharkBot |

Shop > Badge Box opens a choice, not a purchase. Normal boxes cost 50 coins.
Only currently active Holiday boxes appear; they cost 35 coins and guarantee
one of that event's badges, uniformly chosen within the collection. Duplicates are
allowed. Every choice has a second confirmation before money is spent.
Overlapping events appear side by side. Expired confirmations cannot spend coins.

Seasonal emoji already in ordinary pools move into Holiday, retaining exactly
the same stored value. Existing owners keep their badges, duplicates, active
selection and ability to trade/donate; they now count toward Holiday collections.
Normal boxes cannot drop Holiday badges. Ordinary chess badges stay ordinary.
All unlocks remain usable outside the purchase window.

The Daily worker uses seasonal avatars and guild nicknames throughout the
event period, restoring the original profile afterward. Event notices state
the actual holiday date separately from the box availability period. Files and generation prompts are in `assets/holiday_avatars/`.

## Deployment verification

Merge the PR to main; Puzzle and Guess push triggers restart their respective
workers automatically. Keep their long-running bot steps active (orange).
Check both shops, the Holiday profile category, a final puzzle move, and a
subscriber equipping purple then pink. If Discord rejects a color, check the
actual role ordering: SharkBot above shop colors above Subscriber. Offline
tests reproduce both a normal subscriber and adjacent role positions, but are
not a substitute for a live hierarchy check.
