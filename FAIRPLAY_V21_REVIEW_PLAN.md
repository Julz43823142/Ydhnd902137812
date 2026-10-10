# SharkBot Fair Play v21 — scope and acceptance contract

**Status: draft only; owner merges by hand.** Changes do not make automated cheating accusations.

## Inputs and selection (deterministic and label-blind)

1. Collect up to **1,000** latest publicly available, **explicitly rated**, standard
   live games (rapid/blitz/bullet) as metadata, subject to archive availability.
   Existing PGN parser rejects unrated, unknown rated status, unsupported variants,
   daily, abandoned, fewer than 24 plies, or fewer than 8 useful subject decisions.
2. Select up to **100 most recent** games with known player/opponent ratings where
   opponent is **not more than 500 Elo below** the subject. Backfill older
   eligible games until 100 peers are found or the available history ends.
3. Fast-screen **500** rated games: the union of the 100 peer matches and
   up to 400 more recent rated contextual games. These are PV1, 24k-node
   Stockfish searches with separately checked played moves.
4. From remaining peer matches in the fast-screened history, choose up to
   **25** additional games, mixing whole chronological discovery windows
   (PV1 engine CPL trends) and evenly spaced controls. Opponent strength
   is already controlled. **Do not select based on official Chess.com
   Accuracy, closures/bans, or game outcome labels.**
5. Re-run meaningful positions in the 100+25 games at **24k nodes MultiPV 3**,
   using a separate exact-encrypted `fast-pv3` checkpoint contract. This
   restores the missing candidate gaps and valid paired fast/deep comparison.
6. Deep-confirm those same selected games: rapid/blitz **depth 18**,
   bullet **depth 12**, **MultiPV 3**. Store complete PV and played-root
   evaluations; do not substitute a less-complete search.
7. Maia-3 operates on a causal, stratified selection from the deeply selected
   games, up to 800 model predictions (typically 6–8 per eligible game).
   Counterfactual Stockfish roots remain bounded inside the existing worker
   pool. Missing/incomplete Maia data is never treated as independent evidence.
8. **Score only the selected PV3-confirmed games**. Use the fast-only 400
   for discovery/context, not deep cheating verdicts. Retain fast-only game
   decisions in the owner export with explicit per-game depth/search contracts.

## Public score and Accuracy

The code only reads `accuracies.white/black` when Chess.com's published
game archive supplies it; it cannot initiate external players' Game Review.
Missing values remain missing. Descriptive statistics include separate 7, 30,
90 and 365-day rated performance, per time class: win/draw/loss, Elo-adjusted
point excess, rating movement, observed win streak, strongest opponent defeated,
and official Accuracy availability/mean/90+ count.

The official API's `/stats` best/last rating is optional. No account
status, ban reason, personal account name, Chess.com Accuracy or externally
assigned labels can add an independent HIGH vote.

## ETA, resilience, privacy

A new ETA uses **measured throughput in actual positions**, separated by
wide fast PV1, selected fast PV3 and depth-search phases. Projected total
remaining time is a **range**, with larger uncertainty before deep searches
have been observed. First real throughput samples appear after 8 completed
positions and 8 seconds. Store only phase counters, elapsed time and anonymous
processing rates in encrypted checkpoints; preserve state over runner handoff.

The Sharkmeister-only Owner Evidence button returns **one ZIP** containing
the original gzipped manifest and chunks **only if checksum validation succeeds**
and the archive is at most 8 MiB. Otherwise it falls back to original
owner-only .json.gz parts. The original encrypted owner audit store is unchanged.

## Safety and explicit tradeoffs

- No change to HIGH/VERY HIGH numeric thresholds, no ban APIs, no public
  punishments, no inference of fair play from 400 shallow-only games.
- A 100+25 deep sample has less coverage than old 500-game depth18.
  Rare/episodic incidents can be missed. The report must state this.
- "100" is an *up to* number: if fewer than 100 valid rated peer games
  exist in historical archives, do not fabricate or expand outside filters.
- A normal GitHub Actions runner can be interrupted; retain exact completed
  PV1/PV3/deep/counterfactual positions in the existing encrypted checkpoint.
- Low Confidence is not proof of fair play. Positive flags are review priorities,
  and independent properly labelled player-disjoint validation remains needed.

## Required acceptance tests

- The main 500-game breadth stays 500 even when recent games include very
  weak opponents, if a thousand metadata games offer enough valid peers.
- Core is 100, reserve never exceeds 25 and has representative controls.
- Fast PV1 positions cannot be restored as PV3; selected positions must have
  three candidate lines on both fast and deep passes.
- No partial/timeout PV3 or depth18 can become a completed review.
- Maia receives the selected set rather than 500 once-per-game quota.
- Public Accuracy is optional, scoped and never used to pick discovery games.
- ETA produces a total range after actual progress and survives a handoff.
- Single owner ZIP is checksum-verified; no non-owner export.
- Legacy offline suite and all seven GitHub Actions jobs must pass.
- Real-world runtime, sensitivity and false-positive rate remain empirical
  questions. No guaranteed scan time or detection result is asserted.
