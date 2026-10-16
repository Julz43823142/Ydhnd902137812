# Independent, near-continuous accuracy research (legal-first)

## What changed

Chess.com's User Agreement (updated 2026-03-25) restricts automated use of
its content to develop datasets or improve competing analysis products without
prior written authorization, and prohibits attempting to reverse-engineer
its algorithms. The independent bot does **not** use Chess.com PGNs, official
accuracy values, Brilliant labels, website scraping, or undocumented APIs.

The prior [nightly CAPS2 reference workflow](../.github/workflows/nightly_caps2_research.yml)
no longer has a schedule. Its only entry point is a manual dispatch that is
skipped unless the repository variable `CHESSCOM_RESEARCH_WRITTEN_AUTHORIZATION`
has been explicitly set to `true` **after obtaining appropriate written
authorization**. This is not a declaration that all usages are automatically
lawful; seek actual permission describing the intended use.

## Continuous independent work

[Independent Formula Research](../.github/workflows/independent_formula_research.yml)
is **scheduled every five minutes, 24 hours/day**, at minutes 02, 07, 12,
etc. UTC to reduce peak-time dispatch contention, with a concurrency group
that prevents simultaneous writes. A normal run may use up to 48 minutes
for engine research and has a 59-minute GitHub runner limit.

Updating the independent workflow on `main` also triggers one **immediate
bootstrap run**, using one original game, the fast Stockfish analysis profile
and up to seven minutes of engine work. This is a functional startup check,
not a substitute for the scheduler or a guarantee that enough real analysis
will finish. It does not collect any Chess.com data. New triggers are pending while an
earlier run is active, and obsolete pending triggers may be replaced.

**This is near-continuous best-effort scheduling, not a guaranteed 24/7
dedicated runner.** GitHub may delay/skip scheduled workflows, may suspend
inactive repos, and limits job runtimes. Each runner is ephemeral; setting up
a truly dedicated always-on self-hosted machine needs hardware/hosting,
configuration, ongoing maintenance and possibly payment. No such runner has
been purchased or provisioned by this change.

The worker `scripts/independent_formula_lab.py`:

1. Generates original, reproducible legal standard-chess move sequences locally
   (not downloaded parties or engine game databases).
2. Compares its own two Stockfish 19 evaluation profiles: depth-18 versus
   a fast 0.12-second-per-position profile.
3. Tries up to 2,000 candidate numeric scoring formulas per run to predict
   *the independent deep Stockfish estimate from the fast metrics*, using
   a deterministic game-level training/holdout split.
4. Persists original-game engine metrics and independent formula results on
   a separate public `sharkbot-independent-state` branch. Results can resume
   after interruption; no partial engine reviews are accepted.
5. Never modifies the Discord bot's accuracy/classification parameters,
   Fair Play, or user-facing review responses automatically.

The original generated games are a useful deterministic engineering test,
but **not a representative sample of human chess**. A match to Stockfish 19
is **not a match to Chess.com**, and the lab cannot conclude that it has
recovered CAPS2 or Chess.com's proprietary Brilliant classifier.

For local smoke tests:

    python -m unittest -v test_independent_formula_lab test_brilliant_calibration

To run independent experiments manually with Stockfish configured:

    python scripts/independent_formula_lab.py --state-dir ./local-independent-state \
      --max-minutes 10 --trials 250

The generated state should not be confused with Chess.com reference data.

## Brilliant move comparison

`scripts/calibrate_brilliant_moves.py` supports research comparing the
**exact ply numbers** marked Brilliant in a fully labeled game to the ply
numbers predicted by SharkBot's separate Brilliant rule. Counts alone are not
enough: two different Brilliant moves are a mismatch.

The Chess.com PubAPI does **not** contain move-by-move official Brilliant
labels. The script performs **no remote requests** and cannot obtain
authoritative Chess.com Brilliant labels by itself. Use only original
independent annotations or specific Chess.com-provided labels whose intended
use has been authorized in writing. A manually typed screenshot annotation
does not itself bypass applicable contractual restrictions.

Input is a local JSON file:

```json
{
  "schema_version": 1,
  "games": [
    {
      "id": "my-original-game-01",
      "pgn": "[Event \"Original Game\"]\n[White \"A\"]\n[Black \"B\"]\n[Result \"*\"]\n\n1. e4 e5 2. Nf3 Nc6 *",
      "reviewed": true,
      "label_source": "Original independent human analysis",
      "brilliant_plies": [3]
    }
  ]
}
```

Ply 1 is White's first move; ply 2 is Black's first; ply 3 is White's
second. An empty `brilliant_plies` means someone reviewed **every move**
and explicitly confirmed no Brilliant. Never treat missing labels as zeros.

The script rejects missing review attestations, duplicate game IDs, illegal
PGNs, invalid move indices and incomplete Stockfish reviews.

    python scripts/calibrate_brilliant_moves.py \
      --dataset ./authorized-local-brilliant-labels.json \
      --output ./brilliant-research-result.json

The report includes precision, recall, false positives, missed plies, exact
per-game move-set matches and separate White/Black counts. Candidate thresholds
are chosen using training games; independent holdout performance is reported
and **never automatically deployed to Discord**. With insufficient fully
annotated examples it reports `insufficient_labeled_games`, not fake accuracy.

## Chess.com permission

Request prior **written authorization** covering use of public PGNs,
official accuracy scores, per-move Brilliant references, automated collection,
dataset retention, calibration, and non-commercial Discord bot deployment.
Their [User Agreement](https://www.chess.com/legal/user-agreement) and
[PubAPI guidelines](https://support.chess.com/en/articles/9650547-what-is-the-pubapi-and-how-do-i-use-it)
can apply simultaneously; API availability is not blanket permission.
