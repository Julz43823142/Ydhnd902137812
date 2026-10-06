# Fair Play Review v8

This is a moderator screening tool, not a cheating verdict or a calibrated probability. It performs no punishments, reports, wallet mutations or rewards. Missing evidence is never suspicious. False positives and missed cases remain possible; human review is mandatory.

## Architecture and runtime

- `fairplay_data.py`: validated usernames, fixed-host serial Chess.com PubAPI requests, PGN/clock extraction and eligibility.
- `fairplay_analysis.py`: one low-priority, one-thread Stockfish process; fixed-node searches, same-root evaluation loss, pipeline and bounded process-local cache.
- `fairplay_clusters.py`: strength-aware evidence, denominator-aware persistence, exact-control discovery and deep confirmation.
- `fairplay_calibration.py`: expectation shrinkage, Wilson denominator checks, leave-cluster-out deltas and representative baseline selection.
- `fairplay_positions.py`: opponent-matched context, adjacent same-budget opponent-error exposure, easy-conversion normalization and competitive-position metrics.
- `fairplay_history.py`: cheap extended-history metadata, bounded engine discovery probes and adaptive historical candidates/control games.
- `fairplay_baseline.py` / `fairplay_timing.py`: personal timing profiles, trivial-move delays, premoves, dispersion, complexity response and behavioral changes.
- `fairplay_scoring.py`: conservative priority gates, confidence and report diagnostics.
- `fairplay_results.py`: conservative bounded-score corroboration for same-control periods, with an underrating margin.
- `fairplay_ui.py`: existing Discord bot, isolated channel, persistent submission/report views, one movable one-hour idle panel, bounded queue/executor and one public progress card.
- `fairplay_validation.py`: optional local calibration CLI, never imported by the production bot.

Default `ReviewConfig`: `history_games=500`, `primary_engine_games=100`, `max_archives=36`, runtime deadline 1,800 seconds. The full fast pass on the literal latest 100 eligible **rated** games runs first, newest-by-end-time; casual or unknown-status games never fill slots; reporting restores chronological order. The remaining history receives metadata/clock profiles and four evenly spaced 2,000-node discovery probes per game. Probes never count as fully scanned or scoring-eligible games. Their stage has a soft 12% runtime allocation. Exact-control eight-game changes in probe quality or raw timing nominate historical periods, plus six surrounding control games on each side, with at most 40 extra full fast scans. Stable history selects no additional full historical scans. Engine-only changes may be missed by sparse probes; this is a bounded discovery strategy, not exhaustive historical engine coverage.

Fast searches use 24,000 nodes / MultiPV 3. Deep searches use 320,000 nodes / MultiPV up to 5, reserved for critical/ambiguous rankings; ordinary deep positions retain MultiPV 3. Up to ten deep games are selected deterministically: normally seven games spanning the strongest period and three same-control rated baseline controls at CPL quartiles. For critical-led periods, preserve endpoints and improve critical-opportunity coverage within those seven slots, selecting by opportunity count rather than success rate. If no suitable controls exist, the period can use all ten slots; fallback scans fill remaining capacity. The baseline is not the worst-quality games. Final discovery and confirmation use frozen equal-budget fast metrics. Games with fewer than eight meaningful decisions do not fill reporting/HIGH coverage, but fully scanned sparse/easy games remain in chronological periods with their actual losses and missed critical opportunities. Truly unscanned gaps cannot be bridged. Explicitly marked historical probes never enter full-game discovery or scoring. Clearly decisive fast positions beyond ±800 cp are not needlessly deep-searched. Fast positions beyond ±600 cp do not contribute engine evidence; positions between ±300 and ±600 cp have reduced evidential weight. CPL is bounded at 1,000; mate scores are normalized safely and decisive mate evaluations provide no engine-matching evidence. Near-best choices (within 15 cp), equivalent candidate counts, quiet top-1 agreement and fast/deep best-move stability are also retained. If the separate actual-move root search exceeds the nominal best score by >20 cp, that contradictory position is excluded from precision instead of recorded as a perfect zero-loss decision. The scaled evaluation-loss index `sigmoid(.00368208*best_cp)-sigmoid(.00368208*actual_cp)` is descriptive context only, never a fitted player outcome or cheating probability.

