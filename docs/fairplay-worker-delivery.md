# Worker delivery from filtered checkouts

## Reproduced failure

A real `blob:none` clone can list the encrypted exchange's tree without having
all of its blobs locally. `git mktree` rejects those inherited entries with
“object ... is unavailable”, even though they exist on the remote. This prevents
progress and final-result writes and causes local recovery. A shallow fetch alone
does not fix it. The new partial-clone regression reproduced the exception before
the repair; existing full-clone tests did not cover this combination.

The compute snapshot now uses `mktree --missing` for unchanged object IDs obtained
from the fetched remote tree. New ciphertext is still written and its object ID
validated locally. Remote publication remains a non-forced fast-forward push,
with conflict/retry handling; another worker's encrypted result is preserved.
Cleanup uses the same rule. No remote history is rewritten or deleted.

Workers acknowledge validated work before their first engine search. An
acknowledged worker is subject to the existing progress/deadline limits rather
than the no-worker-started timeout. Fixed failure codes distinguish missing
engine evidence, invalid data and Git snapshot failures. Unknown error text is
never copied into diagnostics. Engine Analysis displays verified workers,
failed/stalled workers, remote games and local recovery counts.

## Validation and limits

The regression uses real local Git transport, a filtered clone, absent promised
blobs, actual publication and cleanup. It verifies that unrelated ciphertext is
preserved without downloading it. Existing concurrent-publication tests remain.
Read-only hosted probes deserialized all 14 real workload groups. One original
worker completed all 14 assigned games in 3 minutes 10 seconds when remote writes
were simulated; its result serialized successfully. A real filtered repository
reproduced the subsequent tree-write failure, and succeeded after this repair.
This is not a completed production scan or a whole-review speed benchmark. Deployment must use the new coordinator
revision: compute jobs intentionally pin their code to the requesting bot.

No detector threshold, account-specific rule or classification changed here.
Runtime improvement depends on workers returning verified results; no fixed
speedup or guaranteed HIGH is claimed.

## Research reviewed

- [Maia-3 official implementation](https://github.com/CSSLab/maia3): human move
  prediction conditioned on player/opponent strength, not a calibrated
  misconduct classifier. The referenced Chessformer paper is presented by the
  authors as ICLR 2026 research.
- [Irwin implementation](https://github.com/clarkerubber/irwin): trained pattern
  analysis and moderator assistance. Its setup requires a substantial training
  database, not a pair of accounts used repeatedly as target labels.
- [Regan's Intrinsic Ratings Compendium](https://www.cse.buffalo.edu/~regan/papers/pdf/Reg12IPRs.pdf):
  a working draft describing scaled move-value differences and calibration on
  established players; it cites Regan and Haworth's AAAI 2011 *Intrinsic Chess
  Ratings*. It does not validate SharkBot's heuristic thresholds.

These sources support separating model disagreement, data coverage and review
priority, and using held-out validation across strengths. They do not justify
turning a particular account HIGH because the owner expects that label, nor
counting correlated Stockfish/Maia-derived features as independent evidence.
