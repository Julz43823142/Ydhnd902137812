"""Readable descriptive charts. Zoomed axes are explicit; missing stays missing."""
from collections import defaultdict
import math
from statistics import mean
from xml.sax.saxutils import escape

CLASSES = ("bullet", "blitz", "rapid")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def axis_range(values, *, minimum_span=1, ceiling=None):
    present = [float(v) for v in values if finite(v) and v >= 0
               and (ceiling is None or v <= ceiling)]
    if not present:
        return 0.0, float(ceiling or minimum_span)
    lowest, highest = min(present), max(present)
    span = max(minimum_span, highest - lowest)
    center = (lowest + highest) / 2
    low, high = max(0, center - span * .6), center + span * .6
    if ceiling is not None:
        high = min(ceiling, high)
        low = max(0, min(low, high - minimum_span))
        # Percentages already have a meaningful scale. Coarse "nice" steps
        # can turn 59..100 into 40..100 and reintroduce empty space.
        return float(math.floor(low)), float(min(ceiling, math.ceil(high)))
    raw_step = (high - low) / 4
    power = 10 ** math.floor(math.log10(max(raw_step, .000001)))
    step = next(n * power for n in (1, 2, 2.5, 5, 10) if n * power >= raw_step)
    low = max(0, math.floor(low / step) * step)
    high = math.ceil(high / step) * step
    if ceiling is not None:
        high = min(ceiling, high)
    return low, max(low + .000001, high)


def text(x, y, value, size=18, color="#132136", anchor="start"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}" font-family="sans-serif">{escape(str(value))}</text>')


def start(width, height):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>']


