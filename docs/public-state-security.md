# Public repository, private game answers

SharkBot's source remains public so GitHub-hosted standard Actions runners remain free. This does **not** imply that private game answers or user records should be published.

## Runtime protection for new puzzle answers

The Daily and Survival workers use `shark_state_privacy.py` to encrypt unreleased move sequences, PGN, and answer fields before writing public JSON state. The files remain JSON, but protected fields contain authenticated ciphertext. The bot decrypts them only in runner memory, and refuses to overwrite state it cannot decrypt.

The encryption key is selected in this order:

1. `SHARK_STATE_ENCRYPTION_KEY`, if set as a GitHub Actions repository secret.
2. The existing `DISCORD_TOKEN` secret, using a distinct derived encryption key.

**Recommended:** Add a random, long-lived `SHARK_STATE_ENCRYPTION_KEY` in GitHub Settings > Secrets and variables > Actions > Repository secrets. Use the same value for both Daily and Survival workers. Never commit it or expose it in logs.

The fallback requires no configuration changes but couples recovery to the Discord bot token. Before rotating or revoking the token, configure a stable encryption key and verify that the current state has been rewritten with it. Back up the encryption key separately: losing both the key and any previous bot token makes encrypted answers unrecoverable. Do not reset the saved game files on decryption failure.

## Existing public history

Encryption protects **future writes only**. Earlier commits may still expose puzzle answers and user records. A normal commit deleting or encrypting a field does not erase previous Git history, forks, Actions artifacts or downloaded copies. Purging Git history requires a separate coordinated migration with worker downtime, backup, repository-history rewrite and review of downstream consumers. Do not force-push the state branch casually.

Public chess puzzles may also be discoverable from their public Lichess IDs. Hiding the direct move list reduces accidental leaks but cannot make a public-source puzzle impossible to solve externally.

## Personal information

User IDs, player names, transaction histories and usage records still exist in public ledger files. They are not converted to private storage by this release, because Daily, Survival, Guess and Shop workflows share them. If these records should be confidential, migrate them to a separately permissioned store first. Encryption of a few answer fields is not GDPR compliance by itself.

## Verification

`python -m unittest -v test_shark_state_privacy test_shark_commands test_shark_ai_safety test_shark_v2`

No claim of 100% security is made.
