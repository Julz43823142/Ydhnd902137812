# Fair Play v49 — retrieved-period coverage

## Problem

A failed older monthly archive set `primary_archive_partial`, even when every
selected game from a later, fully retrieved period had completed its required
engine work. This blanket flag disabled existing gameplay HIGH paths and could
produce LOW despite supported recent evidence. A partial historical sample must
not be treated as either positive evidence or a clean personal baseline.

## Correction

The collector records the months containing unavailable archives, malformed
archive/game data, unknown rated status or missing player identity. A collection
deadline also marks its current month. Missing `games` payloads are errors rather
than apparently complete empty months.

For a primary-archive-partial review, and only after all observed primary engine
work and required deep work complete, a separate scoring scope starts on the
first UTC day **after the latest incomplete month**. Whole months are excluded
conservatively; no unavailable rows are reconstructed. The selected games retain
the unchanged pre-engine plan, ratings and original chronological indices.
Only rated live games from that suffix enter the existing gameplay scorer and
paired human-policy scorer. No threshold, depth, model or contributor requirement
changes. A supported existing HIGH/VERY HIGH route with deep confirmation can
raise the parent result to HIGH, never VERY HIGH; the parent remains partial and
confidence is capped at MEDIUM. Unsupported primary-archive-partial LOW becomes
INSUFFICIENT DATA. Optional unvisited older context does not activate this route.
Required missing engine/deep work, unlocalized gaps, or too short a suffix cannot
bypass coverage. Daily scoring and its separate reference rules remain unchanged.

The parent report discloses missing archive months and the bounded positive
scope. Review Gates and Engine Analysis show the scoped gate result and actual
supporting original route; whole-history diagnostics remain available separately.
No account identities, closure metadata or validation labels enter this logic.

## Mobile copying

All private detail cards have a persistent **Copy Text** button. It sends the
visible card as ordinary ephemeral text in Discord-sized chunks, without a
file/download or mentions. Copying depends on the message, not the process-local
case cache, so visible cards remain copyable after a bot handoff. Main-report
copying uses the same helper.

## Validation

Synthetic regressions cover positive distributed gameplay, elite and ordinary
controls, one exceptional game, missing recent archives, incomplete engine/deep
work, malformed/unlocalized gaps, preserved chronology, optional context, closure
invariance, confidence caps, localized collection failures, and mobile copying.
Private replay of retained engine metrics reproduced the blanket coverage failure
and restored an existing deep-confirmed fixed-half route solely in the fully
retrieved suffix. That is a historical metric replay, not a fresh production scan
or a guarantee about any future account result. No private reports are committed.

The correction adds bounded final scoring only; no extra engine scans or GitHub
workers. It is not a speed change and has no claimed production runtime gain.
