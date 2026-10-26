"""Descriptive published-Accuracy charts from already collected Method 3 games."""
from collections import Counter
from datetime import datetime, timezone
from math import ceil

from fairplay_charts import start, text
from fairplay_method3_accuracy_frequency import _valid_accuracy
from fairplay_method3_period_overlays import periods, draw

CLASSES=('bullet','blitz','rapid')
THRESHOLDS=(80,90)
COLORS=('#2563eb','#0f766e')


def frequency_summary(games,kind):
    """Count graph-only frequencies in one pass, with the official coverage rule.

    The full player summary also calculates medians and Elo context, none of
    which the frequency image uses. Missing and invalid Accuracy stay missing.
    """
    counts = {80: 0, 90: 0, 95: 0}
    available = 0
    for game in games:
        if game.time_class != kind or not _valid_accuracy(game):
            continue
        available += 1
        for threshold in counts:
            counts[threshold] += game.accuracy >= threshold
    return {'games':len(games),'available':available,
            'missing':len(games)-available,'counts':counts}


def rolling_frequency_points(values, window=100, stride=10):
    """Exact rolling published-Accuracy fractions, O(n) rather than O(n*window).

    Return the same end-game indices, thresholds and missing-data gaps as the
    original sampled-window chart. A final off-stride observation is preserved.
    Values are already validated; None is missing rather than zero.
    """
    if window <= 0 or stride <= 0:
        raise ValueError('Rolling window and stride must be positive.')
    counts = {threshold: 0 for threshold in THRESHOLDS}
    observed = 0
    points = {threshold: [] for threshold in THRESHOLDS}
    for end, value in enumerate(values):
        if value is not None:
            observed += 1
            for threshold in THRESHOLDS:
                counts[threshold] += value >= threshold
        if end >= window:
            dropped = values[end-window]
            if dropped is not None:
                observed -= 1
                for threshold in THRESHOLDS:
                    counts[threshold] -= dropped >= threshold
        if end < window - 1 or ((end + 1 - window) % stride and end != len(values) - 1):
            continue
        for threshold in THRESHOLDS:
            points[threshold].append((end, counts[threshold]/observed if observed else None))
    return points


