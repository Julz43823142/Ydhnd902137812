"""Readable dark Insights pages; bounded SVG renderer, no browser or engine."""
from collections import Counter
from html import escape
import math
from statistics import mean
import cairosvg
from shark_insights_data import grouped

PAGES = ['Overview', 'Game Results', 'SharkBot Game Shapes', 'Game Phases',
         'Opening Performance', 'Opening Mastery', 'Tactics', 'Move Quality',
         'Pieces', 'Castling', 'Calendar', 'Geography', 'Trends & Opponents']
COLORS = ['#81b64c', '#999999', '#ef6860', '#49bce0', '#d5b45e', '#ae92e0']


class Canvas:
    def __init__(self, report, page):
        self.parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="2000" viewBox="0 0 1400 2000">', '<rect width="1400" height="2000" fill="#222321"/>']
        self.y = 240
        self.text(60, 75, f'SharkBot Insights · {PAGES[page]}', 38)
        self.text(60, 125, f"{report['player']} · {report['time_class'].title()} · {'Rated' if report['rated'] else 'Casual'} · {report['color'].title()} · last {report['days']} days", 25, '#c5c7bf')
        self.text(60, 166, f"{len(report['games'])} valid games · timezone {report['timezone']} · accuracy: published scores only", 23, '#c5c7bf')
        self.text(60, 1930, 'Partial sample' if report['limits'] else 'Public archive sample · no new engine analysis', 23, '#d5b45e')
        self.text(60, 1970, f"V1 · {page + 1}/{len(PAGES)} · details: use the Details button", 22, '#c5c7bf')

    def text(self, x, y, text, size=25, color='#f1f1ed'):
        self.parts.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="DejaVu Sans,sans-serif" font-size="{size}">{escape(str(text))}</text>')

    def title(self, text):
        self.text(60, self.y, text, 30)
        self.y += 50

    def note(self, text):
        import textwrap
        for line in textwrap.wrap(text, 96):
            self.text(60, self.y, line, 23, '#c5c7bf')
            self.y += 32
        self.y += 15

    def bars(self, rows, title, limit=12):
        self.title(title)
        rows = list(rows)
        maximum = max([v for _, v in rows] or [1]) or 1
        total = sum(v for _, v in rows)
        for label, value in rows[:limit]:
            self.text(60, self.y + 21, str(label)[:43], 23)
            width = 480 * value / maximum
            self.parts.append(f'<rect x="650" y="{self.y}" width="{width:.2f}" height="27" rx="3" fill="#49bce0"/>')
            self.text(1150, self.y + 22, f'{value} ({100 * value / total:.1f}%)' if total else '0', 22)
            self.y += 42
        if len(rows) > limit:
            self.note(f'{len(rows) - limit} additional categories are omitted from this chart.')
        self.y += 20

    def stacks(self, groups, title, limit=12, labels=('Win', 'Draw', 'Loss')):
        self.title(title)
        self.text(650, self.y, ' / '.join(labels) + ' · counts and percentages', 22, '#c5c7bf')
        self.y += 35
        rows = list(groups.items())
        for name, row in rows[:limit]:
            total = sum(row.get(k, 0) for k in labels)
            self.text(60, self.y + 20, str(name)[:43], 22)
            x = 650
            for j, k in enumerate(labels):
                width = 650 * row.get(k, 0) / total if total else 0
                self.parts.append(f'<rect x="{x:.2f}" y="{self.y}" width="{width:.2f}" height="24" fill="{COLORS[j]}"/>')
                x += width
            caption = ' / '.join(f'{row.get(k, 0)} ({100 * row.get(k, 0) / total:.0f}%)' if total else '0' for k in labels)
            self.text(650, self.y + 48, caption, 20)
            self.y += 65
        if len(rows) > limit:
            self.note(f'{len(rows) - limit} additional categories omitted.')
        self.y += 15

    def donut(self, counts, title):
        self.title(title)
        total = sum(counts.values())
        offset = 0
        radius = 95
        circ = 2 * math.pi * radius
        for i, (label, value) in enumerate(counts.items()):
            fraction = value / total if total else 0
            self.parts.append(f'<circle cx="220" cy="{self.y + 105}" r="{radius}" fill="none" stroke="{COLORS[i % len(COLORS)]}" stroke-width="48" stroke-dasharray="{circ * fraction:.3f} {circ * (1-fraction):.3f}" stroke-dashoffset="{-offset:.3f}" transform="rotate(-90 220 {self.y + 105})"/>')
            offset += circ * fraction
            self.text(420, self.y + 25 + i * 35, f'{label}: {value} · {fraction * 100:.1f}%', 24, COLORS[i % len(COLORS)])
        if not total:
            self.text(120, self.y + 105, 'No data', 24)
        self.y += 265

    def accuracy_bars(self, rows):
        for label, value in rows:
            self.text(60, self.y + 25, label)
            self.parts.append(f'<rect x="600" y="{self.y}" width="{6*value}" height="32" fill="#49bce0"/>')
            self.text(1220, self.y + 25, f'{value:.1f}')
            self.y += 60
        self.y += 30

    def weekdays(self, groups):
        self.title('Results by weekday · Win / Draw / Loss')
        base = self.y + 320
        for i, name in enumerate(('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')):
            row = groups.get(name, {})
            total = sum(row.get(k,0) for k in ('Win','Draw','Loss'))
            x, y = 80 + i*180, base
            for j, key in enumerate(('Win','Draw','Loss')):
                h = 280 * row.get(key,0)/total if total else 0
                y -= h
                self.parts.append(f'<rect x="{x}" y="{y}" width="120" height="{h}" fill="{COLORS[j]}"/>')
            self.text(x, base+35, name[:3])
            self.text(x, base+65, f'n={total}', 20)
            self.text(x, base+95, '/'.join(str(row.get(k,0)) for k in ('Win','Draw','Loss')), 19)
        self.y = base + 140

    def svg(self):
        height = max(1000, int(self.y + 150))
        svg = ''.join(self.parts) + '</svg>'
        return svg.replace('height="2000"', f'height="{height}"').replace('0 0 1400 2000', f'0 0 1400 {height}').replace('y="1930"', f'y="{height-70}"').replace('y="1970"', f'y="{height-30}"')


