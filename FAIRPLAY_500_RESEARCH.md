# Fair Play 500 / Depth 18 / Multi-model research integration

**PR #82 merged by the repository owner on 9 October 2026.**
**Follow-up PR #83 is DRAFT ONLY; do not merge or deploy without new approval.**
#82 superseded #80/#81; #83 further improves optional model availability checks.

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
| Lc0 / Leela Chess Zero | **Official pinned-source CPU build and real model smoke proposed in draft #83** | The new hosted workflow attempts provisioning; no claim of a production-successful build before its real CI smoke passes |
| ChessMimic | **Real opt-in localhost REST adapter** to upstream `/models` and `/get_move` | Must be self-hosted with multi-GB Git LFS artifacts; requires PolyForm **Noncommercial** acknowledgment. Uses the actual opponent clock when available, never clones the player clock. Upstream sometimes uses an opening-book database, not necessarily neural inference. |
| Allie (original) | Local `/predict` sidecar protocol | Requires operator-supplied model server; original release requests GPU and large model assets |
| Allie v2 (legacy) | Local `/predict` sidecar | Historical Qwen/vLLM model; former GitHub link now redirects to the newer model. Not deployed. |
| **Allie 2.0** | **Official local Python `Allie.from_pretrained()` and `analyze()` inference**, or local `/predict` sidecar | Real UCI move probabilities, played-move likelihood and predicted think time using both ratings, actual position history and available observed clocks. Needs separately installed ~11 GB weights, package and several GB RAM; **not auto-provisioned on the Stockfish runner**. |
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
FAIRPLAY_EXTERNAL_MODELS=maia3_23m,maia3_79m,lc0,chessmimic,allie,allie_v2,allie_2,maia4all,kaladin,irwin
FAIRPLAY_EXTERNAL_MAX_POSITIONS=48
FAIRPLAY_EXTERNAL_MAX_SECONDS=600
```

- `FAIRPLAY_MAIA_23M_CHECKPOINT` and `FAIRPLAY_MAIA_79M_CHECKPOINT` are set by checksum-checked preparer scripts, after installing the pinned official `maia3` Python UCI package.
- `FAIRPLAY_LC0_BIN` and `FAIRPLAY_LC0_WEIGHTS` enable Lc0 with its own UCI executable and neural network (not Stockfish weights).
- `FAIRPLAY_CHESSMIMIC_URL=http://127.0.0.1:8000` and `FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE=1` enable the local upstream ChessMimic inference service; the backend must be configured for authorized access and must have actual move-model checkpoints available. Do not enable for commercial usage without legal approval.
- **Allie 2.0 native inference:** install the [official source](https://github.com/y0mingzhang/allie) and predownload [released weights](https://huggingface.co/yimingzhang/allie-2.0) to a local directory containing `config.json` and `model.safetensors`. Set `FAIRPLAY_ALLIE_2_MODEL_DIR=/path/to/model`. Its official CPU API reads the true move prefix and observed historical clocks and returns legal-move probabilities and predicted think time. The model weighs about 11 GB on disk, so **do not share the ordinary Stockfish worker without a separate memory/compute review**. CI tests the real API contract with synthetic local stubs; it has not loaded 11 GB weights on the hosted runner.
- `FAIRPLAY_ALLIE_BRIDGE_URL`, `FAIRPLAY_ALLIE_V2_BRIDGE_URL`, `FAIRPLAY_ALLIE_2_BRIDGE_URL`, `FAIRPLAY_MAIA4ALL_BRIDGE_URL`: operator-provided **local-only** bridges implement `POST /predict` with JSON input containing `model`, `fen`, `moves` (SAN), `rating`, `clock_time`, `opponent_clock_time`, `increment`. Must return `{"model":"<same model id>","move":"e2e4"}` with a legal move. This is a **protocol adapter**; the public projects do NOT promise to expose `/predict` on their own.
- Every configured local HTTP model uses a redirect-blocking, proxy-free transport. A 30x response is rejected instead of forwarding a private game position outside localhost.
- `scripts/benchmark_chessfraud.py --allow-download` needs optional `datasets`.
- `scripts/benchmark_lichess_baseline.py --input local-lichess.pgn[.zst]` needs optional `zstandard` for compressed dumps.

## Performance and correctness caveats

**No defensible measured percent accuracy improvement exists.** Native Allie 2.0 integration is executable *once an operator installs the real model*, but adapter tests do not establish higher cheating-detection recall. 500 vs 100 full-depth games means up to 5× historical primary coverage, not 5× detection accuracy. Expanded searches incur a look-elsewhere effect; HIGH rules remain conservative. Use separately labeled clean and assisted games to quantify recall, false-positive rate, and calibration before any external-model signal enters priority scoring.

Fixed depth is NOT fixed seconds; running 500 primary games, every informative move's Stockfish depth 18, and any required Maia counterfactuals can take **substantially longer than 24 hours** on CPU. GitHub Actions workers rotate every ~4h20 and use encrypted checkpoints on a separate branch; failure to persist/recover that state could prevent a single uninterrupted report. CPU-heavy sidecars must not be provisioned untested on the same GitHub Actions worker.

No automatic bans, no user punishments, no external data treated as independent proof.

## Verified upstream references and licenses

- [Maia-3 official code](https://github.com/CSSLab/maia3) and [23M](https://huggingface.co/UofTCSSLab/Maia3-23M), [79M](https://huggingface.co/UofTCSSLab/Maia3-79M) checkpoints. Official Maia-3 79M weights are AGPLv3; review obligations before deployment.
- [Maia4All](https://github.com/CSSLab/maia4all) - work in progress
- [Original Allie](https://github.com/ippolito-cmu/allie), [legacy Allie v2 URL](https://github.com/y0mingzhang/allie-v2) and [official Allie 2.0](https://github.com/y0mingzhang/allie) ([weights](https://huggingface.co/yimingzhang/allie-2.0)): new 2.0 has real CPU inference and a documented Python API; older versions still need local hosting.
- [ChessMimic](https://github.com/thomasj02/1e4_ai) - PolyForm Noncommercial 1.0.0 including artifacts
- [Lc0](https://github.com/LeelaChessZero/lc0) - GPLv3 engine
- [ChessFraud research repository](https://github.com/artem-lepin-ml/chess-fraud)
- [Kaladin](https://github.com/lichess-org/kaladin) and [Irwin](https://github.com/clarkerubber/irwin)
- [Official Lichess game database](https://database.lichess.org/)

## Draft #83: optional real-model provisioning and acceptance (NOT MERGED)

Draft #83 contains **source-pinned Lc0 0.32.1 CPU build and a small official
network** from the published Lc0 best-net index. The builder checks the full
pinned upstream commit `fd71a2d921b689c5f479d3227c3806c8e272d9c5`
before executing the build, bounds the network download to 64 MiB, rejects
off-domain redirect targets, records the network SHA-256, and will enforce an
operator-provided digest via `FAIRPLAY_LC0_NETWORK_SHA256`. **Without this
expected hash, the model is downloaded from a fixed official HTTPS URL but
is not pre-pinned; do not claim reproducible weights.**

There is now a real (not synthetic) Lc0 test in PR CI. The ordinary hosted bot
worker attempts Leela setup with `continue-on-error`, emits the necessary
`FAIRPLAY_LC0_BIN`/`FAIRPLAY_LC0_WEIGHTS` environment entries only after a
real UCI inference smoke, and records unavailable/error otherwise. This adds
startup time and CPU load. Production suitability still needs monitoring.

Use **only a separately provisioned, adequately resourced worker** for Allie
2.0's ~11 GB weights and ChessMimic's multi-GB noncommercial artifacts.
`python scripts/smoke_fairplay_external_models.py --require allie_2,chessmimic`
performs a real legal-move inference with each explicitly configured provider
on a synthetic board; it returns failure unless *both* actually respond. The
test is never a cheat accusation or accuracy benchmark. The existing adapter
is local-only and will not send real Chess.com FENs to arbitrary internet APIs.

Operator checklist for off-hosted inference workers:

1. Install the official Allie package/weights into the same host as the bot,
   set `FAIRPLAY_ALLIE_2_MODEL_DIR`, and run the real synthetic smoke.
2. For ChessMimic, check PolyForm Noncommercial eligibility with legal/operator,
   install upstream Python 3.12 backend and real Git LFS artifacts on that host,
   start its backend bound to loopback, set `FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE=1`
   plus `FAIRPLAY_CHESSMIMIC_URL`, and run the synthetic smoke.
3. Older Allie and Allie v2 can only run when their actual upstream weights
   and a truthful local `/predict` implementation are installed. Merely
   implementing `/predict` is not proof that upstream model inference works.
4. Maia4All lacks confirmed public pretrained per-user checkpoints; Kaladin
   and Irwin need Lichess-internal signals. Those integrations cannot be
   automatically enabled responsibly on Chess.com accounts.
5. ChessFraud/Lichess data remain offline evaluation sources, not model votes.
   Style embedding/signature research without released weights is research-only.

The owner-only diagnostic now includes `model_coverage_summary` with actual
per-model status and number of measured positions; absent models are not
silently presented as operative. Extra model observations **still cannot
change LOW/HIGH** until blinded validation demonstrates better detection at
an acceptable false-positive rate. The full 500-game Stockfish depth-18
requirement and encrypted checkpoints remain unchanged.

### Dedicated runner activation (draft #83)

For optional heavyweight models, register a **trusted private self-hosted runner**
with sufficient disk/RAM and local model installations. Do not attach secrets
to an untrusted self-hosted runner. Define GitHub Actions repository variable
`SHARKBOT_DAILY_RUNNER` to the runner label *only after separate approval*;
the default remains `ubuntu-latest`. Configure
`FAIRPLAY_ALLIE_2_MODEL_DIR`, `FAIRPLAY_CHESSMIMIC_URL`,
`FAIRPLAY_CHESSMIMIC_ACCEPT_LICENSE`, and other provider-specific local
paths/localhost URLs with repository variables. A 16 GB available-memory
threshold protects the ordinary shared runner from trying to load Allie 2.0
weights; configure a sufficiently large worker and account for Stockfish,
Maia, and operating system allocations.

The manual workflow `.github/workflows/fairplay-model-acceptance.yml` accepts
a comma-separated required-model list and executes **only synthetic opening
positions** on the selected worker. It fails if any requested model does not
produce a legal move. That verifies actual model functionality, not whether
engine assistance can be accurately detected. It never merges/deploys.
