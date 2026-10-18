# Method 3 v60 — separate rated and unrated live comparisons

The fast production method now collects the latest **up to 1,000 unique eligible
standard live games in total**, including unrated and Bullet. Rated and unrated
share this collection quota. Daily, variants, unknown rated status, abandoned and
invalid games are excluded; smaller histories use the available games. PGNs are
validated. No Stockfish/Maia is run and missing official Accuracy is never invented.

Within that history, each rated/unrated cohort has its own latest-200 chronological
priority tail, class periods and strictly earlier matched Accuracy references.
Rated/unrated never contaminate each other's references, history comparisons or
chart lines. Old normal games cannot dilute a recent qualifying period through
an account-wide average. No username or closure status affects this computation.

Existing rated HIGH and Accuracy-based MODERATE boundaries are unchanged.
Unrated comparisons use those same boundaries, but any qualifying flag is capped
at MODERATE with LOW results confidence. Unrated ratings do not update from these
games and can be stale; they cannot independently support HIGH. A rated HIGH or
MODERATE takes precedence over unrated context. Excellent Accuracy alone, a short
upset streak, or wins that Elo already predicts do not automatically establish
misconduct. The earlier development thresholds remain uncalibrated and results
confidence is not confidence in a cheating accusation. VERY HIGH is unavailable.

The short report states rated/unrated counts, official Accuracy availability and
the selected cohort. Details show separate unrated class results and Accuracy
medians. Existing rated charts remain, with up to three extra explicitly labelled
unrated class charts (at most nine attachments). Unrated historical comparisons
are retained in owner diagnostics. Clocks remain descriptive only. Daily is excluded.

## Submission behaviour

Discord still receives an immediate private protocol acknowledgement. Its transient
placeholder is deleted after submission; immediate successful starts have no private
"Review accepted" message. The public progress/report remains. Queued/duplicate,
cached/restarting/full-queue notices and failures are public with mentions disabled.
The 30-minute result cache, concurrency limit, cancellation, and checkpoint storage
remain. Stop/moderator controls retain their existing private responses.

Regression coverage includes cohort separation, rated HIGH preservation, Bullet,
Daily/variant exclusions, stale-rating protection, short upset samples, missing
Accuracy, closure-status invariance, graph rendering, quiet immediate submissions,
public queue/failure notices and hidden internal exception details.

Version: `fairplay-v60-method3-unrated-context`; SharkBot `2.32`.
