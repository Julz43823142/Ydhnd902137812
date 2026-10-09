# Fair Play 500 / Depth 18 / Multi-model research integration

**Draft PR #81. This branch must not be merged or deployed without explicit owner approval.**
Replaces superseded, unmerged PR #80.

## Verified architecture and decision contract

- Up to **500 most recent eligible rated standard live Chess.com games**, across a capped 240 monthly archives.
- **Every one of those 500 (or fewer available) primary games receives the ordinary 24,000-node Stockfish screening AND a real UCI fixed-depth 18 Stockfish search** in the production `FAIRPLAY_FULL_DEPTH18=1` mode. Deep searches use real depth contracts and incomplete deep sweeps yield no final priority.
- Existing **Maia-3 5M** human-move policy and paired Stockfish counterfactuals continue independently. Its full-coverage work in depth-18 mode can be substantial; it is *not* equivalent to a 500-game Maia vote.
- The existing false-positive protections, personal baselines, same-control/chronology, and HIGH route thresholds are unchanged. Expanded history is not itself misconduct evidence.
- Each completed review builds a **complete owner-only compressed per-game/per-decision evidence export** before position trees are discarded. SHA-256 manifest integrity and encrypted short-retention state are separate from Discord's public review.
- Owner Evidence button remains **visible but not usable** to anyone other than exact Sharkmeister Discord ID `362606514764251137`. Authorized exports are sent as ephemeral gzip attachments.
- Any model failure is reported as unavailable/failed, never silently counted as a useful observation.

## Which third-party projects are really run?

| Source | Implemented mode | Availability and limits |
|---|---|---|
| Stockfish 19 | **Required**, 500-game depth 18 | Real engine work; GitHub Actions rotations with encrypted checkpoints |
| Maia-3 5M | **Existing active** local policy | Existing worker; this is an additional human comparison, not a cheat probability |
| Maia-3 23M | **Real UCI adapter + official checksum-pinned checkpoint installer** | Production workflow provisions model; new real-inference CI smoke |
| Maia-3 79M | **Real UCI adapter + official checksum-pinned checkpoint installer** | Production workflow provisions model; new real-inference CI smoke; larger CPU/RAM footprint |
| Lc0 / Leela Chess Zero | **Real opt-in UCI adapter** | Requires local Lc0 binary and compatible downloaded network weights; not provisioned by hosted workflow |
| ChessMimic | **Real opt-in localhost REST adapter** to upstream `/models` and `/get_move` | Upstream model must be self-hosted; multi-GB Git LFS artifacts; requires explicit PolyForm **Noncommercial** licensing acknowledgement; never call remote public demo |
| Allie (original) | Local `/predict` sidecar protocol | Requires operator-supplied model server; original release requests GPU and large model assets |
| Allie v2 | Local `/predict` sidecar protocol | Requires actual vLLM 1.7B-parameter backend and operator-owned bridge; not installed on GitHub CPU runner |
| Maia4All | Local `/predict` sidecar protocol | Upstream project still describes public release as work in progress; trained per-player checkpoints not provisioned |
| Kaladin | **Explicit incompatible status** | Lichess-specific private insights; no demonstrated Chess.com-equivalent inference |
| Irwin | **Explicit incompatible status** | Lichess moderation-specific model/data pipeline; not a general chess UCI predictor |
| ChessFraud KDD 2026 | **Real offline labeled benchmark script** (separately run with `--allow-download`) | No production use of its labels; limited controlled dataset with distribution shift |
| Lichess Open Database | **Real offline local PGN/.pgn.zst baseline parser** | Operator downloads permitted game dump; unlabelled human references, NOT clean labels or detection votes |
| Chess Signatures / player-style embedding studies | Research only | No verified released and operational production classifiers |

**Data-coverage honesty:** The optional multi-model sampler evaluates **at most 48 temporally spread useful decisions** by default per available provider, *not* 48 games' complete trees, and it must not be reported as independent confirmation or as a percentage probability. The 500-game depth-18 claim refers specifically to Stockfish, not every external neural model.

