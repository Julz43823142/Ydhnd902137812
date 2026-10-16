# Public Chess.com accuracy references (research-only)

This tool collects only official Chess.com Published Data API games with BOTH published Accuracy scores. It never computes missing accuracy, scrapes HTML, changes the live review formula, runs Stockfish/Fair Play, or schedules work.

## Collect up to 100 games

Install dependencies:

    python -m pip install -r requirements.txt

Run from the repository root:

    python scripts/collect_chesscom_accuracy.py --player Sharkmeister --limit 100 --max-archives 24 --max-requests 70 --output-dir data/chesscom_reference.local

Archives are processed newest month first, with games sorted by end_time descending within each month. Only completed standard chess games with legal PGN, matching names/results, documented game identity, and both official Accuracy values between 0 and 100 inclusive qualify. Zero is valid. Invalid games are counted by exclusion reason.

The collector uses serial HTTP requests, no redirects, (5 sec connect / 20 sec read) timeouts, up to 3 attempts per URL, bounded backoff and Retry-After handling. Defaults allow 24 monthly archives and 70 HTTP attempts in total, including retries. Unvalidated archive URLs are never fetched.

Each invocation writes to a unique new directory under the gitignored local output path. Files are staged, then the entire directory is published atomically; a failed write never replaces an earlier good dataset.

- dataset.json: PGNs and both official accuracies. Compatible with the existing calibrate_review_accuracy.py --dataset input schema.
- overview.csv: PGN-free table.
- report.json: checked and stored counts, exclusions, missing optional metadata, visited archives, actual HTTP requests, stop reason and API errors.
- report.md: human-readable results and known limitations.

The command prints the exact run directory. Independently verify using the actual printed path:

    python scripts/verify_chesscom_accuracy_dataset.py data/chesscom_reference.local/run-EXAMPLE/dataset.json --report data/chesscom_reference.local/run-EXAMPLE/report.json

## Calibration format

The existing research-only calibration script accepts a JSON list of records with pgn and chesscom containing white and black scores. It will perform Stockfish evaluations only when separately invoked later, and only if a configured engine is available. The collector does not start calibration. Do not invoke it until authorized.

## Coverage and privacy

Not every public Chess.com game has an official published Accuracy. Fewer than 100 valid games is a permitted partial result, and the report must explain the reason. Missing Elo ratings and optional metadata are preserved as null and counted. The collector never estimates accuracy.

Brilliancies, move classifications, and Game Ratings are NOT exposed as standard fields in the published game archive. They are not present in this dataset and are never invented.

Game PGNs and user-specific reports remain in the gitignored output directory. Do not force-add these to this public repository or upload them as public GitHub Actions artifacts. The optional live workflow collects inside its ephemeral runner, logs aggregate counts only and does not upload PGNs. Run the same command locally when you need the actual dataset on disk.

## Test and one-time live check

Offline mocked tests:

    python -m unittest -v test_chesscom_reference_collector test_chesscom_reference_verify

GitHub Actions smoke workflow: .github/workflows/chesscom-accuracy-reference-smoke.yml. It is triggered on collector PRs and manually; its live collection step is non-blocking so API access failures are visible in the logs without being misrepresented as successful API retrieval. Look at the live step output and report to see the ACTUAL number obtained.

## Usage

The collector makes no more than --max-requests HTTP requests on a single invocation. GitHub Actions runner minutes depend on workflow runtime and the applicable billing settings. Actual remaining account usage cannot be deduced from these commands.