All heavy work remains in the existing dedicated executor, not Discord's asyncio loop. Threads=1, Hash=64 MB, deterministic nodes and per-position hash clearing; one scan at a time with duplicate-target reuse. Transport timeout is eight seconds per engine command. Deadline expiry preserves finished fast results and lowers coverage/confidence; it does not invent a completed deep pass. The public card distinguishes collection, full scans, scoring inclusion, deep reviews, exclusions and incomplete extended discovery. No new environment dependency beyond the existing working Stockfish 19/python-chess setup.

## Eligibility and clocks

Only explicitly `rated == true`, completed standard Rapid, Blitz or Bullet games, from the standard initial position, with a valid PGN, at least 24 plies and eight structural non-opening decisions qualify. Unrated games and missing/non-boolean rated flags have `unrated` / `rated_status_unknown` counters and never enter history, engine selection, baselines or scoring. Variants, correspondence, abandoned games, malformed PGNs, custom starts, short games, duplicates and unavailable archives have separate exclusion counters. Ineligible rows do not consume the eligible-game limit. Eligible games use completion-time ordering.

The first 20 plies contribute no engine-matching evidence. Only genuinely forced/trivial structural proxies are excluded: one legal move, near-forced check responses, obvious single recaptures, simple exchanges and sparse-board immediate mates. Difficult check responses and nontrivial recaptures remain analyzable. Proxies are imperfect and documented as such.

Clock reconstruction uses previous same-side clock + increment − new clock. Missing clocks break the chain; first moves and unknown controls do not produce invented times. Negative times, times ≥120 seconds, severe time trouble (≤10 seconds or 5% of base time) and unsupported clocks are excluded. Premoves ≤0.5 seconds are separate from genuine delayed trivial moves. Openings retain raw timing for personal comparison, never engine agreement. Rapid/Blitz/Bullet and exact base+increment remain separate; only rated games are admitted. Elo-expectation/rating-movement context uses only explicitly rated games; missing flags are never inferred as rated. Timing is retained on trivial and already-won/lost positions even when their engine-matching weight is zero. Bullet contributes only 35% signal weight. Missing clocks can coexist with HIGH engine confidence.

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

Let `ramp(x,a,b)=clamp((x-a)/(b-a),0,1)`. Rating bands (<1000, 1000–1399, 1400–1799, 1800–2199, ≥2200) have indices `b=0..4`. Unknown rating uses `b=0`. **The same strength expectation applies in fast and deep analysis.** The compatibility `personal` argument no longer removes rating adjustment; titles never multiply evidence or grant exemptions.

Absolute evidence uses expectation shrinkage before applying ramps:

```
posterior_rate = (observed_rate × count + expected_floor × prior_count)
               / (count + prior_count)
posterior_CPL = (observed_CPL × effective_decisions + 60 × 35)
              / (effective_decisions + 35)

engine = .65 × ramp(posterior_weighted_top1, .62+.025b, .96)
       + .15 × ramp(posterior_top3, .82+.015b, 1)
       + .20 × (1-ramp(posterior_CPL, 5, 60))
critical = .75 × ramp(posterior_critical_top1, .50+.035b, .96)
         + .25 × ramp(posterior_unique_hit_rate, .50+.035b, .96)
```

Agreement priors use 35 effective decisions; critical and unique rates each use their own denominators and eight prior opportunities. CPL is shrunk toward the conservative weak-performance expectation. No extra square-root multiplier or second full-sample penalty is applied. Paired deep effect retention uses raw effects with explicit coverage gates, at the actual rating; final primary evidence is the minimum of shrink-adjusted full-period evidence and paired deep strength.

