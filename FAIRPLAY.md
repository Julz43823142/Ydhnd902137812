# Fair Play Review v4

This is a moderator screening tool, not a cheating verdict or a calibrated probability. It performs no punishments, reports, wallet mutations or rewards. Missing evidence is never suspicious. False positives and missed cases remain possible; human review is mandatory.

## Architecture and runtime

- `fairplay_data.py`: validated usernames, fixed-host serial Chess.com PubAPI requests, PGN/clock extraction and eligibility.
- `fairplay_analysis.py`: one low-priority, one-thread Stockfish process; fixed-node searches, same-root evaluation loss, pipeline and bounded process-local cache.
- `fairplay_clusters.py`: continuous evidence strengths, exact-control windows, approximate sessions, recurrence and cluster-specific deep confirmation.
- `fairplay_history.py`: cheap extended-history metadata, bounded engine discovery probes and adaptive historical candidates/control games.
- `fairplay_baseline.py` / `fairplay_timing.py`: personal timing profiles, trivial-move delays, premoves, dispersion, complexity response and behavioral changes.
- `fairplay_scoring.py`: conservative priority gates, confidence and report diagnostics.
- `fairplay_ui.py`: existing Discord bot, isolated channel, persistent submission/report views, one movable one-hour idle panel, bounded queue/executor and one public progress card.
- `fairplay_validation.py`: optional local calibration CLI, never imported by the production bot.

Default `ReviewConfig`: `history_games=500`, `primary_engine_games=100`, `max_archives=36`, runtime deadline 1,800 seconds. The full fast pass on the newest 100 eligible games runs first, newest-first; reporting restores chronological order. Older history receives metadata/clock profiles and four evenly spaced 2,000-node discovery probes per game. Probes never count as fully scanned or scoring-eligible games. Their stage has a soft 12% runtime allocation. Exact-control eight-game changes in probe quality or raw timing nominate historical periods, plus six surrounding control games on each side, with at most 40 extra full fast scans. Stable history selects no additional full historical scans. Engine-only changes may be missed by sparse probes; this is a bounded discovery strategy, not exhaustive historical engine coverage.

Fast searches use 12,000 nodes / MultiPV 3. Deep searches use 160,000 nodes / MultiPV up to 5, reserved for critical/ambiguous rankings; ordinary deep positions retain MultiPV 3. Up to ten games are selected from the strongest chronological group, critical evidence and a second period where useful. Clearly decisive fast positions beyond ±800 cp are not needlessly deep-searched. Fast positions beyond ±600 cp do not contribute engine evidence; positions between ±300 and ±600 cp have reduced evidential weight. CPL is bounded at 1,000; mate scores are normalized safely and decisive mate evaluations provide no engine-matching evidence. This version retains CPL rather than adding an uncalibrated WDL model.

All heavy work remains in the existing dedicated executor, not Discord's asyncio loop. Threads=1, Hash=64 MB, deterministic nodes and per-position hash clearing; one scan at a time with duplicate-target reuse. Transport timeout is eight seconds per engine command. Deadline expiry preserves finished fast results and lowers coverage/confidence; it does not invent a completed deep pass. The public card distinguishes collection, full scans, scoring inclusion, deep reviews, exclusions and incomplete extended discovery. No new environment dependency beyond the existing working Stockfish 19/python-chess setup.

## Eligibility and clocks

Only completed standard Rapid, Blitz or Bullet games, from the standard initial position, with a valid PGN, at least 24 plies and eight structural non-opening decisions qualify. Variants, correspondence, abandoned games, malformed PGNs, custom starts, short games, duplicates and unavailable archives have separate exclusion counters. Ineligible rows do not consume the eligible-game limit. Eligible games use completion-time ordering.

The first 20 plies contribute no engine-matching evidence. Only genuinely forced/trivial structural proxies are excluded: one legal move, near-forced check responses, obvious single recaptures, simple exchanges and sparse-board immediate mates. Difficult check responses and nontrivial recaptures remain analyzable. Proxies are imperfect and documented as such.

