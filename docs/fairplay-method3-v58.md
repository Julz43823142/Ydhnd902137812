# Method 3 — v58 live Discord route

Historical v58 contract. Current Method 3: [v59 with 1,000-game history](fairplay-method3-v59.md).

`FAIRPLAY_METHOD3=1` switches the existing Fair Play review entry point to up to
200 rated standard live games. The Daily Puzzle workflow enables it by default;
set repository variable `FAIRPLAY_METHOD3=0` to restore the previous V51 route.
No second bot/token, Stockfish calls, Maia calls or compute-shard dispatch occurs
in this review. Ordinary chess/review Stockfish is preserved. Model provisioning
and Fair Play model warmup are skipped in this mode. In-flight processes are not
restarted by the implementation.

HIGH uses the exploratory results-only development boundary: at least ten games
in a chronological time-class period; ≥80% have valid ratings/results; descriptive
Elo-points residual ≥2.4 AND score excess ≥.20. LOW means no qualifying observed
period, not proof of fair play. MODERATE may be supported by official Accuracy
against an adequately covered, strictly earlier personal reference (8 points,
60% above reference upper quantile). Accuracy alone cannot create HIGH.
Insufficient samples stay INSUFFICIENT DATA. VERY HIGH is deliberately unavailable.
Timing is descriptive. Status/closure fields and validation identities are never
scoring inputs. Thresholds are post-hoc development settings, not holdout-validated
probabilities. Underrating, genuine improvement, rating lag and hot streaks can
qualify. Numerous correlated windows increase false-positive risk.

Official Accuracy may be missing. No estimated Accuracy is fabricated and no
population Elo→Accuracy model is claimed. Earlier same-control, own/opponent
rating-matched personal references are described as history, not clean games.
Groups show expected/actual points and actual winrate separately, never fake
Elo-derived win probabilities. The charts retain missing-data gaps, official
Accuracy, opponent strength, descriptive clocks and add a rolling expected versus
observed points chart. Buttons and copy text use method-specific explanations;
engine/model cards explicitly say not performed. Progress/footer identify v58.

Collection uses fixed validated PubAPI endpoints and existing bounded retries,
no arbitrary URLs. A five-minute deadline bounds network work and cancellation
is checked before scoring/reporting. Existing executor/queue keeps HTTP, PGN
parsing and graphs off the Discord event loop. Network collection and chart
rendering, not saved-data calculation, dominate runtime. Tens of seconds to a
few minutes is an estimate, not a measured whole-account latency guarantee.

Activation: merge, then allow normal bot handoff or start a new run after the
active review finishes. Never cancel a running review just to deploy this mode.
Verify progress/report footer `fairplay-v58-method3-results`. Rollback variable
requires the next normal process start. The existing engine route remains
available for independent gameplay confirmation.

## Discord presentation and chat

The main report retains priority, sample/confidence, a plain-language reason,
expected/actual points, and the screening limitation. Report Details preserves
the selected period and technical explanation. Existing charts and detailed
buttons remain available; presentation changes do not alter classification.

Mentioning SharkBot in Fair Play Task Force enables text replies using the
existing chat provider and rate limits. This channel never enters confirmation,
free-box claim, trade/donation or AI command-planning paths. Slash `/alert`
uses its own authenticated subscription UI; chat cannot change subscriptions.
