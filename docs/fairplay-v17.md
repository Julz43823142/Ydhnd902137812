# Fair Play v17: local human reference and runtime reliability

This remains automated moderator screening, not proof of misconduct. Production
never reads account closure status or validation labels. No account-specific
rules, threshold reductions or public case ledger were introduced.

## Audit of the preceding release

The shared fixed-node Stockfish pool, one-review queue and positional scheduling
were useful changes. Existing human expectedness was still a rating-conditioned
heuristic, not Maia. Two implementation defects were found: a failed engine
replacement could leave borrowers waiting forever, and a deadline during the
positional batch could discard already-completed games. Both have regressions.

The preceding eight-case cold measurement on a four-core runner took about
126–192 seconds (88–100 full fast games; 10–14 deep games). Most time was engine
work. The initial compact-transport-only synthetic comparison was 18.27 versus
17.90 seconds: about 2%, not a claim of a threefold speedup.

## Local Maia-3

The adapter uses the official [Maia-3](https://github.com/CSSLab/maia3) 5M CPU model:
source revision `1e13597c42d4858b7cfd7cfdae01e297263364b2`,
[weight revision](https://huggingface.co/UofTCSSLab/Maia3-5M/tree/b6559de2398d7140b985f28fd2c19fb5e47ddabe)
`b6559de2398d7140b985f28fd2c19fb5e47ddabe`.
The 20,968,049-byte checkpoint is SHA-256 verified before loading. Upstream code
is AGPL-3.0; its source/license remain in the installed dependency, not copied
into this repository. The upstream weight card does not declare a separate
weight license; weights are downloaded from the official host during setup and
are not redistributed in this repository.

Inference runs in an isolated persistent CPU process, one Torch thread, batches
of 32, at most 400 positions per review. The model receives real causal history
(up to eight preceding boards), both players' ratings, and no account identities
or labels. It outputs a distribution over every legal move. Legal moves, black
orientation, rating conditioning and deterministic repetition have a real-model
smoke test. No dependency or model downloads happen during scans.

Selected positions cover difficulty quantiles within each game; selection does
not use successes, CPL or engine matches. Misses remain in the sample. Unknown
engine alternatives receive perfect quality when calculating the **upper bound
on model-expected quality**. Subtracting that bound from observed quality gives
a conservative lower bound on quality excess. Equivalent good moves reduce
information; rare bad moves, theory, forced play and easy conversion do not
produce strong-play information.

A replicated pattern in adjacent games may allocate up to two additional full
deep games within the existing 14-game cap, including their mistakes. It does
not replace representative controls. Existing HIGH/VERY HIGH requirements are
unchanged: neural rarity is not an independent evidence family or an automatic
verdict. This deliberately avoids inventing a misconduct calibration from a
few owner-labelled accounts. The new private **Human Moves** detail shows actual
model coverage, limitations and links to sampled decisions with their model rank
and Stockfish loss. Existing scoring details explicitly identify
the heuristic as a heuristic.

Maia predicts human moves, not misconduct. Lichess and Chess.com ratings, time
controls and elite-player distributions differ. Its probabilities must not be
described as cheating probabilities.

## Runtime and deployment

The normal sample remains up to 200 rated context games, the latest 100 full
24,000-node MultiPV-5 fast games, and selected 320,000-node MultiPV-5 deep games.
No sparse probes or reduced search budget were reintroduced.

`fairplay_engine.py` reads only the score and first principal-variation move
needed by Fair Play, without an asyncio transport thread per engine. Ordinary
chess/Game Review still use python-chess. Real-engine tests compare all decision
metrics with the original transport. The pinned official Stockfish profile-guided
build uses the same AVX2 architecture and neural weights, a separate cache, and
an ordinary-vs-profile-guided fixed-node equivalence check.

Workers obey CPU/memory limits, leave memory for Discord and model inference,
yield CPU priority to interactive work, and fail promptly if replacement fails.
At a deadline only complete games survive; unfinished games never become
fabricated evidence. The one-review queue and channel isolation remain.

Optional local setup:

```sh
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-fairplay-human.txt
python scripts/prepare_fairplay_maia.py /tmp/sharkbot-maia/maia3-5m.pt
export FAIRPLAY_MAIA_CHECKPOINT=/tmp/sharkbot-maia/maia3-5m.pt
python scripts/smoke_fairplay_maia.py
```

The production workflow provisions and checksum-checks the model explicitly.
Startup warms it off the Discord event loop. If it is unavailable, Stockfish
continues and the model detail says so. Model/data caches are bounded,
process-local and never public case storage. Public CI uses invented positions;
private timing comparisons use encrypted temporary artifacts.

## Research and limits

- [Maia-3 / Chessformer](https://arxiv.org/abs/2605.19091): strength-conditioned
  human move prediction, not a ready-made anti-cheat classifier.
- [Maia-2](https://github.com/CSSLab/maia2): predecessor; official maintainers
  recommend Maia-3 for new integrations.
- [Lichess Kaladin](https://github.com/lichess-org/kaladin): a trained classifier
  using Lichess-specific insights. Its purpose does not establish transferable
  thresholds or accuracy guarantees for Chess.com screening.
- [Official Stockfish build guidance](https://github.com/official-stockfish/Stockfish):
  profile-guided compilation improves runtime without lowering search nodes.

One-minute completion is a performance target, not an unconditional guarantee.
Network latency, game length, CPU allocation and deep-review demand vary.
A small validation set cannot establish general detection accuracy. Thresholds
were not adjusted to force named accounts into requested categories.
