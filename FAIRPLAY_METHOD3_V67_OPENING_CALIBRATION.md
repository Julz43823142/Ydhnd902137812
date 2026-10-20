# Method 3 v67: early-account Elo calibration and /fairplay group

## Why

A results-only search can mark an early Elo ascent HIGH while assuming every
published historical rating is a well-calibrated estimate of stable skill.
Example evidence inspected privately: a verified 2022 newly created account
with an opening 100-game Blitz run, median published Elo ~862 in its first 10
and ~1597 in the final 10 (+735) with 87/100 scored points. Published Accuracy
was available in only 8/100 games. **These data do not establish cheating
or fair play.** Do not commit raw account evidence or labels as validation.

## Guard (conservative, not a proof of innocence)

`_opening_rating_ramp` requires:
- A trustworthy public profile `joined` Unix timestamp.
- The first observed rated game of that exact time class falls within 90 days
  after joining, to avoid mistaking truncated archives for account creation.
- At least 40 valid same-control rated games; first 40–100 completed within
  400 days, with >=500 Elo difference between first/last 10 rating medians.
- A flagged period ending in the first 150 rated games of the control and
  no more than one year after the calibration sample.

A matching initial rating ramp caps **results-only** HIGH/VERY HIGH to
MODERATE for human triage; otherwise the detector is unchanged. It does
not erase the original residual, alter Elo-derived expected scores, claim
a clean account, infer engine use, or use later ratings as substitutes.
Unverified joined dates **do not** activate the guard; the missing-archive
caveat remains.

## Commands

Two separate, flat Discord slash commands preserve the direct username input:
- `/fairplay username:<Chess.com name>` for screening.
- `/fairplaystop` (one word) for a moderator-authorized cancellation.

The obsolete `/stopfairplay` is removed after command sync. Discord's
client-side command cache may take time to refresh after deploy.

## Validation and limitations

Synthetic regressions cover account-creation verification, an early
rating ramp, an unchanged old-account HIGH case, non-propagation to later
windows, production routing and slash registration. A real Chess.com
evidence file is not a verified ground-truth cheat/honest label. Before any
claim of improved recall or false-positive rate, run frozen, blinded
account-audited holdouts on v66 and v67 and report HIGH/MODERATE/LOW counts,
false-positive rate, recall and runtime. `HIGH` is still a workload
priority, not a verdict.
