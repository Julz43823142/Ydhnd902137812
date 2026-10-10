# Fair Play v22 — five encrypted Stockfish compute jobs

**Status: draft PR; not merged or deployed by the assistant.**

## One central report

The existing Daily Puzzle runner remains the only Discord client, Chess.com
data collector, policy evaluator, evidence scorer and Owner Evidence ZIP publisher.

The central controller first analyzes up to 500 rated games using 24k-node
MultiPV 1 and rechecks the selected set at fast MultiPV 3. It then dispatches
**one** GitHub Actions workflow, containing exactly **five parallel matrix jobs**
on standard ubuntu-latest runners.

The expanded deep selection comprises up to 200 recent games with known
opponent rating no more than 500 Elo below the subject, plus 50 stratified
historical/control games. Older matches are used to backfill this selection
within a 1,000-rated-game metadata limit.

Shards are balanced by position count rather than by game count. Each computes
Stockfish 19 MultiPV 3 depth 18 for rapid/blitz, depth 12 for bullet, with up
to four CPU workers. They never connect to Discord or make Fair Play judgments.

The controller simultaneously runs Maia policy and counterfactual analysis on
up to 1,600 deterministic, causal sample positions. It then accepts only
exactly matched completed search results, compares positions across all 250
selected games and publishes **one** Fair Play priority and Owner ZIP.

## Security and failure handling

Only 24-character HMAC-ticket IDs and shard indices enter workflow inputs.
Player games, FENs and move evidence are domain-separated Fernet+gzip encrypted
on the isolated fairplay-distributed-work git ref. Writes use optimistic
fast-forward retries, retaining other concurrent workers' blobs. Filenames,
Git refs and version/engine/depth contracts are strictly checked.

Workers use the dedicated FAIRPLAY_DISTRIBUTED_KEY secret if configured.
Otherwise they reuse the DISCORD_TOKEN as key material (with a separate
derivation context). The dedicated secret is recommended to avoid exposing
reusable Discord credentials to five additional runner environments.

A restarted controller can reconstruct the same pseudonymous ticket.
Incomplete, corrupt, mismatched or unavailable worker results never count as
verified evidence. The controller instead recomputes missing full games
locally with the existing depth-18/depth-12 path. Historical depth-only
selection still has limited sensitivity to intermittent assistance.

Encryption protects the public git ref, but old encrypted blobs may remain
reachable in git history even after the active ref entries are removed.

## Capacity and rollback

Five extra runners alongside two currently active controllers means normally
seven active standard GitHub jobs and thirteen slots free under Free's limit
of twenty, assuming no other account jobs are consuming capacity. The five
runners only start when an owner-issued Fair Play review reaches the deep stage.

Activation is controlled by FAIRPLAY_DISTRIBUTED=1 in Daily Puzzle's Actions
environment and the additional on-demand compute workflow. To revert to the
older local 100+25 game behavior, turn off that flag. Existing Guess Chatter,
minigames and other Discord controllers are unchanged.

## Acceptance and limitations

- Five shards preserve all selected positions and one authoritative score.
- Worker results match expected version, exact Git SHA, Stockfish engine,
  game IDs, FEN, ply, move, requested depth, MultiPV and search exactness.
- Failure or interruption causes explicit local recomputation, no imputation.
- No player-specific high-risk tuning; old LOW/HIGH false-positive safeguards.
- Offline, synthetic and real-Stockfish smoke tests must pass CI.
- Owner performs merge and deployment. No live remote job is started in draft.

The target of about 30–40 minutes for the expanded 250-game deep sample is
a benchmark goal, NOT a confirmed performance result. The measured v21
comparison scan lasted 34m 44s for 125 selected deep games; worker start
delays and central Maia counterfactual searches can reduce the speed benefit.
