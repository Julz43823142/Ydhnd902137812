# Fast single-PV screening and Stockfish worker recovery

The 500-game scope and Stockfish 19 NNUE stay unchanged. The fast pass
now uses **MultiPV 1**, **24,000 nodes** and an independent root-restricted
search if the player did not play Stockfish's best move. This does **not**
impose a one-second time budget or reproduce undisclosed Chess.com settings.

Deep evidence remains **MultiPV 5**: critical/unique flags use the best-to-
second-best gap and a spread across alternatives. Removing those candidate
scores from deep searches would silently invalidate existing HIGH scoring.
Full mode remains bullet depth 12, rapid/blitz depth 18.

The 80% Discord screenshot maps to a shared error handler for UCI
engine exceptions or timeouts; the underlying cause cannot be inferred
from the image alone. Previously a single failed worker was replaced but
its original error was immediately raised and aborted the scan. Now the
affected position retries once on a fresh worker; a second failure or
failed replacement still aborts safely without manufacturing results.
Checkpointed completed positions remain eligible for resume.

Sanitized worker health logs reveal timeout / terminated / uci-error,
restart status and attempt number, never usernames, FENs, moves or raw
exceptions. Successful reports include an engine restart counter.

**Limitations:** A/B throughput and detection-quality measurements are
still needed. MultiPV 1 can change fast top-three/critical metrics and
non-full-mode deep selection. Version/config changes invalidate previous
MultiPV 5 fast-position checkpoint contracts.