## Operator configuration

The production workflow now requests the optional models with:
```
FAIRPLAY_EXTERNAL_MODELS=maia3_23m,maia3_79m,lc0,chessmimic,allie,allie_v2,maia4all,kaladin,irwin
FAIRPLAY_EXTERNAL_MAX_POSITIONS=48
FAIRPLAY_EXTERNAL_MAX_SECONDS=600
```

- `FAIRPLAY_MAIA_23M_CHECKPOINT` and `FAIRPLAY_MAIA_79M_CHECKPOINT` are set by checksum-checked preparer scripts, after installing the pinned official `maia3` Python UCI package.
- `FAIRPLAY_LC0_BIN` and `FAIRPLAY_LC0_WEIGHTS` enable Lc0 with its own UCI executable and neural network (not Stockfish weights).
- `FAIRPLAY_CHESSMIMIC_URL=http://127.0.0.1:8000` and `FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE=1` enable the local upstream ChessMimic inference service; the backend must be configured for authorized access and must have actual move-model checkpoints available. Do not enable for commercial usage without legal approval.
- `FAIRPLAY_ALLIE_BRIDGE_URL`, `FAIRPLAY_ALLIE_V2_BRIDGE_URL`, `FAIRPLAY_MAIA4ALL_BRIDGE_URL`: operator-provided **local-only** bridges implement `POST /predict` with JSON input containing `model`, `fen`, `moves` (SAN), `rating`, `clock_time`, `opponent_clock_time`, `increment`. Must return `{"model":"<same model id>","move":"e2e4"}` with a legal move. This is a **protocol adapter**; the public projects do NOT promise to expose `/predict` on their own.
- `scripts/benchmark_chessfraud.py --allow-download` needs optional `datasets`.
- `scripts/benchmark_lichess_baseline.py --input local-lichess.pgn[.zst]` needs optional `zstandard` for compressed dumps.

## Performance and correctness caveats

**No defensible measured percent accuracy improvement exists.** 500 vs 100 full-depth games means up to 5× historical primary coverage, not 5× detection accuracy. Expanded searches incur a look-elsewhere effect; HIGH rules remain conservative. Use separately labeled clean and assisted games to quantify recall, false-positive rate, and calibration before any external-model signal enters priority scoring.

Fixed depth is NOT fixed seconds; running 500 primary games, every informative move's Stockfish depth 18, and any required Maia counterfactuals can take **substantially longer than 24 hours** on CPU. GitHub Actions workers rotate every ~4h20 and use encrypted checkpoints on a separate branch; failure to persist/recover that state could prevent a single uninterrupted report. CPU-heavy sidecars must not be provisioned untested on the same GitHub Actions worker.

No automatic bans, no user punishments, no external data treated as independent proof.

## Verified upstream references and licenses

- [Maia-3 official code](https://github.com/CSSLab/maia3) and [23M](https://huggingface.co/UofTCSSLab/Maia3-23M), [79M](https://huggingface.co/UofTCSSLab/Maia3-79M) checkpoints. Official Maia-3 79M weights are AGPLv3; review obligations before deployment.
- [Maia4All](https://github.com/CSSLab/maia4all) - work in progress
- [Allie](https://github.com/ippolito-cmu/allie) and [Allie v2](https://github.com/y0mingzhang/allie-v2) - require model hosting or custom serving
- [ChessMimic](https://github.com/thomasj02/1e4_ai) - PolyForm Noncommercial 1.0.0 including artifacts
- [Lc0](https://github.com/LeelaChessZero/lc0) - GPLv3 engine
- [ChessFraud research repository](https://github.com/artem-lepin-ml/chess-fraud)
- [Kaladin](https://github.com/lichess-org/kaladin) and [Irwin](https://github.com/clarkerubber/irwin)
- [Official Lichess game database](https://database.lichess.org/)
