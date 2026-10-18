# Fair Play Ban Alerts

Independent opt-in notifications on SharkBot's existing Discord client. No
review scoring, Accuracy Optimizer, Stockfish settings, review checkpoints or
worker processes are changed by this feature. Status is NEVER analytical input.

## Detection and limitations

Chess.com Published Data API profile `status` must be exactly
`closed:fair_play_violations`. The current public field/value was verified with
an actual PubAPI request on 2026-10-18. No other closure or unknown/missing
reason qualifies. Official endpoints: `/pub/player/{username}` and validated
monthly `/games/archives`, `/games/YYYY/MM` endpoints. No arbitrary fetching,
third-party accusation API or public compensation API is assumed.

An alert reports **newly detected**, not newly imposed, closure and does not
identify which game involved misconduct. The followed Chess.com identity is not
verified Discord ownership. A subscription starts with a per-opponent baseline;
first-seen closed profiles are suppressed, and failures are unknown rather than
an active/closed baseline. Later observed transitions to the exact Fair Play
status create one receipt key per Discord user/followed account/opponent/type.
Multiple games provide one latest link and the count of recorded meetings.

Default initial lookback: 90 days, bounded at 14 monthly endpoints and 2,000
recent game rows/account. Successful incremental refresh revisits months since
last success including a previous-month margin. Opponents remain watched for
365 days after the last recorded meeting, including months after leaving the
history-download window. Repeated API failures retain old knowledge. Unknown
closure timing, missing public games, retention and API caching limit coverage.

One serial, paced PubAPI client/cycle, no engine/model processes. Shared opponent
profiles are deduplicated and cached for an hour; fresh subscription baselines
refresh older cached profiles. At most 2,000 oldest-checked opponents per cycle
rotate fairly; a large watchlist can therefore take multiple cycles. Max 50
unique tracked accounts, 500 total subscriptions and five per Discord user. Subscription-validation HTTP concurrency is limited to two. Defaults are
conservative resource bounds, not a promise of realtime coverage. Cycle budget
50 minutes and cancellation are bounded; normal bot shutdown signals the poll.

## Durability, exactly-once limits

Only encrypted data reaches dedicated Git branch `fairplay-ban-alerts`, file
`subscriptions.enc`. This is not the `fairplay-checkpoints` branch. Encryption
uses a distinct key derivation domain. Local writes are atomic and mode 0600;
remote non-force pushes compare against the loaded revision. Two stale writers
cannot both durably reserve the same pending event. A state write failure
prevents delivery and requires reload. Schema v1 is explicit; unreadable state
is not silently reset. A single bot/writer is required for local-only storage.

Persist `sending` BEFORE sending. Persist the Discord message ID after ACK.
Explicit rejected 4xx sends may retry next cycle. Timeouts, 5xx, process death
or uncertain ACKs stay `sending`; reconcile only the bot's own channel messages
by opaque alert footer ID. **Never blindly retry ambiguous sends.** If the
message is absent from the bounded history search (2,000 messages), keep it
uncertain for operator review. This guarantees conservative at-most-once
behavior, not lossless exactly-once delivery: an interrupted reservation before
sending can leave a missing notification. Deleted delivered messages do not
trigger resend. Outbox receipts survive restart and unsubscribe/resubscribe.

## Discord and activation

Persistent buttons use `shark:fairplay:ban-alert:*`: Subscribe / Unsubscribe
(username modal), Mute Pings / Unmute Pings, My Subscriptions. Users modify only
their own Discord-ID settings. Any public Chess.com account may be followed.
Mute retains public alerts but disables mentions; allowed mentions explicitly
include only the subscribed Discord ID. No role/ban/report/coin action exists.

On the next normal bot start, workflow default `FAIRPLAY_BAN_ALERTS=1` restores
state/views and the separate panel in channel 1311445685492781186. Set repository
variable `FAIRPLAY_BAN_ALERTS=0` to disable on next start. Do NOT interrupt an
active review to deploy. To follow the initial desired account, click Subscribe
and enter its Chess.com username; there is no automatic opt-in or ownership claim.

Optional environment settings:

* `FAIRPLAY_BAN_ALERTS_KEY` dedicated stable secret, recommended; otherwise
  `SHARK_STATE_ENCRYPTION_KEY`, then existing `DISCORD_TOKEN` as fallback.
* `FAIRPLAY_BAN_ALERTS_FILE`: local encrypted snapshot path; default hidden file.
* `FAIRPLAY_BAN_ALERTS_INTERVAL`: 3600–86400 seconds (default 3600).
* `FAIRPLAY_BAN_ALERTS_LOOKBACK_DAYS`: 1–365 (default 90).

Actions uses the existing repository Git proxy/token and `contents: write`;
branch rules must permit the dedicated encrypted branch. Do not rotate keys
without migrating the encrypted state: losing receipts loses dedup history.
No private usernames/results or live payloads are checked into main or CI.
