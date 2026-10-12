# Fair Play v42 — Daily search audit

The Daily route could trust a recorded depth even when a present search
contract did not certify completed, exact depth-18 work. It also removed
unverified difficult decisions before assessing coverage of the survivors.

Daily search eligibility now checks the completed exact depth contract when
present, retaining compatibility with historical depth-only exports. Invalid
or missing losses, shallow/inconsistent searches and incomplete contracts
remain unverified decisions. They stay in the opportunity denominator rather
than becoming successful moves or disappearing; any such hard decision in a
candidate or comparison period blocks the route. Diagnostics distinguish
capped opportunities from uncapped unverified difficult decisions. No HIGH
threshold, rating expectation, live Maia policy or gameplay success definition
is relaxed. Existing book, forced, conversion and correspondence safeguards
remain in place.

Daily aggregation now computes each game's decision evidence once per review.
Rating and opponent medians are also computed once per candidate window.
The cache lives only during that call; refreshed engine evidence cannot reuse
stale scores. Every chronological candidate window is still assessed.

## Benchmark scope

Local synthetic depth-complete fixtures (10 difficult decisions per game):

| Daily games | v41 aggregation | v42 aggregation | Speedup |
| --- | --- | --- | --- |
| 86 | 94 ms | 17 ms | 5.5x |
| 200 | 314 ms | 65 ms | 4.9x |
| 500 | 1,116 ms | 239 ms | 4.7x |

These are post-processing measurements, **not full Stockfish scan speedups**.
The private 44-game Daily replay retained the same unsupported outcome and
64 opportunities, with zero unverified hard searches; aggregation fell from
18 ms to 9 ms. This case still lacks the required comparable historical
change and opportunity distribution. It is not evidence of fair play.

## Verification

Synthetic tests cover malformed/partial contracts, incomplete historical
reference, retained missing-evidence denominators, valid legacy/contract
parity, call-local cache invalidation and one evidence pass per game.
Private validation is local only and never enters production scoring or public
fixtures. Ban/closure status and target usernames remain excluded from scoring.
