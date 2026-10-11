# Fair Play v33: recent-window selection and evidence audit

The prior latest-rated fallback required one exact-control window to equal the
latest ten rated games globally. One interleaved Bullet or other-control game
could suppress that fallback despite a complete recent Blitz/Rapid episode.
The fallback now checks contiguous exact-control windows contained in the
latest fifty rated games. Rating status stays separate; skipped same-control
games still break continuity. This does not combine different clocks.

Published-score windows and recent fallback windows with fewer than eight
comparable opponents are now investigated for context rather than silently
skipped. The eight-peer requirement for the ten-game HIGH route remains
unchanged and is explicitly enforced after investigation. Such a contextual
window cannot set HIGH, even if its model metrics are strong. Incomplete
context is reported, never treated as completed negative evidence, and does
not override a separate supported result.

A robust personal Accuracy change can also allocate full-window review below
the old absolute 85 mean selector: at least eight published scores, current
median at least 80, increase at least 15 points and at least three historical
MADs. The reference uses at least ten earlier published scores from at most
forty games, with the same rated status and exact control and comparable
player/opponent ratings. Candidate games cannot enter their own reference.
This is a heuristic discovery rule, not statistical significance or a priority
rule. Missing scores remain missing; no inferred Accuracy is introduced.

Selected games still require the existing complete depth-18 Stockfish and Maia
contracts. Selection uses game metadata before move evidence is scored.
The existing union of at most 250 context-window games plus the fifty latest
rated games retains the 300-game deep-work cap. Recent fallback games are
already deep-reserved. Earlier relative-shift candidates may add full-game
work; no performance improvement is claimed.

HIGH still requires the existing difficult gameplay, signed quality excess,
paired deep agreement, distributed contributor and learned-policy conditions.
No priority thresholds changed. Engine and Maia gates now return their exact
blockers and signed measurements. Review Gates displays the latest two window
audits before the account-level gates; bounded full audit details remain in
private evidence exports. Review Gates and Human Moves now also use the shared
Discord size limiter; their early returns previously bypassed it. The HIGH
trigger is placed first so truncation cannot hide the actual classification
route. Positive-only average bias fixed in v32 stays fixed.

Validation includes interleaved clocks, missing published scores, recent tails,
relative changes, volatile and rating-mismatched baselines, and context-only
nonpromotion even with artificially strong engine/model metrics. Production
never reads validation labels, target exceptions or account closure metadata.
