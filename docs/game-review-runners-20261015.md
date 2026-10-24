# Ordinary Game Review — isolated idle Fair Play runner capacity

## Delivery

The same Game Review service used by `!review`, human-vs-human and human-vs-bot
postgame threads now supports **fourteen on-demand isolated GitHub Actions workers**
for sufficiently long games. This uses the *same GitHub-hosted runner capacity*,
but **not** running Fair Play evidence jobs: GitHub-hosted runners are
ephemeral. A new, separate workflow is dispatched only when the Fair Play
compute workflow has no queued or active runs.

- `game_review_service.py` remains the single-flight cached entry point and
  falls back to the existing **local depth-18 Stockfish 19** automatically.
- `game_review_remote.py` splits eligible mainlines into fourteen contiguous
  segments with their original starting FENs, encrypts all work, leases at most
  one normal review across bot instances, dispatches two compute-only shards,
  validates exact engine configuration and move/FEN chain, and reassembles the
  original per-move classifications.
- Shard engine analysis has a bounded **540-second budget**; the coordinator
  waits at most **600 seconds**. Missing, failed, delayed, preempted or invalid
  packets are not accepted. The original game is then reviewed locally.
- **Fair Play has priority**: a queued or running Fair Play workflow, unknown
  GitHub API status, missing token or broken reservation disables remote
  dispatch. Workers check Fair Play status and lease ownership during their
  move loop and abort ordinary computation on preemption. They never cancel or
  modify an existing Fair Play job. Up to fourteen ordinary compute jobs may be
  active; the ordinary workflow has its own concurrency group.
- Remote workers calculate no Fair Play misconduct scores and never touch
  Discord, wallets, puzzles or economy ledgers.

## Required execution environment

For existing SharkBot deployments already using the Fair Play distributed
compute infrastructure, ordinary review inherits
`FAIRPLAY_DISTRIBUTED=1` as its default switch.

To opt in or out independently:
`GAME_REVIEW_DISTRIBUTED=1` or `GAME_REVIEW_DISTRIBUTED=0`.

Required coordinator environment:
`GITHUB_REPOSITORY=owner/repo`, `GITHUB_SHA` (the bot's exact deployed
40-character commit), `GITHUB_TOKEN` or `GH_TOKEN` with contents-write,
actions-read and workflow-dispatch permissions, plus
`FAIRPLAY_DISTRIBUTED_KEY` (preferred), `FAIRPLAY_CHECKPOINT_KEY` or
`DISCORD_TOKEN` as encryption key material. The compute workflow obtains
the same secret precedence and a GitHub job token via its existing
repository secrets and GitHub Actions credentials.

Only games with at least **80 plies** are eligible by default.
A short game or cache hit always stays local. The normal PGN export UX and
original clock data do not change.

The remote workload and result are Fernet+gzip encrypted on the separate ref
`ordinary-review-distributed-work`. Cipher keys are domain-separated from
the Fair Play compute ref even when the same base secret is used. Workflow
inputs and logs contain opaque tickets/shard indices/pinned revisions, never
player identities, SAN, FEN, private metadata or source PGNs. Old encrypted
blobs may remain reachable in git history even after active-ref cleanup.

## Verification and limitations

- Test coverage checks CAS lease ownership and cleanup with a **real local Git
  remote**, encryption isolation, Fair Play queued/running/unknown fail-closed,
  bounded dispatch inputs, bad/partial/stale response rejection, ordinary
  cached route, and automatic local fallback.
- CI's existing real Stockfish smoke exercises **fourteen full depth-18 segment
  analyses** and recombines them into one legal Game Review with the original
  final board position.
- Remote shard aggregation recomputes **whole-game Accuracy** from the
  combined move record and position probabilities; it does not average
  per-shard accuracy scores. This differs slightly from a monolithic engine
  process when transposition-table contents vary; the Stockfish depth,
  MultiPV and per-ply classifications remain independently evaluated.
- No speed percentage or remote end-to-end gain is asserted. Queue delays,
  the isolated Git exchange, network access, Stockfish startup and concurrent
  Fair Play activity can overwhelm small review workloads. The 80-ply
  threshold is deliberately conservative; **live performance must be
  measured and may warrant disabling remote dispatch**.
- A green PR proves offline tests and pinned-engine smoke. It does *not*
  demonstrate a production bot deployment or a real dispatched Game Review
  until a live >=80-ply review returns verified results.

The workflow is not invoked from pull-request CI. It becomes dispatchable
from `main` after merge, subject to the deployed coordinator's environment.
