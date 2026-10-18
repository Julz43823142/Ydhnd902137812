# v55: configured Bullet review priority

This is an **owner-requested worklist rule**, not a learned cheating detector
or an empirically calibrated Accuracy-by-Elo norm. It deliberately changes
manual-review priority without changing the underlying gameplay assessment.
An honest strong player can satisfy it. No detection improvement, sensitivity,
specificity or cheating probability is claimed.

The v54 Stockfish/recent-gameplay assessment runs unchanged. Its priority and
reasons remain in `gameplay_priority` / `gameplay_reasons` and are shown on
the public card. Only then does the explicit consistency overlay run. If it
passes while gameplay priority is LOW or MODERATE, the worklist priority
becomes HIGH, with **configured manual-review rule** as its stated basis.
It cannot produce VERY HIGH or upgrade INSUFFICIENT DATA. Existing HIGH and
VERY HIGH gameplay conclusions keep their own reasons.

## Exact rule

Use only the existing latest-200 completed rated live-game sample. Group
Bullet by exact base time/increment, keep chronological order including all
misses, and partition into non-overlapping thirty-game blocks aligned to the
latest game. Do not rank/select isolated successful games. Two qualifying
disjoint blocks in one control are required. They need not be adjacent; the
report shows their full chronological span and adjacency explicitly, and
intervening failed blocks remain in the audit. These are not independent
statistical trials or proof of continuous assistance.

Every qualifying block requires:

- all thirty games complete at the configured exact Stockfish depth/MultiPV;
- no duplicate games or unavailable required archive months;
- published official Accuracy for at least 24/30 games;
- published median at least 85, lower quartile at least 80 and MAD at most 7;
- at least 21/30 games with published Accuracy at least 80; missing scores
  remain in the thirty-game denominator;
- known player/opponent ratings for at least 24/30 games;
- median player rating at most 2200 and median opponent rating at least
  player median minus 150;
- at least 100 competitive, useful, non-book/non-forced/nontrivial decisions
  without obvious conversion, opponent material gifts or inconsistent search;
- at least fifteen games contributing three such decisions each.

The combined required archive span and uniqueness are checked again across
the two blocks. Daily, Rapid, Blitz, unrated games, account age, account
status, bans, usernames, desired labels and move-time signals do not enter
this rule. The numerical thresholds are explicit owner review preferences,
not estimates of what a human can achieve. The 2200 ceiling limits the
configured workload; it does not establish legitimacy above that rating.

## Controls and limitations

`FAIRPLAY_BULLET_CONSISTENCY_TRIAGE=0` disables only the overlay. Default is
enabled for the requested production workflow. Existing gameplay gates,
Stockfish depths, Maia sampling, formula, channel routing, queue and evidence
are unchanged. The overlay performs no new HTTP/model/engine searches.

The v54 statement that published Accuracy never changes *any* priority is
superseded only for this separately labelled worklist rule. Accuracy still
does not change the gameplay assessment. Maia confirmation is not invented
or implied when this rule triggers. Coverage confidence is not misconduct
confidence. The public card shows both priorities and the distinction.

Missing, selective or erroneous published scores can limit this rule. High
consistent scores can result from honest skill, preparation, position ease,
opponent behavior or an underrated account despite these context guards.
Human review is mandatory; no punishment, reporting, economy action or
account-specific exception follows automatically.

Local validation reports worklist counts separately. Recall/false-positive
metrics continue to use the underlying gameplay assessment; a configured
priority promotion must never be presented as improved detection accuracy.
