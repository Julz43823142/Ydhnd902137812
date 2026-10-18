# v53 — recent gameplay change and reproducible private validation

The existing `FAIRPLAY_V51=1` runtime switch and full-depth engine pipeline
remain. Version: `fairplay-v53-recent-gameplay-shift`. Bullet stays depth 12;
Blitz/Rapid stay depth 18, MultiPV 3. No production Accuracy formula, economy
state, channel routing, runner count or Discord process changes.

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

v52 required two qualifying disjoint ten-game blocks for non-Bullet HIGH.
One anomalous recent ten-game block could only reach MODERATE, regardless of
how different the earlier comparable games were. A whole-history average also
cannot establish the absence of a short episode. v53 keeps the existing broad
route and adds a separately documented recent-change route.

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
- all existing absolute gameplay requirements for the recent window, including
  rating coverage, capped opportunity/contributor counts, signed quality
  excess, information and Wilson hit lower bound;
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
quality changes and exact missing checks. Existing broad HIGH and stricter
VERY HIGH remain separate. A recent-change result alone cannot create VERY HIGH.

## Compute and validation privacy

No additional engine searches are needed by recent-change scoring. It reuses
the completed, game-capped engine features, including misses; it does not
invent Maia probabilities or paired fast/deep stability. The deployed depth-only
mode still does not run learned Maia inference.

An opt-in mode of the existing manual research workflow supports an explicitly
owner-requested private cohort. It has no schedule. Fourteen bounded jobs use
two single-thread engines each, with a 150-minute job ceiling, a default
20-minute compute checkpoint and an explicit maximum of 110 minutes. Complete
same-engine/config/depth positions can resume; mismatched/partial contracts
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
