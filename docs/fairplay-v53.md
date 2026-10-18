# v53 — recent gameplay change and reproducible private validation

The existing `FAIRPLAY_V51=1` runtime switch and full-depth engine pipeline
remain. **Production keeps the v52 scoring rules and version.** The local-only
candidate is `fairplay-v53-relative-gameplay-research`; it requires the explicit
validation CLI profile `candidate-local`. Discord and environment variables
cannot select it. Bullet stays depth 12; Blitz/Rapid stay depth 18, MultiPV 3.
No production Accuracy formula, economy state, channel routing, runner count
or Discord process changes.

The new scoring profile passed synthetic regressions but did **not** improve
the completed real-cohort classification target. It must not be presented as
a validated detector improvement. Validation-entry, forensic and explanation
fixes apply to production; the candidate scoring changes below do not.

## Verified architectural blind spots

The production validation CLI previously called `fairplay_analysis.review`
(v50) even when Discord selected the depth-only detector. The local profile
could therefore claim deployment equivalence while measuring another system.
`production-local` now explicitly calls `fairplay_v51_shadow.review`.
`legacy-production-local` deliberately retains the older comparison;
`screening` remains a separate fixed-node diagnostic, never a deployed review.
All local profiles remove dispatch credentials and do not post to Discord.
Depth-only results now populate the common forensic timeline. Saved older
depth-only results fall back to their reviewed games when exporting decisions.
The old ablation helper explicitly rejects depth-only input rather than
silently recomputing it with the unrelated v50 scorer.

Review Gates now include fixed-block opportunity/contributor counts and exact
block-level failures, including Bullet-specific checks. A weak whole-history
average no longer hides the explanation for an individual recent block. The
closest block is for explanation only and cannot change the production tier.

v52 required two qualifying disjoint ten-game blocks for non-Bullet HIGH.
One anomalous recent ten-game block could only reach MODERATE, regardless of
how different the earlier comparable games were. A whole-history average also
cannot establish the absence of a short episode. The local v53 candidate keeps
the existing broad route and adds a separately documented recent-change route.

## Relative quality and feasible ceiling guards

The legacy human heuristic remains reproducible with its default arguments.
The local candidate profile explicitly re-annotates decisions using the new
`relative=True` quality profile after the exact engine pass. This changes no
Stockfish score, search budget, opening/forced exclusion or Accuracy formula.

Previously every anomaly hit needed observed quality >= 0.85, even when a
strong #2/#3 move materially exceeded a low-rating reference. The relative
profile instead requires observed quality >= 0.65 AND at least half of the
remaining reference headroom, alongside the existing raw-margin, anomaly,
difficulty and competitive-position requirements. Higher references require
correspondingly stronger decisions; generic high Accuracy is not evidence.

Another old gate could require raw excess 0.175 when the reference was 0.97,
although even perfect observed quality could only exceed it by 0.03. The
relative profile caps the move/period raw floor at **90% of actual remaining
headroom**. At reference 0.97, the required period quality is therefore at least
0.997. Small ceiling residuals still fail. These unitless quality indices are
not Chess.com Accuracy or misconduct probabilities. The constants are shared
in `fairplay_human.py`; the old profile remains an explicit comparison.

## Distributed whole-period HIGH

Two ten-game blocks each needing 40 opportunities accidentally required 80
opportunities to raise a twenty-game period, despite the whole-period rule
requiring 40. The new route retains the full class/period quality and sample
gates, then requires separately supported chronological halves with proportional
coverage floors. It examines fixed, newest-aligned partitions at the existing
configured widths and the whole class; overlapping widths are alternatives.

Normal HIGH still needs at least 20 games. At least 60% of ALL games must
contribute, both overall and in each half; games without opportunities stay in
that denominator. Each half retains the absolute quality/rating gates with at
least 20 opportunities and a proportional contributor floor. Bullet needs the
existing stricter whole-period 30-game / 100-opportunity / 20-contributor and
0.90 hit-lower-bound qualification; halves need at least 15 games, 50
opportunities, ten contributors and a 0.85 lower bound. This route caps at HIGH.
It does not claim the halves are statistically independent trials.

## Recent change route — maximum HIGH

`fairplay_recent_shift.py` evaluates only the latest **10 / 20 / 50** rated
games separately within Rapid and Blitz. These are alternative views of the
same endpoint, not independent replications. Low-opportunity games stay in
chronology. Bullet retains its broader existing safeguards.

The earlier comparison includes the latest at most 100 prior games with:

- the same time class and a time control present in the recent sample;
- player/opponent ratings within the existing 200/250 Elo calipers;
- rating-gap difference within the existing 200 Elo caliper;
- comparable control mixture (total variation distance at most 0.20).

Selection never consults move quality, published Accuracy, wins, labels, names
or closure information. Earlier games are a comparison, not certified clean
play. Missing context is missing evidence, not zero Elo or suspicious data.

Every required check must pass:

- complete required engine coverage and no missing archive inside the required
  comparison interval; a missing older optional archive does not block it;
- the existing rating-relative gameplay quality, contributor, information and
  Wilson requirements, with a recent floor of 20 opportunities (or twice the
  number of opportunity-bearing games, whichever is greater); the separate
  earlier comparison still needs 40 opportunities;
