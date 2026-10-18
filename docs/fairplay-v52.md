# v52 — readable depth-only reports and experimental review priority

The `FAIRPLAY_V51` switch remains compatible. The reported detector version is
`fairplay-v52-depth-review-priority`. Bullet stays depth 12; Blitz/Rapid depth
18, MultiPV 3. No engine budget, bot process, runner count or production
Accuracy formula is changed. Maia is NOT run in this depth-only mode; Human
Moves says so explicitly instead of implying learned evidence exists.

## Why v51 had no LOW/MODERATE/HIGH

That was deliberate shadow-mode behavior, not an interrupted report. v51
returned a fixed INSUFFICIENT DATA placeholder and rendered observational
charts without a priority. v52 adds a distinct **experimental heuristic
review priority**, never a cheating verdict/probability or punishment trigger.
Coverage confidence describes the data, not validation of this heuristic.

The new module reuses the existing `period_summary` / `class_absolute`
rating-conditioned gameplay tests and their existing configuration. These
require useful off-book opportunities, game-capped high-information hits,
distributed contributors, raw quality excess, anomaly strength, sample-size
lower bounds and known ratings. Raw top-1, high Chess.com Accuracy, title,
account age and ban/closure status cannot produce a priority on their own.
No target usernames or ban labels enter the module.

- INSUFFICIENT DATA: incomplete depth coverage or fewer than 10 rated games
  with the per-game useful-decision minimum / 80 useful decisions.
- LOW: adequate coverage, no qualifying gameplay group. Not proof of fair play.
- MODERATE: existing absolute-gameplay gates pass for a class or one complete
  fixed block, but broader replication is not established.
- HIGH: two or more **disjoint fixed chronological blocks** pass those gates,
  containing at least the existing 20-game normal-HIGH minimum. Blitz/Rapid
  blocks contain 10 games; Bullet's stricter existing rules require 30 each.
  Clocks are optional for this overwhelming-gameplay route.
- VERY HIGH: HIGH, at least the existing 30-game threshold, high coverage
  confidence, repeated same-period trivial/normal/critical clock behavior
  within exact time controls, and at least two actual play sessions.

Blocks align to the newest game, without cherry-picking or overlapping
windows. Low-opportunity rated games remain in chronology. Sessions are
computed on the entire rated class timeline, including intervening games;
removing baseline games cannot manufacture separate sessions. Unrated games
remain chart context and never raise priority. Different time classes never
pool gameplay qualifications; clock support is exact-control only.

These are heuristic safeguards, NOT an empirical human-population model.
Fixed disjoint blocks are repeated observations, not statistically independent
trials. Strong/underrated players, preparation and other innocent explanations
remain possible. New independent positive AND normal holdout validation is
needed before claiming detection performance. No validation-account outcomes
are forced. Single-depth data are never called paired fast/deep confirmation.

## Readability and honest charting

Accuracy axes use the full actual range with padding, bounded by 0–100.
Clock axes similarly follow actual medians rather than a fixed 120s scale.
There is no artificial 20s Bullet cap: a genuinely longer valid median stays
visible. Valid thinks over 120s are no longer silently discarded. Axes clearly
announce zooming; missing observations stay gaps, including isolated official
points. Percent labels/fonts are larger. A fourth PNG compares official
Accuracy against the same 200-Elo bins side-by-side for all three classes,
with sample counts and no imputation or official/estimated mixing.

## Recovery and work efficiency

Full-depth checkpoint restoration now requires the exact MultiPV contract.
Previously PV1/missing metadata could restore successfully and then fail the
final PV3 guard repeatedly. Unknown contracts are re-searched, never certified.
Depth completion remains distinct from evaluation consistency; inconsistent
evaluations remain excluded from Accuracy/gameplay evidence.

Completed futures are checkpointed in completion order, avoiding a slow first
position delaying persistence of later finished positions. Existing verified
positions are skipped individually. In v51, each local completed position
serialized/compressed/encrypted/fsynced the entire growing checkpoint. v52
records complete positions in memory and flushes each 32 positions and batch
end, plus exception cleanup. A synthetic 64-position test verifies the SAME
64 engine analyses and 64 records with only TWO full snapshot flushes.
This is not a measured end-to-end speed percentage or reduced engine time.
A hard kill can redo at most the locally unflushed positions; existing remote
flush throttling can also require replay of recent work. No incomplete
position becomes completed, and no economy state is involved.

## Tests and rollout

Synthetic regressions cover all tiers, ordinary/elite high-quality controls,
timing alone, single-game anomalies, unrated/status invariance, Bullet sample
guards, typed depth certificates, wrong-MultiPV recovery, out-of-order
completion, batching, real PNG output, zoom/missing points and Discord limits.
Private owner evidence is inspected locally, never committed. Accuracy plots
and their jumps are descriptive inputs for moderators, not a separate HIGH
gate. Changes are delivered as a separate draft PR; no automatic merge,
deployment, bot restart, scan or workflow dispatch is performed.
