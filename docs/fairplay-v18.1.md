# Fair Play v18.1: opportunity-weighted learned gameplay evidence

This corrects a sample-size discontinuity in the v18 Maia/Stockfish comparison.
Six games with four comparable hard decisions each could qualify, whereas twelve
with two each could never qualify. The latter has equal total coverage and more
replication. It should not be discarded merely because games are shorter.

## Evidence accounting

Each game carries weight `min(covered decisions, 4) / 4`, at most one full
reference-game equivalent. Two decisions carry half weight, three carry 0.75.
Contributing games need at least two decisions and two strong hits, as well as
the unchanged signed excess >=0.18 and information >=0.12. All eligible misses
remain in weighted means. One-decision observations influence the signed
reference fractionally but cannot qualify as contributor games.

A period still needs at least six real contributor games and at least 60% of
all games in the chronological period contributing. It additionally needs six
FULL contributor equivalents and at least 24 game-capped decisions. Thus twelve
2-decision contributors can qualify; six 2-decision games cannot. A hundred
moves in one game still contribute at most one equivalent. No independent
probability, p-value, trained threshold, or new VERY HIGH route is introduced.

Deep confirmation likewise needs four full contributor equivalents and sixteen
game-capped decisions, with the existing 75% information retention and stable
paired reference gates. This requires eight confirmed 2-decision games, not
four. Deep allocation preserves the original plan/controls and existing maximum
of 14; additional slots are selected by chronological opportunity coverage,
never by successful hits. Insufficient remaining slots leave HIGH blocked.

The engine budgets, Maia model, sampling, counterfactual roots, clock parsing,
existing priority paths, isolation, and privacy rules are unchanged. Display
raw contributor counts alongside capped evidence and weighted equivalents.

## Verification and limits

Tests cover equal evidence split into 6x4 / 8x3 / 12x2 games, insufficient
coverage, one large game, sparse misses, deep coverage, control preservation,
and seeded human-policy draws at small denominators. Model-drawn null tests
are aggregation checks, not a real-world false-positive estimate.

Re-scoring eight saved private reviews changes no category. Four normal
controls remain LOW; two positive validation cases still remain LOW. This
repairs an aggregation defect; it does not establish improved detection recall.
No private names, positions, labels or reports are included in this repository.

No new persistence, migrations, remote inference, or scan-time downloads.
