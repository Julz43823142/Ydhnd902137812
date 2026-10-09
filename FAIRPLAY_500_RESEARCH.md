# Fair Play 500 / Depth 18: research and deployment contract

This PR is a **draft** and must not be merged without explicit approval.

## Implemented, not merely listed

- Up to **500 latest eligible rated standard live Chess.com games**, across up to 240 archive months.
- Fixed **Stockfish depth 18 on every collected primary game** when `FAIRPLAY_FULL_DEPTH18=1`; existing equal-budget node screening runs first. An incomplete deep pass MUST NOT produce a priority.
- Existing local **Maia-3 5M** human-move model, sampled (up to 800 positions), keeps paired counterfactual confirmation. This is *not* Maia analysis of every position.
- Complete position-by-position machine-readable evidence, compressed JSON chunks and SHA-256 manifest, generated before decision trees are cleared.
- **Owner Evidence** button restricted by exact Discord user ID, with ephemeral attachments. Other users can see the public button but cannot use it.
- Optional `scripts/benchmark_chessfraud.py --allow-download` runs published **ChessFraud** labeled move-level baselines offline, using pinned dataset revision. The labels and its baseline agreement rates do not change live priorities.

## Research leads, not claimed production detectors

| Source | Actual integration status | Limitation |
|---|---|---|
| Stockfish 19 | Active | Depth search can be much slower than 24k nodes |
| Maia-3 5M | Active | Human move likelihood is not a misconduct probability |
| Maia-3 23M / 79M | Not installed | Must benchmark extra CPU/RAM before use |
| Maia4All | Research only | Public release still in progress |
| Allie / Allie v2 | Research only | GPU / large checkpoints; not turnkey on Actions |
| ChessMimic | Research only | Multi-GB artifacts; PolyForm noncommercial license |
| ChessFraud KDD 2026 | Offline benchmark added | 505 controlled games, not representative of live Chess.com accounts |
| Lichess Kaladin / Irwin | Research only | Lichess-specific inputs/infrastructure; AGPL licensing |
| Leela Chess Zero | Research only | A second engine, not independent proof of assistance |
| Lichess Open Database | Research only | Respect source data/license and matched population |
| Chess Signatures / Elo-disentangled styles | Research ideas | No verified production-ready detectors installed |

## Validation and limits

The number **500 is data coverage, not a percentage-point improvement in detector accuracy**. No validated effect size or false-positive calibration exists for this branch. Full depth-18 on 500 games may take longer than 24 hours depending on CPU, positions and Maia counterfactual work. Hosted workers rotate and rely on encrypted position checkpoints; a review needs consistent secrets and available storage.

Owner exports are available in the process memory for a bounded period (normally two hours) after completion and can disappear after a worker restart. Do not treat a missing export as evidence of any player's conduct. Full cross-restart export persistence is a separate operational requirement.

## Sources

- https://github.com/CSSLab/maia3
- https://github.com/CSSLab/maia4all
- https://github.com/ippolito-cmu/allie
- https://github.com/thomasj02/1e4_ai
- https://github.com/artem-lepin-ml/chess-fraud
- https://github.com/lichess-org/kaladin
- https://github.com/clarkerubber/irwin

No automatic cheating allegation, punishment or ban is triggered by any analysis.
