# v79 opening and time comparisons

Opening-family labels use the longest matching sequence in a local Lichess
chess-openings snapshot (CC0-1.0; source and exact revision in the JSON). No
network calls during reviews. This is sequence matching, not exhaustive
transposition detection; unknown sequences remain Unknown. At most five named
families qualify at 5% of history or 5% plus two games in a period; all remaining
families are Other. White and black are separated, live controls pooled.

Page8 uses one seconds axis and a shared full-history game axis. Up to30 shared
chronological bins require five timed games per class, with a fixed exact control
per class. Gaps remain gaps. The Bullet–Blitz before/during gap uses only bins
where both classes have coverage and bins wholly before/inside the selected
period. No automatic suspicion points or category change are added.

Page9 groups class-colored bars into move phases and separate time-pressure
categories. Light means comparison (before, or after when before is absent);
dark means selected dates. Missing values are dashes, not zero. Rating charts
and existing classification guards remain unchanged. The score caption is now
Results above expectation, with plain-language explanation.