Per-game persistence uses a one-sided Wilson lower bound with z=1.645. Effective weighted top-1 needs at least eight effective decisions and a lower bound ≥.60, or critical precision needs ≥5 opportunities and a lower bound ≥.60. Thus 2/2 critical hits display as 100% but do not establish per-game persistence. A separate pooled-replication route requires ≥10 games / 160 decisions / 30 critical opportunities, a pooled Wilson lower bound ≥.80 and critical hits replicated in ≥80% of games. It never labels the individual tiny-denominator games as strong; six games with 2/2 cannot use this route. These bounds are approximate denominator protection for correlated chess decisions, **not calibrated independent-trial significance**.

Discovery still searches 5/6/8/10/15-game windows and 45-minute-gap sessions. ≥6 games and two-thirds supported games establish discovery persistence. HIGH normally needs an ≥8-game period with ≥160 decisions. A six/seven-game period can qualify only with ≥300 decisions / 40 critical positions, both absolute families ≥.85 and ≥90% supported games. Ranked subsets do not establish persistence. Gaps in scanned/eligible coverage break windows. Overlapping windows never multiply evidence. Separate recurrence requires disjoint periods in the same control, ≥160 decisions / 30 critical opportunities in each and at least six lower-anomaly games between them; one continuous plateau does not qualify.

### Explicit personal comparison

For up to 64 strongest persistent discovery candidates, build a leave-cluster-out baseline using **all** fully scanned comparable rated games, not merely the lowest-quality games. Require ≥10 baseline games / 160 decisions. Unscanned historical probes remain discovery-only and do not become invented full-coverage engine evidence.

Report baseline/cluster weighted top-1, top-3, median/robust CPL, critical precision, mistake/blunder rates and timing profiles with deltas. An engine anomaly requires top-1 gain ≥15 percentage points and robust CPL improvement ≥20 cp / 2.5 robust spread units. Critical anomaly requires ≥30 opportunities on each side, gain ≥20 pp and the same CPL effect. Spread is `max(8,1.4826 × baseline MAD)`. These effect-size/dispersion gates reduce best-window selection bias; they are not a formal multiple-comparison correction. Small +4 pp best streaks do not qualify. Stronger +30 pp / 40 cp effects receive Very Strong context. Before/after baseline counts show whether a period is isolated.

Representative deep controls are picked at the baseline's 25th/50th/75th CPL quantiles, never by cherry-picking its weakest games. Confirm the same cluster subset versus at least three deep controls / 60 decisions; the baseline delta must remain established in the full fast baseline, paired fast controls and deep comparison. For deep controls, compare the cluster Wilson lower bound against the control Wilson upper bound: require ≥15 pp engine-rate separation, or ≥20 pp critical separation with ≥6 control / 20 cluster critical opportunities. This accommodates short representative controls without treating tiny raw percentages as certain. Core deep stability alone does not establish personal anomaly. Stable strong-play checks work for untitled players too: ≥20 same-control games with median top-1 ≥.65, 10th–90th percentile top-1 spread <.08 and CPL spread <15 are descriptive stable history. Genuine independent behavior changes are not suppressed by this check.

### Independent support and gates

- Timing: player-specific timing change, repeated cadence, trivial/ordinary/critical delay overlap or repeated within-game clock transitions. Same-period exact-control overlap is mandatory. Bullet weight remains .35.
- Engine-derived Performance Shift: retained for discovery, persistence, onset and UI, **never independent support**. CPL/top-1 changes and win/loss engine-quality contrasts cannot corroborate the same engine evidence a second time.
- Rated result/Elo overperformance: weak secondary support (up to .65); account age (up to .10) and rating gain (up to .35) remain descriptive and cannot unlock HIGH alone. Separate recurrence supplies replication support under the coverage rules above.

The diagnostic weights .30/.30/.15/.15/.10 remain descriptive only. Gate rules:

- **INSUFFICIENT DATA:** fewer than 10 usable rated games or 80 meaningful decisions. Ten short games still receive a priority with LOW confidence.
- **HIGH:** primary engine ≥.65 or critical ≥.65 with ≥20 opportunities; qualifying persistent period; core deep confirmation; independent timing/result ≥.50 or separate recurrence; confidence not LOW. Normally ≥20 scoring games. A leave-cluster-out personal anomaly must also survive deep comparison. An exceptional absolute path remains for no-comparable-baseline cases or separate recurrence: both primary families ≥.85, ≥10 period games / 300 decisions / 40 critical opportunities and timing ≥.75 or recurrence.
- **10–19-game exception:** additionally the qualifying period covers ≥80% of the scoring sample (at least eight games), both primary families ≥.85, ≥300 period decisions / 40 critical opportunities and independent support ≥.75. Ten ordinary good games do not qualify; ten extreme, deeply confirmed games with strong timing can.
- **VERY HIGH:** all HIGH gates, max(engine,critical) ≥.88 and min ≥.65, independent timing/result ≥.75, ≥30 scoring games / 500 decisions / 60 critical opportunities, ≥10 period games / seven deeply reviewed period games, HIGH confidence, no partial scan and a deeply confirmed personal anomaly or separate recurrence. No small-sample VERY HIGH.
- **MODERATE:** primary ≥.50 with independent support ≥.35, or both primary families ≥.65, without higher gates. If no persistent period qualifies, a single exact-control aggregate can support this descriptive priority, but never HIGH; absence of persistence must not erase otherwise useful aggregate observations. Pure critical precision is insufficient.
- **LOW:** no supported combination; never proof of fair play. Stable precision without behavioral discrepancy is descriptive.

Core deep confirmation still needs ≥5 period games / 60 decisions, strength-aware core effect ≥.65, ≥20 deep critical opportunities for the critical route and ≥80% paired fast→deep effect retention. Confidence stays separate: HIGH requires ≥30 scoring games / 500 decisions / 60 deep decisions and complete core coverage; MEDIUM requires ≥10 / 150; otherwise LOW. Missing clocks have their own insufficient-data family label.

Discord `Clusters & History` shows same-control baseline, cluster, deltas, opponent ratings/expected results, competitive versus easy decisions and deep baseline confirmation. `Engine Analysis` and history details explain absolute scores, independent timing/results, recurrence, qualifying coverage and concrete HIGH-block reasons. No cheating probability is shown.

## Opposition and position ease

Opponent Elo never directly reduces engine evidence. Leave-period-out controls preferentially match the exact time control, player rating within 200 Elo, opponent rating within 250 Elo and player-minus-opponent gap within 200 Elo of the cluster medians. If cluster ratings are known, mismatched or missing-rating controls do not silently fill missing coverage. A sparse matched baseline stays insufficient. These configurable tolerances are heuristic matching rules, not a fitted population model. Deep controls use the same matching rules.

Expected and actual score are shown separately. Existing Elo-expected excess-score statistics continue to distinguish expected wins against weak opponents from surprising results against stronger opposition. Results remain supporting evidence only.

Position ease uses the existing engine evaluations, not the opponent rating. Across adjacent subject decisions, subtract the earlier actual-move evaluation from the current pre-move evaluation in the same player POV. Require equal node budgets, adjacent plies, consistent search scores and non-mate evaluations. Missing or incomparable evaluations remain unknown. A swing of at least 150 cp is an approximate opponent-error exposure; short searches can still be noisy. This measures exposure, not a validated classification of the opponent's move. It adds no Stockfish searches.

Obvious material-gaining captures after such an error or in an already winning position (at least +350 cp), with clear candidate separation, receive zero engine-matching evidence. Other straightforward winning captures receive 0.15 of their original weight and do not count as critical/unique opportunities. Large candidate gaps alone never mark quiet difficult moves as automatic, and a whole game is never discarded simply because the opposition is weak. Existing opening/forced exclusions remain in force; their clocks remain available to timing analysis. Context annotation is idempotent and recomputed after deep analysis.