def calendar_buckets(games,kind,selected=None,limit=4):
    """Cover the complete history, including empty UTC calendar intervals."""
    if not games:return []
    def month(game):
        value=datetime.fromtimestamp(game.ended,timezone.utc)
        return value.year*12+value.month-1
    def month_start(index):
        year,offset=divmod(index,12)
        return datetime(year,offset+1,1,tzinfo=timezone.utc)
    first,last=min(map(month,games)),max(map(month,games))
    width=max(1,ceil((last-first+1)/limit))
    groups=[[] for _ in range(ceil((last-first+1)/width))]
    for game in games:groups[(month(game)-first)//width].append(game)
    buckets=[]
    for index,rows in enumerate(groups):
        begin=first+index*width
        end=min(last+1,begin+width)
        a,b=month_start(begin),month_start(end)
        label=a.strftime('%b %Y') if end-begin==1 else (
            a.strftime('%b %y')+' – '+month_start(end-1).strftime('%b %y'))
        overlaps=bool(selected and selected.get('first_ended') is not None
                      and selected.get('last_ended') is not None
                      and selected['first_ended']<b.timestamp()
                      and selected['last_ended']>=a.timestamp())
        buckets.append({'label':label,'months':end-begin,
                        'selected_dates':overlaps,**frequency_summary(rows,kind)})
    return buckets


def selected_control_comparisons(games,selected,limit=3):
    """Compare nearest played games in each exact control, preserving gaps."""
    if not selected:return [],0
    kind=selected['time_class']
    ordered=sorted((g for g in games if g.time_class==kind),key=lambda g:(g.ended,g.identity))
    first,last=selected['first_game']-1,selected['last_game']
    if not 0<=first<last<=len(ordered):return [],0
    during=ordered[first:last]
    controls=Counter(g.time_control for g in during)
    chosen=sorted(controls,key=lambda control:(-controls[control],control))[:limit]
    comparisons=[]
    for control in chosen:
        phases={
            'Before':[g for g in ordered[:first] if g.time_control==control][-50:],
            'During':[g for g in during if g.time_control==control],
            'After':[g for g in ordered[last:] if g.time_control==control][:50]}
        comparisons.append({'time_control':control,
            'phases':{name:frequency_summary(rows,kind) for name,rows in phases.items()}})
    return comparisons,max(0,len(controls)-len(chosen))


def frequency_label(summary,threshold):
    available=summary['available']
    count=summary['counts'][threshold]
    return f'{count}/{available} ({count/available:.1%})' if available else '0/0 (Unknown)'


def control_label(control):
    try:
        base,increment=(str(control).split('+')+['0'])[:2]
        base,increment=int(base),int(increment)
        return f'{base//60}+{increment}' if base%60==0 else f'{base}s+{increment}s'
    except (ValueError,TypeError):return str(control)


def accuracy_frequency_svg(result):
    """Compact, class-only Accuracy frequencies; no redundant control table.

    The three Bullet/Blitz/Rapid panels fit above the existing comparison of
    thinking times on report page 7. Missing official Accuracy never counts as
    a low score; rolling fractions require at least one published observation.
    """
    parts = start(1200, 600)
    parts.extend((text(28, 42, 'Official Chess.com Accuracy · frequency', 30),
                  text(28, 76, 'Bullet / Blitz / Rapid · only published scores count; missing is not zero.', 19, '#526477'),
                  text(28, 105, 'Rolling 100 played games (minimum 100) · ≥80% blue / ≥90% green.', 18, '#526477')))
    marked = periods(result)
    for index, kind in enumerate(CLASSES):
        rows = sorted((g for g in result.games if g.time_class == kind),
                      key=lambda g: (g.ended, g.identity))
        stats = frequency_summary(rows, kind)
        x0 = index * 400
        left, right, top, bottom = x0 + 44, x0 + 374, 258, 474
        parts.append(text(x0+22, 151, kind.title(), 26))
        parts.append(text(x0+22, 184,
                          f"{stats['available']} available / {stats['missing']} missing · {stats['games']} games",
                          17, '#526477'))
        for j, threshold in enumerate(THRESHOLDS):
            parts.append(text(x0+22+j*186, 216,
                              f'≥{threshold}%: {frequency_label(stats, threshold)}',
                              17, COLORS[j]))
        for level in (0, 25, 50, 75, 100):
            y = bottom-(bottom-top)*level/100
            parts.append(f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#d6e1ea"/>')
            parts.append(text(left-6, y+5, f'{level}%', 13, anchor='end'))
        draw(parts, rows, kind, marked, top, bottom, left, right)
        n = len(rows)
        for tick in range(3):
            game_no = 1 + round((n-1)*tick/2) if n else 0
            xx = left + (right-left)*tick/2
            parts.append(text(xx, bottom+23, str(game_no), 14, anchor='middle'))
        values = [g.accuracy if _valid_accuracy(g) else None for g in rows]
        rolling = rolling_frequency_points(values)
        for j, threshold in enumerate(THRESHOLDS):
            segment = []
            def flush():
                if len(segment) > 1:
                    points = ' '.join(f'{xx:.1f},{yy:.1f}' for xx,yy in segment)
                    parts.append(f'<polyline points="{points}" fill="none" stroke="{COLORS[j]}" stroke-width="3"/>')
                elif segment:
                    xx,yy = segment[0]
                    parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="3" fill="{COLORS[j]}"/>')
                segment.clear()
            for end, fraction in rolling[threshold]:
                if fraction is None:
                    flush()
                    continue
                xx = left + (right-left)*end/max(1,n-1)
                segment.append((xx, bottom-(bottom-top)*fraction))
            flush()
        if n < 100:
            parts.append(text((left+right)/2, top+115, 'Fewer than 100 games',
                              17, '#526477', 'middle'))
    parts.append(text(28, 566,
                      'Periods: own priority color; purple is cross-class context. Descriptive frequencies, not proof.',
                      17, '#526477'))
    parts.append('</svg>')
    return ''.join(parts)
