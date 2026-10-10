# SharkBot Fair Play v20 — research, speed and failure diagnostics

**Status:** proposed, research-only diagnostics; this document does not authorize a merge.
Existing LOW / MODERATE / HIGH / VERY HIGH decision rules remain unchanged,
except the operator-requested deep candidate count, MultiPV 5 → 3.
Human review is mandatory. This is not Chess.com's proprietary detector.

## Research-grounded modules

| Research idea | Production implementation | Scope / caveat |
| --- | --- | --- |
| Evaluate candidate quality, not mere top-1 agreement (Regan and Haworth) | `fairplay_analysis.engine_metrics`, `fairplay_difficulty`, `fairplay_evidence_audit` | Three exact deep principal variations; played-root search remains independent. |
| Rating-appropriate human moves (Maia-3, Chessformer) | `fairplay_maia`, `fairplay_policy`, `fairplay_human` | Played move surprise is NOT cheating probability. |
| Clock-pattern anomalies (Lichess/ALLIE/ChessMimic) | `fairplay_timing`, `fairplay_local_timing`, `fairplay_baseline` | Missing/unsupported clock data is unknown; no clock data is invented. |
| Same-position engine, human and clock corroboration | **NEW** `fairplay_research.summarize_research` | Descriptive, per-game joint observation; correlated evidence, never extra independent score. |
| Strong moves in genuinely difficult positions | `fairplay_difficulty`, `fairplay_positions`, `fairplay_scoring` | Forced/book/trivial and equivalent-move decisions deweighted or excluded. |
| Game-level uncertainty and correlated decisions | **NEW** 160 deterministic whole-game bootstrap resamples | Exploratory intervals, not certified confidence intervals. |
| Many overlapping candidate periods / look-elsewhere effect | **NEW** 160 shuffled-order MAX-window reference diagnostics | This fraction is NOT a calibrated p-value. Independent control-group calibration still needed. |
| Personal historical baseline and change points | `fairplay_calibration`, `fairplay_clusters`, `fairplay_sequence` | Existing guarded comparisons; do not mix incompatible time controls. |
| Repeated evidence and counterevidence | `fairplay_convergence`, `fairplay_evidence_audit` | HIGH still needs multiple independently checked evidence conditions. |
| Independently labelled research examples (ChessFraud) | `scripts/benchmark_chessfraud.py`, `fairplay_validation.py` | The published dataset is **not** downloaded during a live review, and its partial samples must not train/define enforcement labels without independent holdout validation. |
| Cross-engine consistency / Lc0 | `fairplay_multimodel`, `fairplay_private_extension` | Optional and descriptive, never an extra conviction vote. |
| Detect false positives and near misses | `fairplay_validation`, existing synthetic legacy gates | Actual false-positive rates are *unknown* until independent representative holdout tests. |
| Annotated forensic evidence and provenance | `fairplay_evidence_payload`, `fairplay_export_persistence` | Owner-only, encrypted. |

Research context:
- Chess.com Fair Play: https://www.chess.com/cheating
- Lichess source, Irwin and Kaladin: https://lichess.org/source
- Regan–Haworth *Intrinsic Chess Ratings*, AAAI: https://cdn.aaai.org/ojs/7951/7951-13-11479-1-2-20201228.pdf
- Barnes–Hernández-Castro, *Computers & Security*: https://kar.kent.ac.uk/44719/
- ChessFraud KDD benchmark: https://github.com/artem-lepin-ml/chess-fraud
- Maia-3 / Chessformer ICLR: https://proceedings.iclr.cc/paper_files/paper/2026/hash/3d167db04a90885ad5208fe8b273668b-Abstract-Conference.html
- ALLIE ICLR: https://proceedings.iclr.cc/paper_files/paper/2025/hash/0ef1afa0daa888d695dcd5e9513bafa3-Abstract-Conference.html

## Execution contract

1. 500 selected rated games still require complete fast and full-depth position analysis.
2. Fast Stockfish: 24k nodes, MultiPV **1**. Deep: depth **18** for rapid/blitz,
   depth **12** for bullet, MultiPV **3**.
3. MultiPV 3 preserves the existing HIGH gate minimum of three completed candidate
   lines. Lower-resolution MultiPV 1 cannot be mistaken for comparable evidence.
4. A missing or timed-out exact search does not become evidence; checkpointed
   complete positions can be restored using the encrypted contract.
5. `fairplay_maia.carry_policy_after_deep` now indexes source positions once per
   game rather than quadratic scanning.
6. `scripts/benchmark_fairplay_depth18.py` times MultiPV 1 fast,
   MultiPV 3 deep and MultiPV 5 reference on the **same synthetic positions**.
   A single runner benchmark is not a 500-game runtime guarantee.
7. Optional Lc0 build-cache fallback uses pinned source and a clean
   `-Dnative_arch=false` build to avoid SIGILL on different GitHub CPUs.

## Failure forensic flow (Sharkmeister only)

When a review terminates with a caught exception:
1. Public Discord progress card shows a safe, short failure reason.
2. The bot saves a sanitized JSON record, with exact Git SHA,
   workflow/run URL, elapsed time, phase, engine budget, MultiPV, worker retry
   and encrypted checkpoint status. Never include raw exception text, FENs,
   usernames, PGNs, Chess.com API bodies, or Discord secrets.
3. The record is encrypted with the existing checkpoint secret and retained
   for at most seven days in the private checkpoint state on the isolated
   `fairplay-checkpoints` git ref.
4. Only the Sharkmeister Discord account (matched to `shark_admin.ADMIN_ID`)
   receives a DM containing the **View failure insights** button.
   The button performs a second server-side identity check and serves the
   JSON through an ephemeral interaction.
5. If DMs are disabled, Sharkmeister may run `/fairplaydiagnostic` for recent
   IDs or `/fairplaydiagnostic review_id:<12-hex-ID>` for the same JSON.

The exact **GITHUB_SHA** identifies the build. The source PR number can be
unknown on a main-branch runner and is deliberately not invented.

**Limitation:** a hard runner kill or GitHub cancellation cannot always send
a failure DM, because the process is already gone. Recovery still depends on
checkpoints and normal GitHub Actions logs. A 500-game real-world acceptance
scan must be observed before claiming that all long-running timeouts are fixed.

## Acceptance criteria before changing scoring

- Tests for normal and known-assisted gameplay on independent, labelled holdouts.
- False-positive rates reported by time class, rating bucket and sample size.
- True-positive/false-positive trade-offs validated outside training data.
- Absent or incomparable observations remain **unknown**, not negative evidence.
- No public accusation, ban or punitive action from automated SharkBot output.
