# SharkBot 2.17 — Initial security and reliability audit

This report describes verified findings from inspecting the repository, selected source files and GitHub Actions. It is **not** a complete penetration test or proof that the entire bot is secure.

## CI root cause: 2.15

PR #174: Offline Bot Tests ran 1,575 tests, failing only `test_free_box_claim_uses_authenticated_author`. The test mocked **every** `asyncio.to_thread` call with a fake claim receipt. The newly added read-only stats query executed first and consequently returned a truthy, non-string fake result; the claim branch was never reached. The test now distinguishes the claim service from other asynchronous background calls. A new CI run must verify this.

## Security controls observed

- `shark_commands.py` defines an explicit command allowlist; administrative commands are absent.
- The claim route uses the authenticated message author's Discord ID, not an LLM-provided ID.
- Existing command handlers are intended to enforce trade ownership and transaction checks.
- Workflow permissions for the examined test jobs include `contents: read`.

These checks are **partial**. They do not independently prove that downstream persistence, concurrency, or every game command is safe.

## Changes in the 2.17 audit branch

- Corrected the claim regression test mock, also backported to PR #174.
- Capped in-memory follow-up context at 512 entries, opportunistically purging expired entries.
- Added tests for expired-entry cleanup and capacity limits.
- Prepared release version 2.17.

## Further high-priority review (not yet certified)

1. Audit every coin, reward, shop, pet and trade mutation for atomicity, replay protection and race conditions across concurrent workers.
2. Verify guild-level data authorization end-to-end; using guild IDs in conversation context alone does not guarantee game storage isolation.
3. Confirm that command execution relies on actual Discord IDs and roles, not names or AI-suggested identities.
4. Perform secrets scanning across tracked files and Git history, rotate exposed keys and minimize bot permissions.
5. Audit dependency versions, CI third-party actions, workflow triggers and supply-chain access.
6. Add bounded payload sizes, timeouts, rate limits and idempotency to external Groq requests and mutation tools.
7. Add malicious-input regression tests for prompt injection, forged mentions, ambiguous names, cross-guild queries and double claims.
8. Expand natural-language support with real, typed read-only tools (profile, coins, pets, badges, trades, chess, leaderboards) instead of creating a large static question catalog.
9. Ensure each failed game action returns a clear, non-sensitive failure reason.

Do not merge this branch until the automated tests and code-review gates are green. Version 2.16 remains an independent draft specification and is not silently considered implemented.