def result_counts(games):
    c = Counter(g['outcome'] for g in games)
    return {k: c[k] for k in ('Win', 'Draw', 'Loss')}


def ordered(groups):
    return dict(sorted(groups.items(), key=lambda kv: -sum(kv[1].get(k, 0) for k in ('Win', 'Draw', 'Loss'))))


def accuracy_note(c, games):
    known = [g['accuracy'] for g in games if g['accuracy'] is not None]
    c.note(f'Published game accuracy: {mean(known):.1f} · coverage {len(known)}/{len(games)} games.' if known else 'Published accuracy: Not available. Missing scores are never counted as zero.')


def render_svg(report, page):
    if not 0 <= page < len(PAGES):
        raise ValueError('Invalid Insights page')
    c = Canvas(report, page)
    games = report['games']
    if not games:
        c.title('No usable games in this selection')
        c.note('Try another time control or a longer period. See Details for skipped records and unavailable archives.')
        return c.svg()
    if page == 0:
        c.donut(result_counts(games), 'Games played')
        c.stacks({'All games': result_counts(games)}, 'Results')
        accuracy_note(c, games)
        bands = grouped(games, 'rating_band')
        c.stacks(ordered(bands), 'Results by opponent rating · most played bands', 10)
        old = report['previous']
        c.note(f'Previous non-overlapping {report["days"]}-day period: {len(old)} games; ' + (f'{100 * result_counts(old)["Win"] / len(old):.1f}% wins.' if old else 'no usable comparison data.'))
    elif page == 1:
        for outcome in ('Win', 'Draw', 'Loss'):
            counts = Counter(g['reason'] for g in games if g['outcome'] == outcome)
            c.donut(dict(counts.most_common()), f'{outcome}: termination reasons')
    elif page == 2:
        c.stacks(ordered(grouped(games, 'shape')), 'Results by SharkBot Game Shape')
        c.note('Ordered definitions: Short Game ≤15 moves; Long Battle ≥60; Material Swing: material-balance range ≥8 pawn units; Endgame Battle: ends with ≤20 total non-pawn material; Check-rich: ≥8 checks; otherwise Other Game.')
        c.note('These are reproducible descriptive categories, not Chess.com game shapes or engine assessments.')
        c.donut(dict(Counter(g['shape'] for g in games).most_common()), 'Games by shape')
    elif page == 3:
        c.stacks(grouped(games, 'phase'), 'Results by final position phase')
        for color in ('White', 'Black'):
            c.bars(Counter(g['phase'] for g in games if g['color'] == color).items(), f'Ending phase · {color}')
        c.note('Endgame: ≤20 non-pawn material points across both sides (N/B=3, R=5, Q=9). Otherwise opening through move 10; middlegame afterwards. Phase accuracy is unavailable without trusted per-move review data.')
    elif page == 4:
        for color in ('White', 'Black'):
            counts = Counter(g['opening'] for g in games if g['color'] == color)
            top = dict(counts.most_common(7))
            remaining = sum(counts.values()) - sum(top.values())
            if remaining:
                top['Other openings'] = remaining
            c.donut(top, f'Most played opening families · {color}')
            c.y += 70
        c.note('Local opening families, not exact variations. Counts include only this selection.')
    elif page == 5:
        c.title('Local opening-reference coverage')
        for color in ('White', 'Black'):
            sample = [g for g in games if g['color'] == color and g['book_moves'] is not None]
            deviated = [g for g in sample if g['deviation'] is not None]
            c.note(f'{color}: {len(sample)} reference-eligible games. ' + (f'Mean own reference moves: {mean(g["book_moves"] for g in sample):.1f}.' if sample else 'No reference data.'))
            if deviated:
                c.note(f'{sum(g["deviation"]["own"] for g in deviated)}/{len(deviated)} observed first deviations were played by this player. Mean first deviation: move {mean(g["deviation"]["move"] for g in deviated):.1f}.')
        c.note('A reference move continues a prefix in our pinned local Lichess opening database. The first missing prefix is a reference deviation, not a mistake or proof that theory ended. Lines ending before a deviation are censored. Nonstandard starts are excluded.')
        c.stacks(ordered(grouped(games, 'opening')), 'Most played families · results', 10)
    elif page == 6:
        counts = Counter()
        for g in games:
            counts.update(g['tactics'])
        c.bars([( {'New absolute pins': 'New pieces pinned to the king', 'Captures of undefended pieces': 'Undefended enemy pieces captured', 'Geometric forks (not verified wins)': 'Moves attacking multiple pieces'}.get(k,k),v) for k,v in counts.items()], 'Observed patterns · player moves only')
        c.note('Geometric forks attack at least two enemy non-pawn pieces after a move. Targets may be defended; this does not establish a winning tactic. Pins count newly absolutely pinned enemy pieces. Undefended captures exclude en passant.')
        c.note('No missed-tactic rates, forced mate searches, hanging-piece losses or tactical win claims are inferred. These require a stronger opportunity model.')
    elif page == 7:
        c.title('Published game accuracy')
        accuracy_note(c, games)
        rows = []
        for outcome in ('Win', 'Draw', 'Loss'):
            sample = [g['accuracy'] for g in games if g['outcome'] == outcome and g['accuracy'] is not None]
            if sample:
                rows.append((f'{outcome} · {len(sample)} scores', round(mean(sample), 1)))
        known = sum(g['accuracy'] is not None for g in games)
        c.donut({'Published score': known, 'No published score': len(games)-known}, 'Accuracy coverage')
        c.accuracy_bars(rows)
        c.note('Move labels such as Brilliant or Blunder are unavailable from the public API. No move-quality estimates are invented.')
    elif page == 8:
        counts = Counter()
        phases = {}
        for g in games:
            counts.update(g['pieces'])
            for p, v in g['piece_phases'].items():
                phases.setdefault(p, Counter()).update(v)
        c.donut(dict(counts), 'Moves per piece · player moves only')
        c.stacks(phases, 'Piece moves by phase', labels=('Opening', 'Middlegame', 'Endgame'))
        c.note('Pieces are identified before the move: promotions count as pawn moves; castling counts as a king move. Per-piece accuracy unavailable.')
    elif page == 9:
        c.stacks(grouped(games, 'castle'), 'Results by own castling side')
        c.stacks(grouped(games, 'castle_phase'), 'Results by castling phase')
        pairs = [dict(g, pair=f'{g["castle"]} / opponent {g["opponent_castle"]}') for g in games]
        c.stacks(grouped(pairs, 'pair'), 'Own / opponent castling', 9)
    elif page == 10:
        c.stacks(grouped(games, 'daypart'), f'Results by time of day · {report["timezone"]}')
        c.note('Uses recorded game END time. Night 00–06; Morning 06–12; Afternoon 12–18; Evening 18–24. This is not the time of each move, especially for Daily games.')
        c.weekdays(grouped(games, 'weekday'))
    elif page == 11:
        c.stacks(ordered(grouped(games, 'country')), 'Public opponent countries · top 16', 16)
        c.note('Country codes describe public profile settings, not verified location. International, unknown, unavailable and not-fetched profiles remain separate. Up to 100 most frequent opponents are fetched per report.')
    elif page == 12:
        months = sorted(grouped(games, 'month').items())
        c.stacks(dict(months[-13:]), 'Results by month · most recent 13', 13)
        # Longer selections retain coverage and identify omitted months explicitly.
        c.title('Published accuracy over time')
        samples = [(name, mean(row['accuracies']), len(row['accuracies'])) for name, row in months if row['accuracies']]
        if not samples:
            c.note('Not available: no published scores in this selection.')
        else:
            base, height = c.y + 210, 180
            c.text(60, c.y, 'Monthly mean · gaps indicate missing published accuracy', 23, '#c5c7bf')
            points = []
            for i, (name, row) in enumerate(months):
                x = 100 + 1180 * i / max(1, len(months) - 1)
                if not row['accuracies']:
                    if len(points) > 1:
                        c.parts.append('<polyline points="' + ' '.join(points) + '" fill="none" stroke="#49bce0" stroke-width="3"/>')
                    points = []
                    continue
                value = mean(row['accuracies'])
                y = base - height * value / 100
                points.append(f'{x:.1f},{y:.1f}')
                c.parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#49bce0"/>')
                if len(months) <= 13 or i % 2 == 0:
                    c.text(x - 20, y - 12, f'{value:.0f}', 18)
                    c.text(x - 40, base + 30, name[2:], 18)
            if len(points) > 1:
                c.parts.append('<polyline points="' + ' '.join(points) + '" fill="none" stroke="#49bce0" stroke-width="3"/>')
            for value in (0, 50, 100):
                c.text(45, base - height * value / 100, str(value), 18)
            c.y = base + 75
            c.note(f'Published-score coverage: {sum(n for _, _, n in samples)}/{len(games)} games. Missing scores are excluded.')
    return c.svg()


def render(report, page):
    return cairosvg.svg2png(bytestring=render_svg(report, page).encode())


def details(report):
    groups = grouped(report['games'], 'rating_band')
    bands = sorted(groups, key=lambda k: int(k.split('–')[0]) if k != 'Unknown' else 99999)
    lines = [f"{report['version']} · {report['player']}", f"UTC period: {report['period_start']} → {report['period_end']}",
             f"Coverage: {report['counts']}", f"API requests: {report['api_requests']} · cache hits: {report['cache_hits']}",
             'Limits: ' + ('; '.join(report['limits']) or 'No collection failures reported'),
             'Opponent rating bands: games · wins/draws/losses']
    for band in bands:
        r = groups[band]
        lines.append(f'{band}: {sum(r[k] for k in ("Win", "Draw", "Loss"))} · {r["Win"]}/{r["Draw"]}/{r["Loss"]}')
    return '\n'.join(lines)[:1900]
