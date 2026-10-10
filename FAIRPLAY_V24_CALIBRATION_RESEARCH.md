# SharkBot Fair Play v24 — calibration-first draft

**Draft only. Do not merge or deploy without explicit owner approval.**

## Intent and invariants

The proven v23 production reference is 25m25s for one completed, distributed review, with 10/10 remote shards and 250/250 remote deep games. v24 proposes **14** pinned Stockfish compute shards; performance is unmeasured until a real end-to-end run. Sharding affects speed and queueing, **not** the number of games, evidence classes, Stockfish search depths or fraud priorities. All decisions remain centralized.

v24 deliberately **does not lower or raise any live threshold**. The configured HIGH 0.65 engine/critical scores, VERY HIGH 0.88 score and gameplay evidence stability 0.75 are preserved. Strong play, a short positive test cluster, timing artifacts or a suspicious username are not proof of cheating. Each HIGH route still has its own demanding coverage and deep-confirmation gates.

## What changed

1. Fourteen remote Stockfish shards (0–13) with exact pinned code revision. File validation now accepts two-digit shard IDs 10–13 while retaining a strict allowlist. All encrypted packets, remote verification, one coordinator and missing-shard recovery remain unchanged.
2. Evidence-grade depth stability is split into two **read-only** diagnostic predicates:
   - **Played-move quality preserved:** candidate-centipawn and scaled-win-loss comparisons remain close between fast and deep searches.
   - **Informative position geometry preserved:** the position still qualifies as difficult and competitive on both budgets.
   An ordinary high-quality move can pass the former and fail the latter. Neither diagnostic changes live HIGH/LOW.
3. The ChessFraud benchmark now reports player-side game-level match baselines and uses player-disjoint development/holdout splits. The previously available move-level descriptive comparison remains, but no longer serves as the only result.
4. A private, aggregate-only offline calibration lab compares recorded production and shadow priorities against independent, controlled labels. It aggregates *per player*, not correlated moves or repeated scans.
5. An explicitly **manual-only** research workflow runs up to two research jobs at once. It downloads only the public controlled ChessFraud dataset when invoked and exports aggregate statistics, not raw PGNs or labels.
6. New negative and synthetic tests cover shard IDs, non-cherry-picked shadow cohorts, label conflicts, incomplete search contracts and preserved-quality/noninformative-geometry cases.

Existing Maia-3 5M, Maia-3 23M/79M and Lc0 diagnostics already exist in the codebase. v24 does **not** claim to turn auxiliary models into independent misconduct votes, silently download new weights, or spin up speculative live model workers.

## Offline calibration lab

The lab takes a **private** JSONL file in a trusted machine, not the public GitHub repository. Each line must represent one independently verified/controlled player scan:

~~~json
{"player_key":"anonymous-controlled-001","truth":"controlled_fair","priority":"LOW","shadow":{"proposal-a":"MODERATE"}}
{"player_key":"anonymous-controlled-002","truth":"controlled_assisted","priority":"HIGH","shadow":{"proposal-a":"HIGH"}}
~~~

Optional shadow entries must be obtained by an actual research replay. The lab will **not invent** candidate ratings or silently assume that changing one gate reproduces the full classifier.

Run locally:

~~~bash
python -m scripts.fairplay_calibration_lab --cases /private/calibration-cases.jsonl --output /private/calibration-summary.json
~~~

Only controlled labels \`controlled_fair\` and \`controlled_assisted\` count. \`suspected\` and \`unknown\` entries are intentionally excluded. Multiple scans for one participant count as one player-level outcome (maximum review priority). Partial shadow coverage excludes the participant from that candidate analysis rather than cherry-picking one scan. Development/holdout assignment is deterministic and player-disjoint.

The reported Wilson intervals describe sampling uncertainty but do not correct all cross-game dependence or cross-platform domain differences. The lab does **not** select a winning shadow variant, modify SharkBot configuration, infer cheating probabilities or publish case identifiers.

## ChessFraud research workflow

Manual from Actions after the workflow is available on the default branch:

\`Fair Play v24 research (manual, aggregate-only)\`

Or locally, after installing the optional \`datasets\` dependency:

~~~bash
python -m scripts.benchmark_chessfraud --allow-download --output /tmp/chessfraud-aggregates.json
~~~

Uses the pinned KDD 2026 \`artemlepin/chess-fraud\` dataset/configuration. The published ChessFraud benchmark stores 505 tournament games as 1010 player-side games with labels; this script applies additional paper-aligned move and minimum-sample filters. It does **not** analyze the synthetic 12,000-player-game corpus or replay Stockfish 19 depth18/depth12 SharkBot routes.

ChessFraud game-level labels cannot be inferred from individual \`is_cheating_move\` flags. The benchmark uses \`is_cheating_player_game\` for game outcomes and \`is_used\` for post-opening decisions. It splits *players*, not rows; chooses a descriptive match-rate threshold on development players, then reports a held-out test. Match rate is not equivalent to a cheating likelihood or SharkBot's multi-route LOW/HIGH classifier.

Dataset: https://huggingface.co/datasets/artemlepin/chess-fraud
Research repository: https://github.com/artem-lepin-ml/chess-fraud

## Account-wide capacity and scheduling

Under the **hypothetical** 20-job standard-hosted concurrency limit:
- up to 14 compute jobs;
- up to 2 main/other pre-existing jobs;
- up to 2 on-demand research jobs;
- 2 slots reserved.

These are not guaranteed free slots: other workflows and GitHub plan limits are account-wide. Model tests are **not** automatically dispatched during a live Fair Play review. Any optional research work that would slow a scan can be run at another time.

## Before approving a merge

- All existing bot, Fair Play, Stockfish-engine and research regression CI checks pass.
- Verify precisely 14/14 remote compute shards and all 250 deep games accounted for, with 0 unintended local recovery; the encrypted handoff and revision pin are unchanged.
- Measure complete runtime against **25m25s** (the new count does not guarantee improved throughput).
- Verify extra \`objective_quality_preserved\` and \`evidential_geometry_preserved\` counts in owner evidence and the public report.
- Complete labeled holdout calibration across multiple ratings, controls, and assistance strengths *before* proposing adjusted LOW/MODERATE/HIGH/VERY HIGH thresholds.
- If compute proves unreliable, keep the v23 known-good commit/ten-shard workflow instead of weakening evidence contracts.

This PR contains no account labels, private evidence ZIP, secret values, suspected-user lists or automated enforcement.