Clock reconstruction uses previous same-side clock + increment − new clock. Missing clocks break the chain; first moves and unknown controls do not produce invented times. Negative times, times ≥120 seconds, severe time trouble (≤10 seconds or 5% of base time) and unsupported clocks are excluded. Premoves ≤0.5 seconds are separate from genuine delayed trivial moves. Openings retain raw timing for personal comparison, never engine agreement. Rapid/Blitz/Bullet and exact base+increment stay separate. Bullet contributes only 35% signal weight. Missing clocks can coexist with HIGH engine confidence.

## Exact evidence model and priority gates

These are explainable heuristic thresholds, not empirically calibrated norms. Parameters are centralized in `fairplay_config.py`.

A meaningful engine decision has a continuous weight:

```
min(1, 0.3 + legal_choices / 30)
× (0.85 for tactical moves, otherwise 1)
× (0.5 beyond ±300 cp, otherwise 1)
× min(1, 0.25 + best_second_gap / 180)
```

Forced/trivial/opening/decisive decisions have weight zero. Critical positions require ≥6 legal choices, best–second gap ≥100 cp and candidate spread ≥180 cp; unique choices require gap ≥180 cp. Quiet and tactical critical opportunities are counted separately. Equivalent top candidates have weak matching evidence.

Let `ramp(x,a,b)=clamp((x-a)/(b-a),0,1)`. Rating bands (<1000, 1000–1399, 1400–1799, 1800–2199, ≥2200) have indices `b=0..4`. Unknown strength uses `b=0`; a sustained personal regime change uses the common baseline so a high rating/title cannot erase the anomaly.

```
engine = (0.65 × ramp(weighted_top1, 0.62+0.025b, 0.96)
        + 0.15 × ramp(top3, 0.82+0.015b, 1)
        + 0.20 × (1-ramp(robust_CPL, 5, 60)))
       × effective_decisions/(effective_decisions+35)
       × sqrt(effective_decisions/raw_decisions)

critical = (0.75 × ramp(critical_top1, 0.50+0.035b, 0.96)
          + 0.25 × ramp(unique_hit_rate, 0.50+0.035b, 0.96))
         × critical_count/(critical_count+8)
```

Agreement denominators are explicit; CPL comparisons use robust equal-game summaries. Absolute full-control and persistent-cluster evidence are complementary: the strongest sustained period is not averaged into the full-sample score. Windows are 5/6/8/10/15 games; ≥6 games and ≥80% repeatedly precise games establish persistence. Five-game windows aid discovery, never the high-priority persistence gate. Approximate sessions use a 45-minute inactivity gap. Ranked subsets are descriptive, not chronological evidence. Targeted samples never bridge unscanned eligible games. Overlapping windows are deduplicated; disjoint windows within a single uninterrupted plateau do not establish recurrence. Recurrence needs a separating lower-anomaly same-control interval.

Supporting families:

- Timing: repeated narrow cadence and trivial/ordinary/critical overlap, plus personal behavior shifts. Personal grades Slight/Moderate/Strong/Very Strong map to .25/.50/.75/.90. Generic cadence alone caps at .70. Trivial delays preserve timing but contribute zero engine matches.
- Performance: sustained adjacent 5/8/10-game robust changes, not one good game; ≥25 cp and ≥2.5 baseline MADs, plus engine/critical agreement gain and ≥80% persistence. Repeated within-game transitions also retain earlier/later decision, CPL, agreement and cadence summaries; five independently recorded non-Bullet game transitions supply .55 supporting strength. Supporting chronological strength is `min(.95,.5+.05×effect_MAD)`; Bullet is reduced. Win/loss comparisons use the same fast-search summaries throughout and require ≥8 each, comparable opponent ratings (within 250) and decision counts (within factor 2).
- Context: new account ≤.10; rating gain ≤.35; Elo excess ≤.65. Context cannot independently create HIGH.

