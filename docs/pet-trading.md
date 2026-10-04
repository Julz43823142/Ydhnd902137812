# Pet trading

Pets use the existing Trade Player, Open Trade and Trade Inbox interfaces in `!menu` in both bot workers. Players choose their own prices; a pet can be exchanged for coins, a badge or another pet.

Copy `pet:<ID>` from the Pet Card into **You give** or **You want**. For a direct trade, a unique name or hatched species also works (for example `pet Buddy` or `pet Dog`). Duplicate names/species require an exact ID. An open trade requesting a particular pet requires `pet:<ID>`.

Example: `!trade @buyer pet:<your-pet-ID> 10` offers that pet for 10 coins. The recipient must accept. Open Trade posts a public listing for eligible players to accept.

Every acceptance rechecks current ownership, living status, expedition status, payment and the final capacity of both collections. The limit is 10 living pets, including eggs; memorial pets do not count. Full collections can swap one pet for another, but cannot buy an eleventh pet. Pet state, both wallets, badge inventories and the immutable trade receipt commit together; conflicts rebuild against current data, and repeated acceptance cannot transfer or pay twice.

A transferred pet keeps its ID, name, XP, level, birth date, feeding deadline and daily care/puzzle state. Trading never resets care or revives a dead pet. A running expedition must be claimed before its pet can be traded. Equipped accessories are removed on transfer; accessory unlocks remain in the original owner's collection. The recipient's current active pet is preserved; an empty collection activates the incoming pet, and a sender who trades their active pet automatically falls back to a remaining living pet.

Egg species and rarity remain hidden in trade labels until hatching. Existing coin/badge trades and donations retain their behavior.
