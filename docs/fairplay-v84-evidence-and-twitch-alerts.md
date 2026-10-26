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
The selected-period Accuracy coverage is retained in diagnostics. The
Suspicion Score stays unknown when that corroboration is absent, stale or
inconsistent with the selected period, rather than manufacturing a number.

## Twitch

Only the specific missing-authorization Twitch Chat EventSub HTTP 403
(`subscription missing proper authorization`) is recorded once in worker
logs without a recurring owner Discord notice. Further doomed Chat
subscriptions are skipped on WebSocket reconnections **within the same
worker process**. Other optional failures (including 403 for different
reasons) and critical Channel Points subscription failures still alert
the owner.

This does **not** grant Twitch Chat permissions. Chat commands remain
unavailable until the OAuth scopes are corrected and a worker restarts.
Other Twitch subscriptions continue to operate independently.

## Efficiency audit

- Accuracy-frequency charts previously calculated comprehensive per-player
  Elo/move-length medians for every display-only bucket. v84 counts official
  Accuracy coverage and threshold numerators directly in one pass, preserving
  missingness and every displayed denominator.
- Rolling 100-*played*-game Accuracy chart trends previously copied and rescanned
  each 100-game window at each sampled point and each threshold. v84 uses
  incrementally maintained numerators and denominators in a single chronological
  pass. Off-stride final samples and unknown-only gaps are unchanged.
- Earlier personal Accuracy references previously scanned the entire history
  on first access to every distinct rating context. v84 indexes earlier games
  by exact time control and own Elo, then applies the original full matching
  predicate and chronological prefix constraints. It still includes only
  already-played reference games at every searched boundary.
- Benchmark with `python scripts/benchmark_fairplay_v84_plot.py --games 5000
  --iterations 25`. It asserts exact equivalence against preserved old
  calculations. Speedups apply only to synthetic chart calculations, not to
  network fetch, PGN decoding, Stockfish/Maia, Discord, or full review latency.
- Indexed-reference tests compare all stored statistics and cryptographic
  reference-pool keys against a brute-force original-style implementation,
  including missing ratings, partial coverage, control changes and boundaries.

## Safety and verification

Regression tests check the same-period requirement, missing/sparse
published Accuracy, LOW-to-MODERATE preservation with valid evidence,
and silence only for the optional Chat 403 while retaining important
error notifications. Existing Fair Play and other bot CI checks must
pass before merge. Merging a code push can rotate the Daily Puzzle
controller and interrupt an active Fair Play scan; review the current
workflow before deployment.