The diagnostic weighted review strength retains weights .30/.30/.15/.15/.10. It is **not** a probability or the decision gate. Gates deliberately avoid the old unreachable categorical `.55` versus `.65` threshold and the mandatory-both-family bottleneck:

- **INSUFFICIENT DATA:** fewer than 15 scoring-eligible games or 150 meaningful decisions.
- **HIGH:** engine ≥.65 **or** critical ≥.65 with ≥20 critical opportunities; a persistent meaningful period; deep confirmation of the core signal; independent timing/performance/context ≥.50 or distinct recurrence; confidence not LOW.
- **VERY HIGH:** additionally max(engine,critical) ≥.88 and min ≥.65, independent support ≥.75, ≥30 games / 500 decisions / 60 critical positions, ≥10-game period / eight deeply reviewed games within it, HIGH confidence and no partial scan.
- **MODERATE:** elevated engine/critical evidence ≥.50 or diagnostic weighted strength ≥.30 without adequate higher gates.
- **LOW:** no supported combination; this never proves fair play.

Deep confirmation is tied to the strongest *fast* period, not an arbitrary mixed set of ten games. It requires five deeply reviewed games / 60 decisions, core engine or critical strength ≥.65, ≥20 deep critical opportunities for the critical path and ≥80% fast-to-deep retention for that specific family. A different surviving family cannot conceal a collapse in the core family. High-priority gates use the confirmed strength; deep attenuation downgrades the result.

Confidence is separate: HIGH requires ≥30 scoring games / 500 decisions / 60 deep decisions and complete core coverage; MEDIUM requires ≥15 / 150; otherwise LOW. Per-family timing availability is explicit. Stable elite precision without an independently observed behavior/regime change is not automatically HIGH from fixed cadence or strong results. This is contextual expectation, not a blanket title discount: genuine personal shifts remain eligible.

## Privacy and local validation

Public profile closure/ban `status` is never read for analysis, selection, gates, confidence or explanations. The allowlist keeps only neutral `joined` and `title` context after username validation. Regression tests compare complete analytical structures and embeds with status hidden/open/closed/fair-play-closed.

No case files, target lists, detailed reports or accusations are persisted to Git. Discord holds public reports; sensitive caches are bounded memory, cleared on restart. No target username is added to feature analytics. Persistent buttons survive restart; detailed runtime results expire and explain how to re-scan.

Use the ignored `validation_cases.local.json`, or files outside the checkout, with explicit `known_fair_play_closed` / `trusted_normal` labels. “Not banned” is not equivalent to trusted normal. Offline fixtures belong in ignored `fairplay_validation.local/` (one account JSON containing `profile` and `games`). Run:

```
python fairplay_validation.py validation_cases.local.json --offline-dir fairplay_validation.local
# Explicit serial network validation, no Discord:
python fairplay_validation.py validation_cases.local.json --live
```

Labels are compared only after the analytical priority is frozen; the analyzer receives only a username. Output is aggregate counts/recall/failures, never account names or reports. Failed scans are reported separately rather than fabricated LOW. No real labelled case set was supplied or used for threshold calibration in this change. Synthetic coverage does not establish empirical detection accuracy.

## Validation and local benchmark

`test_fairplay_v4.py` covers stable normal/elite histories, isolated excellent games, imperfect sustained clusters, intermittent/recurrent periods, title-neutral personal shifts, deep attenuation, missing clocks, 500-game isolation/bounded full scans, variant slots, chronology gaps, sample counters, tactical decisions, local-label separation and privacy. Existing Fair Play, status-independence, idle/routing, game review, economy, pets and minigame suites remain required.

`scripts/benchmark_fairplay.py` compares old/new search budgets on the same synthetic PGN with real Stockfish; it reports measured local timings and a four-position historical probe. It does not contact accounts or measure detection accuracy. `scripts/smoke_stockfish_reviews.py` exercises real two-pass screening plus the existing normal Game Review.