Newly recognized obvious material gains are also categorized as trivial for personal timing profiles, trivial-delay comparisons and cross-category cadence. They remain zero engine evidence; their valid clocks, including near-instant moves, are retained. A slow capture never becomes proof of assistance.

Competitive metrics cover useful decisions within ±200 cp, without an immediately measured large opponent error. Reports retain competitive decisions, top-1, median CPL and critical precision; already-winning fraction, easy-conversion count, opponent-error exposure, concrete material exposure and equal-to-winning transitions are also recorded in memory. These are explanatory breakdowns of the same engine family, not new independent corroboration for HIGH.

`test_fairplay_positions.py` exercises opposition matching, sparse/missing controls, expected wins versus upsets, same-budget POV/error reconstruction, missing/mate/gap safeguards, idempotence, mixed-game preservation, easy-conversion false positives and a genuinely difficult cluster that can still reach HIGH with independent evidence.

## Timing features and explicit coverage

- Cadence uses a data-derived modal center, with half-width `min(1s, max(.1s, .20 × center))`. A non-instant center must be ≥1 second. Median/MAD replace outlier-sensitive CV in the flag; raw CV remains visible. A per-game flag needs ≥15 clocks, ≥80% band coverage and robust CV `1.4826 × MAD / median < .30`. Premoves remain in the denominator.
- Cross-game recurrence permits shorter games (≥8 clocks each), but requires ≥5 games and ≥80 clocks with ≥70% per-game band coverage at a common delay. A small recurrent period is not pooled away by a variable background.
- Trivial/ordinary/critical cadence pools an already recurrent group, requiring category coverage in every counted game. It no longer requires six recaptures plus four critical clocks separately in every individual game.
- Cross-category delayed timing also tests a broader pattern with occasional pauses: ≥6 games, ≥12 trivial / 48 ordinary / 12 critical clocks, each category present in ≥max(3, ceil(games/3)) games; medians ≥1 second; ≤10% near-instant moves; median ratio ≤1.5; MAD/median ≤.45 in every category; all pairwise histogram overlaps ≥.65. It contributes .55, or .75 with ≥12 games. Full control groups and bounded 6/12/20-game chronological windows are described separately. This is a heuristic screen, not a multiple-testing-corrected significance claim.
- Personal comparisons require ≥6 games on each side and ≥15 valid clocks per game. Engine quality comparisons use robust trimmed CPL rather than the often-zero median CPL. Baseline groups remain engine-defined, not timing-defined; genuine premove proportions and full-distribution medians are retained.
- The Timing card shows valid clocks, excluded estimates, engine-linked clocks, recurring periods and trivial/critical medians/overlap. “Insufficient clock data” is separate from “Not elevated.” The Engine card explains the priority gates. Data confidence describes coverage, not certainty about behavior.

All timing features have innocent alternatives: input habits, accessibility aids, lag, clock quantization, deliberate pace and legitimate improvement. Timing alone cannot produce HIGH or VERY HIGH. Neither a ban label nor a desired outcome determines thresholds for a named player.

## Research basis and limits

Public references inspected for this revision:

