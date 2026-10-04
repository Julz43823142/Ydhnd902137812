# Private usage dashboard

`/usage` and the existing **Admin Stats** button open the same ephemeral
Sharkmeister-only dashboard. Every tab checks `shark_admin.require_admin`.
This Week defaults to Monday–Sunday in Europe/Amsterdam; All Time uses permanent
saved counters. Tabs update the existing private card. Every nonzero tracked
feature appears, ordered by count, with readable game/feature names.

`feature_usage.json` remains the single analytics store. New `use:` counters
start from this release (`usage_since`). Older technical command/button counters
are retained but are not reinterpreted as historical popularity counts.
Command aliases share feature names. Allowlisted page-opening button labels
count human actions; Refresh and game invitations/menu clicks do not count plays.

Minigame starts append one usage marker per session to the existing atomic game
transaction. Multiplayer participants do not multiply a play. Saved Chess,
Puzzle Battle, Rush and Survival starts are recovered by the existing buffered
analytics loop and deduplicated using opaque session hashes in `plays_seen`.
Old sessions and pending invitations are excluded. Saved session timestamps
determine the calendar week, rather than the time recovery happens.

No raw user IDs, individual behavioral timelines, arguments, private game state
or messages are added to the analytics file. Existing aggregate unique-user
sketches remain. Buffered page/command actions save each minute (and when
Sharkmeister opens the dashboard); a sudden process failure can lose unsaved
page counts. Saved game starts can be recovered after restart without replaying
financial actions or modifying game state.

The main worker registers `/usage` in the existing guild command sync. Guess
and Minigames route the same application command to their own channel worker,
avoiding competing responses. A normal worker restart loads this command;
deployment does not interrupt games by restarting workers automatically.
