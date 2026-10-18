# Private cohort validation

Account-level labels supplied by the owner are useful development evidence, not
proof that every game in the selected archive was assisted. Never feed labels,
account closure status or target identity into gameplay scoring. A ten-account
cohort cannot establish population accuracy or a reliable false-positive rate.

## Explicit analysis profiles

The historical CLI silently defaulted to `history_games=200`. Current production
expects `CONFIG` equality to activate its v21/depth-18 pipeline, and selects a
larger deep scope when distributed mode is enabled. The old CLI could therefore
measure a different detector, even on a host configured like the Discord bot.

Use the explicit production profile to reproduce selection and search budgets:

```
python -m fairplay_validation /tmp/private-cohort.json --live \
  --profile production-local --partition development
```

This now explicitly calls `fairplay_v51_shadow.review`, the deployed
depth-only entry point. The older CLI called `fairplay_analysis.review` even
when the Discord workflow selected v52; that tested v50 under an incorrect
production-equivalence claim. `--profile legacy-production-local` now retains
that older comparison deliberately. Profiles are named in every report.

Production-local preserves `CONFIG`, latest up-to-500 live-game selection and
exact depths (Bullet 12, Blitz/Rapid 18), including unrated chart context but
rated-only priority. Maia is not used by this deployed depth-only mode. The
CLI removes dispatch credentials and uses the exact **local** fallback; it
never schedules workers or posts Discord messages. Install pinned Stockfish
19 first. A complete batch can take hours. Local CPU/cache/transport timings
are not production latency measurements.

The default `--profile screening` is explicitly a different, fixed-node profile.
It cannot inherit live production flags/credentials, even if an overridden quota
happens to make its config compare equal to `CONFIG`. Do not cite screening
labels as deployed Discord results. Production-profile quota overrides that
change `CONFIG` are rejected, rather than silently switching execution paths.

## Development and holdout

Store private input outside the checkout or in an explicitly ignored validation
path. Each entry has a `username` and an existing validation label (`positive`,
`trusted_normal`, `holdout_positive`, `holdout_normal`). Do not commit inputs,
case output, usernames, game URLs or positions. The CLI emits aggregate counts.

`--partition development` is the default and excludes both holdout labels.
Freeze code, input games, engine/model revisions, budgets and evaluation rules
before running `--partition holdout`. Do not repeatedly tune against holdout
results. `--partition all` exists for explicitly non-blind diagnostics only.
The whole cohort is validated before analysis starts. Case-insensitive duplicate
accounts are rejected, including duplicates assigned different partitions.

The analyzer receives only the normalized username. Labels are consulted after
a result is frozen. Failed scans and insufficient-data results remain visible in
coverage counts; no failure is fabricated as LOW. Compare recall, false-positive
rate, abstentions and ranking, not just accuracy on an artificially balanced set.
A gain on development cases alone is not evidence of general improvement.

The report includes pairwise ranking AUC separately for development and holdout:
the fraction of positive/normal research-index pairs ordered correctly, with
half credit for ties. Missing, non-finite and boolean indices do not become
zeroes. The number of available scores and pairs accompanies each value; no
ranking is reported without both classes. This is descriptive on the supplied
cohort, not a misconduct probability or a population accuracy estimate.

Legacy binary metrics retain their existing semantics for consumers. Use the
additional `decided_high_cutoff` to exclude `INSUFFICIENT DATA` from classified
cases: an unclassified normal is not a correct LOW. Use
`end_to_end_high_cutoff` to include failures in the requested-case denominators;
a failed positive scan cannot disappear and inflate apparent sensitivity.
Read these alongside coverage, recall and false-positive rate.

No scoring gates, model weights or production thresholds change in this patch.
