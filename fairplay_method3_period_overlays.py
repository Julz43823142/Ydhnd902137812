"""Period bands shared by rolling history and accuracy frequency charts."""
from bisect import bisect_left, bisect_right
from fairplay_charts import text

COLORS = {"MODERATE": ("#facc15", "#854d0e"),
          "HIGH": ("#fb923c", "#9a3412"),
          "VERY HIGH": ("#f87171", "#991b1b")}

def periods(result):
    source = result.diagnostics["method3"].get("marked_periods", [])
    result_periods = []
    for kind in ("rapid", "blitz", "bullet"):
        games = sorted((g for g in result.games if g.time_class == kind),
                       key=lambda g: (g.ended, g.identity))
        times = [g.ended for g in games]
        spans = []
        for period in source:
            if period.get("time_class") != kind or period.get("priority") not in COLORS:
                continue
            first = bisect_left(times, period["first_ended"])
            last = bisect_right(times, period["last_ended"]) - 1
            if first <= last and first < len(times) and last >= 0:
                spans.append({"first": first, "last": last, "priority": period["priority"]})
        spans.sort(key=lambda p: (p["first"], p["last"]))
        merged = []
        for span in spans:
            if merged and merged[-1]["priority"] == span["priority"] and span["first"] <= merged[-1]["last"] + 2:
                merged[-1]["last"] = max(merged[-1]["last"], span["last"])
            else:
                merged.append(span.copy())
        for span in merged:
            result_periods.append({"time_class": kind, "priority": span["priority"],
                                   "first_ended": times[span["first"]],
                                   "last_ended": times[span["last"]]})
    result_periods.sort(key=lambda p: (p["first_ended"], p["last_ended"], p["time_class"]))
    for i, period in enumerate(result_periods, 1):
        period["number"] = i
    return result_periods

def draw(parts, games, kind, marked, top, bottom, left, right):
    if not games:
        return
    times = [g.ended for g in games]
    for period in marked:
        first = bisect_left(times, period["first_ended"])
        last = bisect_right(times, period["last_ended"]) - 1
        if first > last or first >= len(times) or last < 0:
            continue
        own = period["time_class"] == kind
        fill, stroke = COLORS[period["priority"]] if own else ("#c4b5fd", "#6d28d9")
        x1 = left + (right-left)*max(0, first)/max(1, len(times)-1)
        x2 = left + (right-left)*min(len(times)-1, last)/max(1, len(times)-1)
        parts.append(f'<rect x="{x1:.1f}" y="{top}" width="{max(2,x2-x1):.1f}" height="{bottom-top}" fill="{fill}" fill-opacity=".22" stroke="{stroke}" stroke-width="2"/>')
        label = f'{period["priority"]} {period["number"]}' if own else f'Context {period["number"]}'
        parts.append(text(max(left+35,min(right-35,(x1+x2)/2)),top+19,label,15,stroke,anchor="middle"))