- [Regan, Intrinsic Ratings Compendium](https://cse.buffalo.edu/~regan/papers/pdf/Reg12IPRs.pdf): decision-level evidence, MultiPV alternatives, position-dependent evaluation loss, calibration and uncertainty. This implementation does **not** claim to reproduce Regan's fitted IPR model.
- [Lichess Accuracy](https://lichess.org/page/accuracy): raw accuracy/centipawn loss depends on position context; a high accuracy value alone does not establish misconduct. Its documented sigmoid motivates the descriptive scaled-loss index, not a cheating likelihood.
- [Lichess Irwin](https://github.com/clarkerubber/irwin) and [Kaladin](https://github.com/lichess-org/kaladin): move/game sequences and multi-dimensional timing/quality information, trained on substantial datasets. Their trained models are not copied or imported here. Private platform signals such as focus events are unavailable through the Chess.com PubAPI and are not invented or collected.
- [Chess.com Fair Play overview](https://support.chess.com/en/articles/8568369-what-do-i-need-to-know-about-fair-play-on-chess-com): statistical assessment and human review; several platform detection methods are confidential.

Large best–second gaps are a reproducible opportunity proxy, **not a validated measure of human difficulty**. Reviewing many overlapping windows can find apparently impressive runs by chance; no p-value or cheating probability is displayed. SharkBot remains a transparent prioritization tool with false positives and missed cases, not a demonstrated replacement for platform fair-play teams.

## Privacy and local validation

Public profile closure/ban `status` is never read for analysis, selection, gates, confidence or explanations. The allowlist keeps only neutral `joined` and `title` context after username validation. Regression tests compare complete analytical structures and embeds with status hidden/open/closed/fair-play-closed.

No case files, target lists, detailed reports or accusations are persisted to Git. Discord holds public reports; sensitive caches are bounded memory, cleared on restart. No target username is added to feature analytics. Persistent buttons survive restart; detailed runtime results expire and explain how to re-scan.

Use the ignored `validation_cases.local.json`, or files outside the checkout, with explicit `known_fair_play_closed` / `trusted_normal` labels. “Not banned” is not equivalent to trusted normal. Offline fixtures belong in ignored `fairplay_validation.local/` (one account JSON containing `profile` and `games`). Run:

```
python fairplay_validation.py validation_cases.local.json --offline-dir fairplay_validation.local
# Explicit serial network validation, no Discord:
python fairplay_validation.py validation_cases.local.json --live
```

Labels are compared only after the analytical priority is frozen; the analyzer receives only a username. Output is aggregate counts/recall/failures, never account names or reports. Failed scans are reported separately rather than fabricated LOW. Public gameplay from a small owner-supplied convenience sample was inspected privately to diagnose regressions, together with strong-play controls. Its labels never enter the analyzer, and its identities, PGNs and reports are not committed. This is exploratory validation, not a held-out population benchmark or a calibrated false-positive rate. A larger independently labelled held-out dataset is needed before estimating sensitivity/specificity; synthetic coverage does not establish empirical detection accuracy.

## Validation and local benchmark

`test_fairplay_v6.py` covers rated-only history/selection, uncertainty, stable untitled/elite controls, hot streaks, genuine changes, correlated engine-only changes, leave-cluster-out exclusion, representative deep controls, deep baseline collapse, ten-game exceptions and Discord gate explanations. Existing v5 tests were updated for explicitly replaced eligibility/persistence/support semantics. `test_fairplay_v5.py` retains correct weighted denominators, post-filter chronology gaps, same-period support, stable strong-play controls, delayed-clock recurrence, premove preservation, root-search uncertainty and Discord coverage/gate explanations. `test_fairplay_v4.py` covers stable normal/elite histories, isolated excellent games, imperfect sustained clusters, intermittent/recurrent periods, title-neutral personal shifts, deep attenuation, missing clocks, 500-game isolation/bounded full scans, variant slots, chronology gaps, sample counters, tactical decisions, local-label separation and privacy. Existing Fair Play, status-independence, idle/routing, game review, economy, pets and minigame suites remain required.

`scripts/benchmark_fairplay.py` compares old/new search budgets on the same synthetic PGN with real Stockfish; it reports measured local timings and a four-position historical probe. It does not contact accounts or measure detection accuracy. `scripts/smoke_stockfish_reviews.py` exercises real two-pass screening plus the existing normal Game Review.


## v10 long-period discovery

Chronological discovery now also searches 20-, 30- and 50-game exact-control
windows, alongside the existing 5/6/8/10/15-game windows and session groups.
This improves opportunity coverage for sustained periods with only a few
critical decisions in each game. Unknown/unscanned gaps still break periods;
overlapping windows still count as one period, not independent corroboration.
The primary 100-game scan, historical targeting limits, ten-game deep budget,
Stockfish node budgets, persistence and priority gates are unchanged. Longer
windows allocate the existing deep budget; they never convert a missing signal
into evidence. Runtime caches are versioned for the changed search allocation.

An exploratory private replay at unchanged search budgets improved deep core
confirmation in one of two owner-labelled positive cases, while the requested
HIGH-or-higher outcome was still not established. This is a discovery improvement,
not empirical proof of detection sensitivity. Identifying details stay outside
the checkout and desired outcomes do not enter scoring.

## v9 independent clock coverage

Clock comparisons now retain all fully scanned rated games, including games
below the per-game engine opportunity minimum. Forced/easy decisions can supply
clock observations without supplying engine agreement evidence. Historical
probes and unscanned games remain excluded from these engine-linked comparisons.
The Timing detail separately reports games retained for clocks below the engine
minimum. Engine sample, persistence, deep confirmation and HIGH gates are unchanged;
additional clocks never manufacture primary evidence. Local account checks are
validation only: desired labels, identities and closure status do not enter scoring.

## v8 period discovery and results

When no persistent period yet qualifies, deep discovery can still investigate the strongest chronological/session candidate with at least eight adequately covered games, 160 meaningful decisions and evidence strength at least 0.50. It is selected from all searched windows, not only the truncated twelve detail rows. It receives the same representative-control allocation; ranked nonchronological subsets cannot supply this fallback. Its deep results are shown separately and cannot bypass persistence, baseline or independent-support requirements for HIGH. Discovery must not require its own conclusion before investing in confirmation.

The previous result corroboration required twenty rated games, although ordinary searched periods contain eight to fifteen. The new supporting feature works from eight rated games with valid ratings/scores. Expected scores allow the player 150 Elo of underrating. For actual score A, conservative expected score E and n games, use the bounded-score tail bound exp(-2 × max(0,A-E)²/n). Support is zero when the bound is at least 0.005, grows linearly in negative-log-bound to 0.00001, and is capped at 0.65. Draws remain half-points rather than invented wins. This is a heuristic supporting feature under independent-game/reliable-rating assumptions, not an account cheating probability or a calibrated multiple-window significance test. Underrating, correlated sessions, legitimate improvement and smurfing can invalidate its assumptions. It never establishes HIGH alone. Existing Elo descriptive trajectories remain visible; HIGH still uses one same-control period and existing engine/deep/persistence/baseline gates.

`test_fairplay_periods.py` covers known sparse-game continuity, missing engine gaps, retained weak decisions, qualified coverage, discovery outside truncated detail rows, probe exclusion, representative deep controls and conservative short-period results. `ReviewResult.timeline` retains full scanned games only in bounded runtime memory, without adding case persistence.
# Repeatable evidence review (v11)

Cold and warm scans now discover periods from the same fixed-budget fast
measurements and use the same bounded deep-review plan. Cached deep results
are reused only for games in that plan; repeatedly scanning an unchanged sample
does not progressively add ten more deeply reviewed games.

Critical-opportunity persistence retains all observed hits and misses. Games
without a critical opportunity are not counted as missed critical decisions.
The pool still needs at least 30 opportunities, the configured Wilson lower
bound, the existing minimum contributing-game coverage, and opportunities in
at least one third of the complete period. Complete continuously scanned runs
are considered alongside short windows. Unknown intervening games still break
the run, and overlapping windows never manufacture independent recurrence.

Personal timing keeps the ranked-group state separate from any stronger
chronological comparison. Only the state belonging to the overlapping game
IDs can corroborate an engine period. Rolling performance uses the same
canonical time-control key as its source groups. Report details distinguish an
exploratory deep-confirmed candidate from a qualifying persistent period.

The HIGH/VERY HIGH gates, engine node budgets, runtime limit, channel isolation,
and ban-status exclusion are unchanged. These are measurement/reproducibility
corrections, not evidence of calibrated detection accuracy. No outcome for a
particular account is guaranteed, and a LOW result does not establish fair play.
