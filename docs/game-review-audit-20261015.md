# Ordinary Game Review — audit and execution contract

## Review pipeline

Both the manual `!review` PGN command and completed human-vs-human and
human-vs-bot games call `game_review_service.game_review_service.analyse`.
The service delegates to the **same** depth-18 `chess_play.analyse_game_moves`
engine, off Discord's asyncio event loop. This does not reuse the
rating-conditioned Fair Play detector or its evidence/scoring rules.

Game Review uses the publicly described Lichess win-chance conversion,
per-move exponential accuracy and the mean of volatility-weighted/harmonic
game accuracy. **Chess.com's proprietary Game Review Accuracy and move
classifier are not claimed as reproduced.** Source:
https://lichess.org/page/accuracy

Stockfish root evaluations are compared from the same mover perspective,
including a paired MultiPV=2 search of the best and played moves at\nthe same root and depth when the played move was not in the top PVs.
The engine search depth is 18. Mate-score sentinels now produce **0% or
100%** winning chances for forced mates, while drawn terminal boards remain
50%. The move-level independent evaluation and original imported PGN
comments/NAGs are strictly separate. The same local result is used to render
both command and post-game reviews.

The shared result identifies analysis version, engine name, depth,
configured threads/hash, analyzed/skipped plies, explicit independent
Accuracy provenance and measured engine elapsed seconds. Early engine
termination, incomplete evaluations and invalid moves are rejected rather
than silently filled with fictitious scores. Existing Fair Play LOW/HIGH
thresholds, analysis channels, coins and wallets were not altered.

Book labels are not created without an opening database. Brilliant labels
still require an engine-approved tactical sacrifice with alternatives;
a forced single legal move cannot be Brilliant.

## Bounded deterministic caching

The service canonicalizes source SAN into a normalized initial FEN +
complete UCI mainline and keys an independent result on Stockfish source
revision, configured binary fingerprint, depth, threads/hash, analysis version
and max-plies. A process-local lock, in-flight future and bounded 24-entry,
30-minute cache make overlapping identical reviews run **once**. Successful
results are copied before serving them. Exceptions are shared with concurrent
waiters but are never cached, so a later attempt retries.

The cache stores only independent analysis. It does **not** store or
replace the original game PGN, per-move clocks, imported headers or
external comments. Different imported clocks are preserved per game when
exporting via Moves only / With clocks / Clocks + analysis.

The cache is process-local, not cross-host or persisted through redeploys.
It intentionally preserves the local Stockfish fallback even when GitHub
Actions or Fair Play infrastructure is offline.

## Fair Play runner investigation (2026-10-15)

The existing `.github/workflows/fairplay_distributed.yml` is a **workflow
dispatch with up to 18 isolated, on-demand matrix jobs**, each limited
to 48 minutes. Worker inputs include authenticated/encrypted Fair Play
tickets, specific shards and immutable code SHA. Their result schema
contains per-game Fair Play compute evidence and is validated against a
central Fair Play coordinator by `fairplay_distributed.py`.

GitHub-hosted runners are **ephemeral jobs**, not a pool of idle
machines that an ordinary review can claim. When a Fair Play review is not
running, the matrix workers do not exist. Starting an ordinary review on
Actions requires **new** jobs, runner queue/startup, Stockfish setup,
separate encrypted input/output schema, quotas and failure recovery.
Feeding ordinary-game requests into Fair Play's encrypted job protocol
would violate its schema and isolation contract.

Historical measurement from the completed, **successful** Fair Play workflow
[run 38776147118](https://github.com/Julz43823142/Ydhnd902137812/actions/runs/38776147118):
four isolated shards entered checkout approximately five seconds after workflow
dispatch, then took **8, 15, 9 and 10 seconds** respectively from checkout
to starting the compute script. Their actual reported compute phases were
**173.28, 152.06, 119.36 and 193.98 seconds** for respectively
**308, 301, 301 and 302 positions**. These are authentic Fair Play workloads
from shard logs, **not** ordinary Game Reviews and not remote review
throughput measurements. Different engine budgets and job scheduling make
a direct speedup inference invalid.

**Decision: do not redirect normal Game Reviews to Fair Play workers.**
There is currently **no measured end-to-end advantage** for a separate
ordinary-review job over the local engine for representative workloads.
The required Fair Play atomic capacity reservation and priority cannot
be guaranteed through the existing workflow protocol. Remote dispatch
is intentionally *not* activated or approximated with unsafe retries.
No Fair Play workflow, priority or cancellation behavior was changed.

The existing `scripts/smoke_stockfish_reviews.py` now records real
local depth-18 timing and an identical cached repeat on its synthetic
six-ply game in PR CI. `scripts/benchmark_normal_review.py` is an
optional standalone two-ply probe; CI does not run that extra engine job. It **does not** claim remote throughput.
A future independent remote-review service would require its own job type,
authenticated schema, fairness scheduling, bounded requests, retries,
result verification, matched engine budgets and comparative benchmarks
before deployment.

## Private validation against Chess.com

`scripts/audit_game_review.py` accepts a **local** PGN file and optional
independently recorded Chess.com and old SharkBot Accuracy percentages.
It reports actual scores by side, evaluated/skipped moves, classification
counts, engine config/version, measured time and absolute deviations.
It does **not** upload the PGN or display identities.

Example:

```shell
python scripts/audit_game_review.py --pgn /private/match.pgn \
  --reference-white 78.1 --reference-black 84.0 \
  --previous-white 65.2 --previous-black 87.1
```

The reference percentages were supplied by the owner; the original match
is **not** committed as a synthetic fixture or embedded in this repository.
Do not assert before/after improvement against that match until it has
actually run on a pinned Stockfish 19 engine at the same search settings.
No Chess.com private-analysis endpoint is fetched. Optional published
Chess.com metadata is not attached without an exact identity and
mainline match. Missing source `[%clk]` entries remain missing.

## Validation

PR CI runs the full offline unittest and minigame suites, Python compile
checks, published repository data validation and existing Fair Play engine
and security gates. A real Stockfish smoke test independently benchmarks a
normal review followed by a cached repeat. Unit tests cover shared
analysis, duplicate concurrent requests, failure/retry, bounded cache,
immutable copy, per-source clocks, untrusted PGN annotations, White/Black
score polarity, terminal mates and conservative move classifications.

**All before/after scores and speed differences must come from completed
runs, never from claims that the code merely looks better.**
