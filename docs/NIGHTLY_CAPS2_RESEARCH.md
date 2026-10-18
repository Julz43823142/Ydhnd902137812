# Chess.com CAPS2 reference research (disabled pending permission)

The proposed opt-in successor is documented in
[ACCURACY_OPTIMIZER.md](ACCURACY_OPTIMIZER.md). It expands this lab to explicit
node/depth/time budgets, encrypted checkpoints and aggregate status. It remains
disabled unless separately authorized AND isolated capacity is confirmed.
The remainder of this page describes the historical two-profile nightly lab.

**Disabled as of 2026-10-16.** Automatic Chess.com reference harvesting has been suspended because written authorization is required for the intended calibration use. The workflow has **no recurring schedule** and can only run after explicit manual dispatch with repository variable `CHESSCOM_RESEARCH_WRITTEN_AUTHORIZATION=true` set following appropriate written permission. See [the independent Stockfish alternative](INDEPENDENT_FORMULA_RESEARCH.md).

## Previous schedule (INACTIVE)

The research workflow is `.github/workflows/nightly_caps2_research.yml`.

- **No automatic research is currently scheduled.** The former schedule was 00:07 to 08:07 Europe/Amsterdam; the notes here describe that historical design.
- Work is allowed only during **00:00-09:00 local time**. Every run checks the clock before provisioning and again before every move. Long-running analysis is interrupted at the cutoff; partial games are never treated as completed.
- Runs for at most 42 minutes of analysis per hourly execution, with runner timeout 58 minutes, leaving overhead for engine preparation and git state persistence. Scheduled starts are best-effort; GitHub can delay or omit runs. This is nine bounded hourly slots, **not** one guaranteed uninterrupted nine-hour process.
- A separate queue and branch are used. The research worker does not dispatch Fair Play workers, ordinary reviews, or change `main` scoring.
- The repo is currently **public** and runs standard GitHub-hosted Ubuntu runners. GitHub describes these minutes as free; storage, rate and runner limits still apply.

## First night, snapshots, and persistence

On the first active run, the script uses **only official Chess.com PubAPI** to collect 100 unique, completed, legal public Sharkmeister games, with both published official accuracies. This was previously achieved in a real GitHub API smoke run (100 valid from 325 checked).

**The user explicitly authorized these public chess games to be stored publicly.** Unlike the earlier collection-only tool's gitignored local output, this optional research automation commits its snapshot of public PGNs, engine metrics, and best candidate settings to the separate **public branch `caps2-research-state`**. Do not use this workflow with private sources or secrets.

The research state lives at `research/caps2/` on that branch:

- `dataset.json`: frozen, verified 100-game reference snapshot; fetched only when needed.
- `collection_report.json`: original official fetch counts.
- `metrics/depth18/*.json`: independent complete Stockfish 19 depth-18 evaluations.
- `metrics/time1/*.json`: independent complete Stockfish 19 time-limited evaluations (one second per searched position).
- `best_depth18.json`, `best_time1.json`: research-only candidate parameters and held-out errors.
- `latest_depth18.json`, `latest_time1.json`, `latest_report.json`: per-run coverage and errors.

Each run reloads the separate branch, analyzes only games not previously completed for the selected regime, then commits the new results back to that branch. If stopped mid-game, that game is retried on a later run. This persists across runners and nights without relying on GitHub cache eviction. The reference game identity plus exact PGN fingerprint bind each cache entry to its source.

The workflow uses existing pinned Stockfish 19 preparation and no untrusted downloaded model. If the public API is unavailable before the initial snapshot, the workflow records a collection failure rather than inventing accuracies. If the branch push fails, the GitHub job fails instead of claiming persisted work.

## Comparing formulas

The existing `scripts/calibrate_review_accuracy.py` provides the independent production-baseline candidate grid. The nightly lab also tests up to **2,000 additional bounded parameter combinations each run**. The only candidates are **mathematical settings**; no arbitrary generated Python code is executed. Each trial optimizes only training-set mean absolute error (MAE).

Games are deterministically assigned to a stable game-ID-based **75/25-like training/holdout split**, with a minimum of 20 fully analyzed games and at least five held-out games. Training score picks candidates; the held-out score is evaluated only after candidate selection. For each regime we report baseline MAE, candidate MAE, held-out improvement and proportion of held-out player accuracies within two percentage points.

A test score better on 100 reference games is **not proof of recovering Chess.com's undisclosed CAPS2 formula**. Neither Chess.com's exact engine settings nor classifier are assumed known. The time1 regime is an experiment, not a claim to mirror Chess.com.

Importantly, this process **cannot modify the Discord review formula or Fair Play**. To consider deployment later, we need independent new games and explicit review of the analysis results.

## How to inspect or run

- Scheduled workflow and runs: [Nightly accuracy research](../../actions/workflows/nightly_caps2_research.yml)
- Checkpoints: [Research state branch](../../tree/caps2-research-state/research/caps2) (appears after first successful active-night run).
- The workflow requires a manual dispatch **and** the written-authorization repository flag. Even with authorization, it skips engine computation outside the Dutch nightly window.

Offline tests:

    python -m unittest -v test_nightly_caps2_lab test_chesscom_reference_collector test_chesscom_reference_verify

The initial full-GitHub test run should be checked for a green 7-job standard CI suite. Real overnight engine and persistence can only be confirmed after an in-window execution completes, because no engine work is run outside the window.

## Operational limits and precautions

The Github Actions schedule may be delayed; it is not a precise wall-clock guarantee. A Stockfish depth-18 search can take longer than expected on a single ply, so the worker checks its time budget **between engine evaluations** and leaves a buffer before 09:00. A runaway engine could still require cancellation by the runner timeout; per-ply execution cannot be interrupted instantaneously.

If the research account later becomes private, revisit billing. Changes to the scoring code or Fair Play are deliberately out of scope. Stop the schedule by disabling `Nightly Chess.com Accuracy Research` in Actions or reverting the workflow on `main`.
