# Reference Accuracy Optimizer — prepared, NOT activated

This extends the existing `nightly_caps2_lab.py` and mathematical fitting
functions. Production `chess_play.py`, Fair Play, Discord and the separate
independent Stockfish lab are unchanged. The independent lab still cannot
measure agreement with Chess.com.

**Do not merge, dispatch, enable or deploy during the current Fair Play
review.** No runtime settings, secrets, production processes or running
Actions are changed by this PR.

## Measurements and isolation

The fixed panel is a verified local dataset with actual official white AND
black accuracies. The existing independent verifier checks legal PGNs,
identities, results and provenance. This worker never fetches references,
scrapes, invents labels or uses targets as engine inputs. Existing written
reference authorization protection remains mandatory.

Budgets: depth 18 (baseline), time 1s, time 0.5s, nodes 24k/50k/100k/250k/500k,
depth 12/15/20, time 2s/5s. These are hypotheses, not confirmed Chess.com
settings. One second is per UCI search; paired searches mean a move can
take longer. Timed searches depend on hardware/load, not bitwise repeatability.

A disposable child uses ordinary Game Review with a private UCI-budget
adapter, its own Stockfish process and process group, one thread, 64 MiB
hash, a 2 GiB address-space ceiling, reduced priority and hard deadlines.
It never contacts a production engine. Official target scores are not
passed to the child. Only complete games are cached; fixed-depth results
must actually reach that depth. Exact node/time exhaustion is not claimed
for solved mate/terminal positions. Binary SHA, source SHA, dataset SHA,
budgets and formula versions bind cached results.

One completed experiment is one engine budget/formula-search round on the
ENTIRE frozen panel. Partial panels do not count. A stable physical-game-ID
split keeps both players together: at least 15 training and 5 validation
games. Only training MAE selects formulas AND profiles. Every candidate's
settings/training error is retained; selected validation results are reported.
Repeated reports are not a fresh final holdout: new independent games are
required before deploying any formula.

Current production error means actual production scoring on depth-18
metrics, not fast-to-deep internal agreement. Report MAE, signed bias,
median error and within-1/2/5 fractions overall and by time class.
`player_scores` is the score-fraction denominator; a separate fraction
reports games where BOTH players meet each tolerance. The training-selected
best can have worse validation error; negative improvement is shown.

## Local use, only on isolated compute after the ongoing review

```sh
python scripts/accuracy_optimizer.py init \
  --state-dir data/accuracy_optimizer.local --dataset /path/to/verified/dataset.json

CHESSCOM_RESEARCH_WRITTEN_AUTHORIZATION=true \
python scripts/accuracy_optimizer.py run \
  --state-dir data/accuracy_optimizer.local --isolated --minutes 20 --trials 2000

python scripts/accuracy_optimizer.py status --state-dir data/accuracy_optimizer.local
```

`status.json` and `runs.json` are aggregate-only and readable during work.
`total_completed_experiments` counts historical completed results;
`completed_experiments` and the baseline/best comparison refer only to the
current identical dataset/source/engine contract. The numbered run manifest
retains prior contracts without pooling their scores.
Missing measurements are null, never fabricated zeros. Check the UTC update
time and workflow heartbeat: a running snapshot can be stale after a crash.
One worker holds an exclusive lock. Whole-game checkpoints survive restarts;
immutable experiment IDs avoid duplicate completion. Failures retry at most
three times per game/profile, then require investigation/new corrected
contract. Different datasets require separate states, never pooled scores.

## Optional scheduling and durable state

The existing `nightly_caps2_research.yml` is upgraded, not a second competing
reference workflow. It has 30-minute triggers but is disabled by default,
requiring ALL flags: `ACCURACY_OPTIMIZER_ENABLED=true`,
`ACCURACY_OPTIMIZER_COMPUTE_ISOLATED=true`, and existing
`CHESSCOM_RESEARCH_WRITTEN_AUTHORIZATION=true`.

It also needs secret `ACCURACY_OPTIMIZER_STATE_KEY` (a securely generated
Fernet key), plus an initialized encrypted reference snapshot. Never paste
the key in chat/logs. `seal`/`unseal` take `--state-dir` and `--bundle` and
read the key only from that environment variable. Seed `state.enc` under
`research/caps2/optimizer/` on the existing `caps2-research-state` branch
after authorization and review completion. No plaintext PGNs are published.
Aggregate `status.json` and `runs.json` there let an AI inspect results
without the dataset/key. These are snapshots, not guaranteed live status.

`ACCURACY_OPTIMIZER_RUNNER` can designate dedicated capacity. An available
hosted slot alone does not prove isolation: concurrency/billing may compete
with production. This PR provisions no server or subscription. One job,
one engine, 25 minutes computation and 35 minutes runner time are permitted
per invocation, at most 48 scheduled triggers/day; manual runs add to that.
Actual billing is unknown, not claimed free. Engine wall/search timings and
explicit cache reuse accounting are recorded. GitHub is best effort, not
guaranteed 24/7 hosting. A separately provisioned machine can supervise the
same bounded resumable CLI; no daemon is activated here.

Persistence authenticates/encrypts private state. Wrong keys fail closed.
Pushes are fast-forward only, never overwrite conflicts. The expanded-state
limit is 64 MiB; archive old rounds before reaching it. A forced runner kill
before persistence can lose that invocation's work but cannot certify a
partial experiment. Earlier pushed snapshots remain intact.

## Delivery

Run new optimizer and existing reference/independent tests, root/minigame
suites, compile and JSON/YAML/whitespace checks. Synthetic mocked engines
test behavior without a heavy production benchmark. Deliver a draft PR;
defer remote engine-heavy CI until it cannot compete with the active review.
