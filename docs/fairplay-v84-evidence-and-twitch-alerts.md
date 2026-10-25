# Fair Play v84 — period-aligned corroboration and Twitch alert noise

## Fair Play false-positive guard

The exploratory LOW-to-MODERATE `combined_manual_review` route previously
used sparse published Accuracy from the subject's *entire time class*. That
signal could be derived entirely outside the selected 100+ game results
period. The two observations were not necessarily contemporaneous, even
though the Discord summary described them as combined evidence.

v84 requires observed, finite official Chess.com Accuracy in the exact
selected time-class chronological period: at least 30 covered games,
at least 60% selected-period coverage, at least 15 identified opponents
among the covered games, 10 Accuracy >=90, and 5 >=95. The broader
historical sparse-Accuracy observation and the already existing results,
rating, opponent and chronology safeguards must also pass.

No account names, player titles or allowlists participate in this rule.
This is a conservative evidence gate, **not** independent population
calibration or engine confirmation. The combined path remains capped
at MODERATE manual-review priority. HIGH/VERY HIGH rules are unchanged.
The selected-period Accuracy coverage is retained in diagnostics.

## Twitch

A 403 for the optional Twitch Chat EventSub subscription, including
`subscription missing proper authorization`, is recorded in worker logs
without generating a recurring owner Discord notice. Twitch Channel
Points subscription failures still alert the owner; other optional
subscription failures still alert. This does **not** grant Twitch Chat
permissions, and chat commands remain unavailable until the OAuth
authorization is corrected.

## Safety and verification

Regression tests check the same-period requirement, missing/sparse
published Accuracy, LOW-to-MODERATE preservation with valid evidence,
and silence only for the optional Chat 403 while retaining important
error notifications. Existing Fair Play and other bot CI checks must
pass before merge. Merging a code push can rotate the Daily Puzzle
controller and interrupt an active Fair Play scan; review the current
workflow before deployment.
