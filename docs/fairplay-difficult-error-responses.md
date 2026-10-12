# Difficult replies after opponent errors (v34)

The previous pipeline treated every reply after a measured opponent evaluation
error as ineligible human evidence. This included equal positions requiring a
quiet, unique resource. The exclusion appeared in competitive classification,
difficulty, heuristic opportunities, Maia sampling, alternative searches and
learned-policy comparison. Consequently deeper analysis could never recover
the excluded decision.

Keep the measured opponent error as context. Determine whether the actual
reply is informative from its position geometry instead:

- It must remain inside the existing competitive evaluation range.
- Automatic material gains and easy conversions remain excluded.
- Forced, trivial and book decisions remain excluded.
- Difficulty, equivalent alternatives, played quality and rating-conditioned
  excess retain their existing requirements.
- Maia alternatives still require complete, exact, paired engine contracts.

The evidence funnel no longer presents opponent error itself as a mandatory
exclusion. Existing error exposure diagnostics remain available. The change
applies to fast and deep passes, with no priority-threshold adjustment.

## Tests and limitations

Three new regressions failed before the repair: equal-position eligibility,
Maia alternative search eligibility and distributed confirmed gameplay HIGH.
Additional controls cover automatic gains and elite precision. Existing easy
conversion tests explicitly represent automatic replies rather than treating
the error flag alone as proof of simplicity.

150 focused tests passed. The existing bounded engine pool and serial heavy
scan limit remain in force. Additional legitimately eligible positions may
require extra alternative searches, so no end-to-end speed improvement is
claimed. Old exports can be re-summarized for diagnosis but cannot supply
alternative searches that the earlier pipeline never performed. A fresh scan
is required to measure those newly eligible comparisons.

This corrects evidence eligibility. It does not establish calibrated
false-positive rates or guarantee a label for an individual account.
HIGH remains a human-review priority, with the existing mandatory disclaimer.
