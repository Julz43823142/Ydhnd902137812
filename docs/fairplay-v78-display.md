# v78 display-only results deviation score

The short results-deviation field supplements the category; it does not replace
or influence scoring, search guards, confidence, period selection or charts.
It uses the selected period's positive standardized Elo-result residual and
positive score excess. Each gets a bounded half-saturation transform (2.4 and
0.20 respectively), combined as a geometric mean and rounded to integer points.
The scale is heuristic and uncalibrated, not a misconduct probability. It has
no category thresholds. Accuracy is not included in this results-only index.

Missing/nonfinite input, fewer than five comparable results, insufficient data,
or explicit selected-period classification/reliability guards produce Unavailable.
Nonpositive results excess produces zero. The display caps at99, avoiding a
100-point certainty impression. Five games is only a display minimum, not a new
HIGH path. No usernames or validation labels are inputs.

Includes the v77 chart cleanup: no detached timelines, combined white/black
opening bars, up to20 full-history periods with context highlights, individual
thinking-time scales and two-way phase comparisons. Rating graphs remain.
