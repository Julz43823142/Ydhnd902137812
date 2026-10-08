# Fair Play v18: human alternatives and distributed quality discrepancy

The v17 Maia adapter was real local neural inference, but its output mostly
allocated two extra deep games. In addition, quality estimation evaluated only
Stockfish's candidates: a human-preferred move outside the top five received
perfect unknown quality. This conservative bound often erased useful separation.
The change repairs that measurement rather than tuning on account labels.

## Measurement

- The same pinned official Maia-3 5M model runs locally. There is no scan-time
  download, third-party inference API, account-label input or status feature.
- Up to eight position-geometry-stratified decisions per fully scanned game,
  at most 800 per scan. Misses are sampled too; CPL, rank and success are not
  selection keys. Older context-only games do not contribute engine evidence.
- At most three unevaluated human-policy alternatives per position receive
  equal-budget, root-restricted Stockfish searches (24k fast, 320k deep).
  Selection stops at 90% known policy mass or below 2.5% per alternative.
- Unknown probability mass still receives quality 1.0. A further 0.10 quality
  margin protects against model/domain mismatch. Neither bound is an empirical
  confidence interval. An alternative overturning the original evaluation by
  more than 20 cp invalidates the comparison.
- Observed quality minus this conservative expected-quality upper bound is a
  SIGNED residual. Bad decisions remain negative and remain in denominators.
  Exact Stockfish rank 1 is unnecessary: equivalent rank 2/3 moves can count.
- Book, forced, trivial, simple threat response, opponent-error and easy
  conversion decisions remain excluded. Missing data is never evidence.

## Additional HIGH route

This is a related GAMEPLAY family, not independent of CPL/engine/difficulty.
It cannot itself assign VERY HIGH. Timing is not mandatory.

Rapid and Blitz are separate; Bullet is excluded from this new route.
Search fixed chronological blocks of 6, 10, 20, 50 games, the complete class and
the latest corresponding window. Never construct a ranked set of best games.
No multiplicity-derived p-value or misconduct probability is claimed.

A contributing game requires >=4 covered decisions, >=2 excellent decisions
with >=0.20 quality excess, mean signed excess >=0.18 and mean difficulty-weighted
information >=0.12. Each game has equal weight. A period requires >=6 contributor
games AND >=60% of its games contributing, >=max(24, 3*period games) opportunities
and the same average information/residual floors.

At least four contributor games and 16 opportunities must survive full-game
deep review, including misses. Deep information must retain >=75% of its paired
fast reference. At least 75% of deep opportunities must retain objective quality,
expected human-alternative quality and the existing search-stability geometry.
Primary engine coverage must be complete. Missing optional historical context
does not erase supported positive evidence.

All thresholds live in PolicyConfig and are conservative engineering gates,
not trained or empirically certified decision boundaries. Stable elite play,
novel bad moves, easy wins and model/domain failures remain important limitations.
No broader detection-accuracy claim follows from passing synthetic tests.

## Execution and privacy

Reuse the existing bounded Stockfish pool, fixed node budgets, one queued heavy
review and background executor. Preserve the original deep plan and controls;
fill only spare slots inside the existing maximum of 14 games. Additional roots
are measured separately. Cancellation drains work once; incomplete optional
comparisons cannot enable this route. Cached deep games clear stale policy
evaluations and re-evaluate the current reference.

No new persistent state, private case ledger, migration or economy integration.
Human Moves and Review Gates explain coverage, confirmation and blockers.

## References

Official implementation: https://github.com/CSSLab/maia3
Pinned model: https://huggingface.co/UofTCSSLab/Maia3-5M
Model probabilities predict moves, not cheating; platform/time-control calibration
remains incomplete. See fairplay-v17.md for pinned revisions and deployment.
