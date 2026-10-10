# SharkBot Fair Play v23 — ten pinned, on-demand compute shards

Draft PR only; the owner decides whether and when to merge/deploy. The live
Discord bot, Guess Chatter, Survival and Minigames are not modified here.

## Confirmed v22 failures motivating this change

Two real v22 reviews (\`nb4\`, \`batmanhacker123\`) both reported 0/5
externally verified compute shards and locally recomputed all 250 deep games,
lasting respectively ~1h41 and ~2h03. All ten worker jobs failed early.
The long-running coordinator code was on \`90f559b3be34\` while the dispatched
workflow executed newer \`main\` revisions \`9e6ff7fae1f0\` and
\`b327234bcd4b\`. The v22 worker required the dispatch workflow's
\`GITHUB_SHA\` to equal the old coordinator SHA, an impossible condition
after main moved. Its full-history \`actions/checkout\` also took up to
~43 minutes. Another latent failure was mismatched key fallback precedence:
controller distributed > checkpoint > Discord token, workers distributed >
Discord token.

## New distributed protocol

1. The central bot gathers 1,000 historical rated metadata games, screens
   500 fast with MultiPV 1, and selects up to 200 recent peer games + 50
   stratified historical/control games for MultiPV 3 deep confirmation.
2. The controller dispatches **one** matrix workflow containing **ten** jobs
   indexed 0–9. Inputs: a keyed pseudonymous ticket and the full 40-character
   immutable coordinator commit SHA. No account, FEN or game information is
   passed via visible workflow inputs or public logs.
3. Every job uses shallow \`fetch-depth: 1\` to checkout precisely that SHA,
   verifies it locally, and validates the matching encrypted request
   schema/config/version/engine before doing any work. Changes to main while
   a review runs no longer invalidate the running review.
4. All parties independently compute Stockfish 19 MultiPV 3 fixed depth 18
   rapid/blitz and depth 12 bullet (with root played-move searches) on
   balanced position-count partitions. They produce evidence only, never
   an independent score or moderation verdict.
5. One central bot still performs Maia-3 human-policy estimates on up to
   1,600 positions, counterfactual analysis, cross-game chronological checks,
   one conservative Fair Play priority and one Owner Evidence ZIP.
6. Encrypted Git ref packets use the same secret precedence at both ends:
   dedicated \`FAIRPLAY_DISTRIBUTED_KEY\`, then \`FAIRPLAY_CHECKPOINT_KEY\`,
   then \`DISCORD_TOKEN\`. Provision a dedicated compute key when possible.
7. Progress and failed-shard status packets are encrypted. Failed, stalled
   and successful shard totals are printed safely in the one Discord report,
   and the central bot recomputes only the missing games. No unverified
   engine result is counted as complete.
8. Each worker has a 35-minute engine budget plus time to upload its
   encrypted result, under a 48-minute GitHub Action limit. The coordinator
   waits at most 45 minutes from dispatch, exits earlier when all workers
   explicitly fail, gives an absent worker up to 8 minutes to begin work,
   and treats 15 minutes without additional progress as stalled. It can
   still recover all missing games locally.

## Evidence quality correction

In the v22 \`nb4\` evidence, only **6 of 1,660** bullet decision positions
entered paired fast/deep stability comparison. The v22 predicate demanded
depth >=18 or a legacy 320,000-node count, even though bullet's required
deep budget was exactly depth 12.

v23 now accepts a completed, exact, contract-verified depth-12 bullet search
as a genuine fast/deep comparison. Invalid/incomplete contracts cannot pass.
It retains the same MultiPV3/depth18/depth12 budgets, classification
thresholds and LOW/HIGH semantics. A new read-only owner audit separates
missing depth pairs from genuine unstable engine comparisons and annotates
the selected chronological suspect period. Per-class counts appear in the
public review as descriptive quality controls; neither these measurements
nor account names are new cheating votes.

For \`nb4\`, an April blitz period previously had 31/53 paired opportunities
stable (~58.5%), below the established 75% quality gate. This remains
insufficient deep confirmation. This PR **does not** alter the 75% gate,
raise priority to HIGH for a known account, or claim a cheating probability.

## Tests and release gate

- 10-way sharding and deterministic balancing;
- pinned revision delivered in workflow inputs and checked before computing,
  with simulated newer dispatch \`GITHUB_SHA\`;
- exact matching crypto secrets on controller/worker;
- fixed-only safe public failure codes, authenticated worker status packets,
  prompt recovery on missing/failed shards;
- real pinned Stockfish19 worker smoke with synthetic chess only;
- exact depth12 bullet comparison and negative incomplete/nonexact controls;
- complete legacy positive/false-positive controls, all bot/minigame tests;
- GitHub Actions green checks on the **final commit**.

The first live v23 scan is still a necessary acceptance test: verify
**10/10 successful compute jobs**, number of remotely verified games,
encrypted transport completion, no unintended local replay, and measured
end-to-end time versus v21 34m44 and v22 1h41/2h03. The goal of roughly
30–45 minutes is provisional, not guaranteed. The free-tier standard job
concurrency cap remains **account-wide** and other persistent workflows
also consume slots.

## Rollback

Unset \`FAIRPLAY_DISTRIBUTED\` to avoid launching distributed work. This
continues the older *local* 125-game depth scope (not the 250-game
distributed expansion), and doesn't introduce any extra bot processes.
Never merge/deploy automatically. No live compute jobs are dispatched by
offline pull-request CI.
