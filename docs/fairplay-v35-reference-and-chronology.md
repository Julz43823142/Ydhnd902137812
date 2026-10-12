# Fair Play v35: class chronology and learned-reference coverage

The review keeps Stockfish quality, the heuristic difficulty score, and the
Maia-3 policy comparison as related gameplay evidence. A Maia move likelihood
is a human move reference, not a probability of misconduct.

## Original class order

The collector now records two indices before selecting engine workers:

- `control_index` advances among games with the same rated status, time class,
  base time, and increment. Timing comparisons continue to use this sequence.
- `class_index` advances among games with the same rated status and time class.
  Rapid/Blitz gameplay periods can therefore include different controls in
  their actual chronological order.

Both indices retain gaps when intervening games are not engine-reviewed. A
period with such a gap is not considered contiguous. Old saved fixtures without
`class_index` retain the conservative same-control fallback.

## Maia opportunity geometry

The former learned-reference path required the separate heuristic difficulty
score to exceed 0.45 before Stockfish searched Maia-favored alternatives. That
could discard a position where several objectively good Stockfish moves are
similar but Maia predicts humans will choose substantially weaker moves.

The policy path now requires at least eight legal choices, a valid competitive
nontrivial position, and the existing book, forced-move, automatic-gain, easy
conversion, threat-response, and inconsistent-search exclusions. Legal choice
count is a coverage check only. The information weight grows conservatively
with the logarithm of legal choices and is capped at one.

Actual information still comes from an equal-budget Stockfish evaluation of
Maia's likely alternatives. Unsearched human probability mass is treated as
perfect play, and a further quality margin reduces evidence. Good moves with
equally good human-favored alternatives therefore contribute little or nothing.
The existing signed misses, per-game cap, multi-game distribution, search
stability, and paired deep-confirmation gates remain in force. Timing, result,
and account closure status do not enter this learned-reference route.

The wider opportunity coverage can require additional Stockfish root searches.
Runtime and false-positive behavior require comparison on player-disjoint
controls before interpreting any increase in review priority as improved
detection. HIGH continues to mean human review priority, not a verdict.