- at least 20 comparable earlier games and 40 earlier difficult opportunities;
- contributing games in both recent halves (at least two / one third each);
- median difficult-decision quality rises by at least 0.25;
- median rating-adjusted signed quality excess rises by at least 0.20;
- robust effect at least 1.5, using 1.4826 × earlier MAD with a 0.05 spread floor;
- recent hit lower bound exceeds earlier hit upper bound by at least 0.15;
- unique games, without repeated identities manufacturing replication.

These are centralized **heuristic safeguards**, not calibrated probabilities.
Intervals describe the sampled hit fractions; correlated chess decisions and
multiple window views are not independent statistical trials. Published
Chess.com Accuracy is not an input to this route. A graph jump alone cannot
produce HIGH. Improvement, preparation, underrating and other innocent
explanations remain possible. No punishment follows automatically.

The result identifies the triggering route and the selected comparison.
Review Gates / Engine Analysis / Clusters & History show opportunity counts,
quality changes, the covered whole-period route and exact missing checks.
Existing disjoint-block HIGH and stricter VERY HIGH remain separate. A recent
change or new whole-period qualification alone cannot create VERY HIGH.

## Compute and validation privacy

No additional engine searches are needed by recent-change scoring. It reuses
the completed, game-capped engine features, including misses; it does not
invent Maia probabilities or paired fast/deep stability. The deployed depth-only
mode still does not run learned Maia inference.

An opt-in mode of the existing manual research workflow supports an explicitly
owner-requested private cohort. It has no schedule. Fourteen bounded jobs use
two single-thread engines each, with a 150-minute job ceiling, a default
20-minute compute checkpoint and an explicit maximum of 110 minutes. Complete
same-engine/config/feature-source/depth positions can resume; mismatched/partial contracts
are re-searched. Public workflow inputs contain only opaque references.
Normal research behavior remains the default when that input is empty.

Each worker publishes an ephemeral RSA public key, never its private key.
The local investigator seals inputs using RSA-OAEP/SHA-256 and AES-256-GCM.
Authenticated compressed input and output sizes are bounded. Only ciphertext
is published on a separate temporary ref. Outputs are encrypted for a separate
local key which never enters Actions. Artifacts retain public keys/ciphertext
for one day. No production key, token extraction, Discord token, target names,
labels, PGNs or FENs enter public inputs/logs. Existing production workflows,
queues and state refs are not modified. Partial work is not a completed case.

For a priority-only comparison, every rated game inside the SAME production
selection must be fully certified. Unrated games cannot affect this scorer
and need not be re-searched merely to compare priorities. This does NOT certify
a completed Discord report or complete unrated chart coverage. Case counts
and these two kinds of coverage must be reported separately. Old cached raw
features are re-annotated for the selected quality profile; old and new labels
are compared without mixing their derived hit definitions.

Validation labels are consulted only after results freeze. Physical games
shared between accounts must stay within the same development/control
partition. A current-pass control split among previously supplied accounts is
not a historically untouched external holdout, nor proof of population accuracy.
Account closure does not establish which particular sampled games were assisted.

## Regression evidence

Tests cover recent extreme play after long ordinary history, pure published
Accuracy jumps, one extraordinary game, steady strong/elite play, easier
opponents, missing ratings/controls/coverage, intervening low-opportunity games,
unrated games, status changes, duplicate identities, Bullet safeguards and
missing optional versus required archives. Transport tests cover authenticated
round trips, wrong keys, tampering, bounds and opaque ref validation.

Real cohort outcomes must be reported separately from synthetic test results.
The requested label distribution is an evaluation target, not a production
rule, and must never be manufactured by identity overrides or ban metadata.

## Completed private evaluation and rejected hypothesis

All eleven requested accounts had complete **rated priority inputs** under the
frozen production selection: 3,342 rated games, with every required decision
certified at the exact production depth/MultiPV. Unrated chart-only games were
not all searched; this is not eleven completed Discord reports. Both v52 and
the local v53 heuristic candidate returned LOW on all eleven accounts. Thus
the requested five-of-six positive HIGH target failed (zero of six HIGH), while
all five owner-supplied normal controls remained LOW. This small, supplied
cohort is not an estimate of population sensitivity or specificity.

The tested Maia-3 experiment used the pinned local 5M policy, causal history,
all decisions in the latest up to 100 rated games and separate Rapid/Blitz
chronological windows. A bounded human/engine contrast included misses, capped
each game's contribution, and used a conservative pointwise likelihood envelope
over rating references 0/+300/+600. That envelope was frozen before inspecting
the current-pass holdout outcomes. It reduced one development-control failure,
but still crossed the preregistered research cutoff on an elite holdout control,
while four of the six supplied positives did not cross it. Those are raw
research-cutoff outcomes, not fully confirmed review tiers. The hypothesis was
rejected for production; it is not imported by bot routing or used to assign
HIGH. Exact code, protocol and per-case output remain private outside Git.

More engine depth alone did not fix the feature/aggregation problem. A local
learned human move policy is also not a calibrated misconduct model; strength
conditioning and a rating envelope did not eliminate domain/style mismatch.
The next validation needs independently labeled game periods and broader
normal-player reference coverage. Do not repeatedly tune these eleven accounts
and then call them a blind or external holdout. Account closure cannot provide
the missing game-level labels and remains excluded from all scoring.
