# Evidence revalidation (v33.1)

Reannotating a decision after it becomes non-useful previously returned before
clearing its derived human-opportunity, quiet-hit and search-stability fields.
That left stale fast-pass evidence on an excluded move. A depth-complete,
explicitly search-inconsistent move could consequently fail the ten-game
window's exclusion safety check and make the window appear incomplete.

Clear those derived fields before returning. Preserve the independent fast
snapshot and the engine contract; neither a missing policy nor an incomplete
search becomes valid. Re-included decisions are recomputed normally.

Overlapping ten-game windows now reuse each game's completeness check within
one integration call. Gap counts are also reused per game and still summed for
each window. This avoids repeatedly reconstructing legal-move sets for the
same Maia policies. The cache cannot survive a rescan or a policy update.

## Validation and limits

- A new regression failed on the previous code with a stale opportunity flag.
- An integration test covers exclusion followed by full-window validation.
- Re-inclusion and repeated annotation preserve the expected game summary.
- Thirty games forming 21 overlapping windows require 30 completeness checks,
  rather than 210. Invalidating a policy on a later call is still detected.
- 118 focused tests passed, including strong-player controls, recent gameplay
  positives, ten-game window safety, and account-status independence.
- A synthetic 200-game, 40-move-per-game benchmark covers 191 windows and
  compares the complete output with the previous implementation. Results must
  remain identical. This measures report post-processing, not Stockfish or
  total scan runtime.

No priority thresholds changed. A recent confirmed gameplay episode can still
produce HIGH without being averaged into the entire history. This patch does
not establish that any particular account should receive a different label,
nor does it calibrate the detector's false-positive or false-negative rates.
Human review remains mandatory.