def plot(parts, rows, y, title, values, unit, color, *, second=None, first_game=1):
    all_values = values + (second or [])
    lo, hi = axis_range(all_values, minimum_span=10 if unit == "%" else 200 if not unit else 1,
                        ceiling=100 if unit == "%" else None)
    left, right, height, top = 115, 1375, 200, y + 30
    parts.append(text(45, y, title, 23))
    parts.append(f'<rect x="{left}" y="{top}" width="{right-left}" height="{height}" fill="#f8fafc" stroke="#cad5df"/>')
    for fraction in (0, .25, .5, .75, 1):
        yy = top + height * (1 - fraction)
        parts.append(f'<line x1="{left}" y1="{yy}" x2="{right}" y2="{yy}" stroke="#d6e1ea"/>')
        value = lo + (hi - lo) * fraction
        label = f"{value:.1f}" if hi - lo <= 10 else f"{value:.0f}"
        parts.append(text(left - 12, yy + 6, label + unit, 18, anchor="end"))
        xx = left + fraction * (right - left)
        number = first_game + round(fraction * max(0, len(rows) - 1))
        parts.append(text(xx, top + height + 26, number, 17, anchor="middle"))
    parts.append(text((left + right) / 2, top + height + 52, "Game number (chronological)",
                      17, "#526477", "middle"))

    def draw(series, stroke, dashed=False):
        segment = []
        def flush():
            if len(segment) == 1:
                xx, yy = segment[0]
                parts.append(f'<circle cx="{xx:.2f}" cy="{yy:.2f}" r="4" fill="{stroke}"/>')
            elif segment:
                points = " ".join(f"{x:.2f},{y:.2f}" for x, y in segment)
                dash = 'stroke-dasharray="9 6"' if dashed else ""
                parts.append(f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="3" {dash}/>')
            segment.clear()
        for index, value in enumerate(series):
            if not finite(value) or not lo <= value <= hi:
                flush()  # don't connect across missing/invalid observations
                continue
            segment.append((left + (right - left) * index / max(1, len(rows) - 1),
                            top + height * (1 - (value - lo) / (hi - lo))))
        flush()
    draw(values, color)
    if second is not None:
        draw(second, "#e87519", True)
    if not any(finite(v) for v in all_values):
        parts.append(text((left + right) / 2, top + height / 2,
                          "No valid measurements available", 20, anchor="middle"))


def accuracy_bins(rows):
    bins = defaultdict(list)
    for row in rows:
        acc, elo = row.get("official_accuracy"), row.get("opponent_elo")
        if finite(acc) and 0 <= acc <= 100 and finite(elo) and elo >= 0:
            bins[int(elo) // 200 * 200].append(acc)
    return bins


def class_svg(kind, rows):
    parts = start(1440, 1390)
    parts += [text(45, 45, f"{kind.title()} — Fair Play Game-by-Game", 30),
              text(45, 78, f"{len(rows)} collected games · blue: SharkBot estimate · orange dashed: official Chess.com", 18),
              text(45, 106, "Y-axes zoom to observed data. Gaps mean missing data. Charts do not establish misconduct.", 17, "#526477")]
    plot(parts, rows, 150, "Accuracy by Game", [r["estimated_accuracy"] for r in rows], "%", "#2563eb",
         second=[r["official_accuracy"] for r in rows])
    plot(parts, rows, 470, "Opponent Elo by Game", [r["opponent_elo"] for r in rows], "", "#0f766e")
    plot(parts, rows, 790, "Median Think Time by Game — valid PGN clocks only",
         [r["median_think_seconds"] for r in rows], "s", "#7c3aed")
    parts.append(text(45, 1120, "Official Chess.com Accuracy by Opponent Elo — 200-point groups", 22))
    bins = accuracy_bins(rows)
    keys = sorted(bins)
    if keys:
        band, base = 1260 / len(keys), 1305
        for i, key in enumerate(keys):
            center = 115 + (i + .5) * band
            avg = mean(bins[key])
            height = 120 * avg / 100
            parts.append(f'<rect x="{center-band*.32:.1f}" y="{base-height:.1f}" width="{band*.64:.1f}" height="{height:.1f}" fill="#2563eb"/>')
            parts.append(text(center, base - height - 10, f"{avg:.1f}%", 17, anchor="middle"))
            parts.append(text(center, base + 25, f"{key}–{key+199}", 15, anchor="middle"))
            parts.append(text(center, base + 48, f"n={len(bins[key])}", 16, "#526477", "middle"))
    else:
        parts.append(text(115, 1240, "No paired official Accuracy / opponent Elo available"))
    parts.append("</svg>")
    return "".join(parts)


def comparison_svg(grouped):
    bins = {kind: accuracy_bins(grouped.get(kind, [])) for kind in CLASSES}
    keys = sorted(set().union(*(set(v) for v in bins.values())))
    height = max(490, 250 + 52 * len(keys))
    parts = start(1620, height)
    parts += [text(40, 46, "Official Accuracy by Opponent Elo — side-by-side", 30),
              text(40, 79, "Same 200-Elo groups and 0–100% scale in every column. Missing groups stay empty.", 19),
              text(40, 108, "Descriptive averages; opponent strength and position difficulty can explain differences.", 17, "#526477")]
    for column, kind in enumerate(CLASSES):
        left = 35 + column * 535
        parts.append(text(left + 245, 155, kind.title(), 26, anchor="middle"))
        for fraction in (0, .5, 1):
            xx = left + 120 + fraction * 250
            parts.append(text(xx, 185, f"{100*fraction:.0f}%", 16, anchor="middle"))
        if not keys:
            parts.append(text(left + 30, 245, "No official paired scores", 19))
        for index, key in enumerate(keys):
            yy = 220 + index * 52
            parts.append(text(left + 110, yy + 20, f"{key}–{key+199}", 16, anchor="end"))
            values = bins[kind].get(key, [])
            if values:
                avg = mean(values)
                parts.append(f'<rect x="{left+120}" y="{yy}" width="{avg*2.5:.2f}" height="28" fill="#2563eb"/>')
                parts.append(text(left + 380, yy + 20, f"{avg:.1f}%", 17))
                parts.append(text(left + 380, yy + 38, f"n={len(values)}", 14, "#526477"))
            else:
                parts.append(text(left + 245, yy + 20, "No data", 16, "#526477", "middle"))
    parts.append("</svg>")
    return "".join(parts)


def chart_pngs(grouped):
    import cairosvg
    files = [(f"fairplay_{kind}_charts.png", class_svg(kind, grouped[kind]))
             for kind in CLASSES if grouped.get(kind)]
    if files:
        files.append(("fairplay_opponent_elo_comparison.png", comparison_svg(grouped)))
    return [(name, cairosvg.svg2png(bytestring=svg.encode("utf-8"))) for name, svg in files]
