# Owner-only automatic Fair Play rescans

In the Fair Play Task Force channel, Sharkmeister can use:

- `/fairplay auto start`: enable a newest-to-oldest historical rescan pass.
- `/fairplay auto stop`: disable further scheduling. A review already queued or
  running finishes; `/stopfairplay` retains its existing active-scan behavior.
- `/fairplay review username:...`: manual submission (formerly `/fairplay username:...`).
  Discord cannot expose a command and a subcommand group under the same name.
  The existing Submit and Re-scan buttons remain available.

Only the existing `shark_admin.ADMIN_ID` can enable/disable automation, including
when other members have administrator permissions. All entry points remain
restricted to the dedicated Fair Play channel.

Automation waits for an empty review queue and reserves at most one background
review. It uses the same analysis, executor, distributed workers, checkpoints,
public progress/report card and scoring version as manual submissions. It does
not preempt an active scan. Manual submissions received during a scan queue ahead
of the next automatic scan. Enabling this feature authorizes ongoing compute use
until stopped or the pass is exhausted; it is disabled by default.

Finished report embeds must be authored by this bot and contain a validated
Chess.com username and a recognized detector version. Human messages, loading
cards, unknown versions, same-major/current reports, and future versions are
skipped. The first report encountered for each case-insensitive username wins,
so repeated reports cannot create repeated scans in a pass. A newly completed
manual scan also marks that account as handled. Failed background scans are not
retried repeatedly in the same pass.

History is paginated in batches of 100 messages, newest first. A pass stops at
the end of history; stop/start starts a fresh pass. On a detector-version upgrade,
an enabled pass begins again using the new version and skips current-version
reports. This feature runs continuously when idle, not on a civil-time schedule;
there is no timezone or daylight-saving boundary to configure.

The cursor, enabled flag, deduplication set and resumable job reservation share
one encrypted checkpoint write. They survive planned Actions worker handoffs.
No plaintext account list or case ledger is committed. Encrypted storage is
required, persistence failures fail closed, and the existing single-heavy-scan
limit remains unchanged. Up to 10,000 account identities are retained per pass;
at the bound automation disables rather than dropping deduplication history.

No detector thresholds, Stockfish budgets, Maia settings or review classifications
are changed by this feature. It does not prove improved detection accuracy.
