"""Method 3 page 8: observed rating history and pooled live-class win rate.

A descriptive, read-only report: it does not participate in any priority,
rating, Accuracy, timing, or engine decision. Bullet/Blitz/Rapid games share
one chronological win-rate axis; draws are not wins and missing results are not
losses. Ratings remain separate by time class and plotted only when observed.
"""
from bisect import bisect_left, bisect_right

from fairplay_charts import start, text
from fairplay_method3_behavior_charts import rating_panels
from fairplay_method3_period_overlays import periods, COLORS

CLASSES = frozenset(('bullet', 'blitz', 'rapid'))


def valid_result(game):
    score = getattr(game, 'score', None)
    return type(score) in (int, float) and score in (0, .5, 1)


def counts(games):
    """One completed game contributes one win, draw or loss to its class pool."""
    wins = draws = losses = 0
    for game in games:
        if not valid_result(game):
            continue
        if game.score == 1:
            wins += 1
        elif game.score == .5:
            draws += 1
        else:
            losses += 1
    return {'played':len(games), 'known':wins+draws+losses,
            'wins':wins, 'draws':draws, 'losses':losses}


def rolling_winrate(games, window=50, stride=5, minimum=10, coverage=.60):
    """O(n) windows by played games; no invalid result can become a loss.

    Each returned position is an index into the original mixed-class timeline.
    The last off-stride point is also represented. Early windows can be shown
    after minimum complete results; once 50 games exist, the window stays 50.
    """
    if not (window>0 and stride>0 and minimum>0 and 0<coverage<=1):
        raise ValueError('Invalid rolling win-rate parameters')
    wins = known = 0
    points = []
    for i, game in enumerate(games):
        if valid_result(game):
            known += 1
            wins += (game.score == 1)
        if i >= window:
            old = games[i-window]
            if valid_result(old):
                known -= 1
                wins -= (old.score == 1)
        played = min(i+1,window)
        if (i+1)%stride == 0 or i == len(games)-1:
            rate = (wins/known if known >= minimum and known/played >= coverage
                    else None)
            points.append((i,rate))
    return points


def rolling_class_mix(games, window=50, stride=5):
    """Played-game composition for the SAME windows as the pooled win-rate line."""
    if window < 1 or stride < 1:
        raise ValueError('Invalid class-mix window or stride')
    mix = {kind: 0 for kind in ('bullet', 'blitz', 'rapid')}
    points = []
    for index, game in enumerate(games):
        if game.time_class in mix:
            mix[game.time_class] += 1
        if index >= window:
            old = games[index-window]
            if old.time_class in mix:
                mix[old.time_class] -= 1
        if (index+1) % stride == 0 or index == len(games)-1:
            points.append((index, dict(mix), min(index+1,window)))
    return points


def _label(data):
    n = data['known']
    if not n:
        return f"{data['played']} games · no valid outcomes"
    points = (data['wins'] + data['draws'] / 2) / n
    return (f"{data['played']} games · {n} results · "
            f"W {data['wins']} ({data['wins']/n:.1%}) · "
            f"D {data['draws']} ({data['draws']/n:.1%}) · "
            f"L {data['losses']} ({data['losses']/n:.1%}) · Score {points:.1%}")


def _selected_rows(games, selected):
    if not isinstance(selected,dict) or selected.get('time_class') not in CLASSES:
        return []
    rows = [g for g in games if g.time_class == selected['time_class']]
    first, last = selected.get('first_game'), selected.get('last_game')
    if type(first) is not int or type(last) is not int or not 1<=first<=last<=len(rows):
        return []
    return rows[first-1:last]


def rating_winrate_svg(result):
    rows = sorted((g for g in result.games if g.time_class in CLASSES),
                  key=lambda g:(g.ended,g.identity))
    diagnostic = result.diagnostics['method3']
    selected = diagnostic.get('selected')
    marked = periods(result)
    parts = start(1200,1740)
    parts.extend((text(30,42,'8 · Rating through the games + total win rate',30),
                  text(30,78,'Observed Elo per class above; one combined Bullet/Blitz/Rapid win rate below.',19,'#526477')))
    parts.extend(rating_panels(result,98,marked=marked,compact=False))
    overall=counts(rows)
    parts.append(text(30,1202,'Total win rate through time · all live classes pooled',26))
    parts.append(text(30,1238,_label(overall),16,'#526477'))
    selected_rows = _selected_rows(rows,selected)
    if selected and selected_rows:
        parts.append(text(30,1272,
                          'Selected '+selected['time_class'].title()+' window: '+_label(counts(selected_rows)),
                          17,'#526477'))
    else:
        parts.append(text(30,1272,'Selected period: unavailable · no fabricated outcomes.',17,'#526477'))
    left,right,top,bottom=100,1130,1350,1590
    n=len(rows)
    for fraction in (0,.25,.50,.75,1):
        y=bottom-(bottom-top)*fraction
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#d6e1ea"/>')
        parts.append(text(left-12,y+6,f'{100*fraction:.0f}%',16,anchor='end'))
    x=lambda index:left+(right-left)*index/max(1,n-1)
    times=[g.ended for g in rows]
    if rows:
        for span in marked:
            a=bisect_left(times,span['first_ended'])
            b=bisect_right(times,span['last_ended'])-1
            if a>b or a>=n or b<0:continue
            fill,stroke=COLORS[span['priority']]
            parts.append(f'<rect x="{x(a):.1f}" y="{top}" width="{max(2,x(b)-x(a)):.1f}" '
                         f'height="{bottom-top}" fill="{fill}" fill-opacity=".19" stroke="{stroke}" stroke-width="1.5"/>')
            parts.append(text(min(right-95,max(left+85,(x(a)+x(b))/2)),
                              top+23,span['time_class'].title()+' '+span['priority']+' '+str(span['number']),
                              14,stroke,'middle'))
        # If the selected results window is LOW and not in marked priorities,
        # still show where its dates fall, as a neutral *context* block.
        if selected and not any(
                p['time_class']==selected.get('time_class') and
                p['first_ended']==selected.get('first_ended') and
                p['last_ended']==selected.get('last_ended') for p in marked):
            a=selected.get('first_ended');b=selected.get('last_ended')
            if type(a) in (int,float) and type(b) in (int,float) and a<=b:
                lo=bisect_left(times,a);hi=bisect_right(times,b)-1
                if lo<=hi and lo<n:
                    parts.append(f'<rect x="{x(lo):.1f}" y="{top}" '
                                 f'width="{max(2,x(hi)-x(lo)):.1f}" height="{bottom-top}" '
                                 'fill="#94a3b8" fill-opacity=".14" stroke="#64748b"/>')
                    parts.append(text(min(right-85,max(left+85,(x(lo)+x(hi))/2)),
                                      top+42,'Selected context',14,'#475569','middle'))
    segment=[]
    def flush():
        if len(segment)>1:
            pts=' '.join(f'{xx:.2f},{yy:.2f}' for xx,yy in segment)
            parts.append(f'<polyline points="{pts}" fill="none" stroke="#2563eb" stroke-width="4"/>')
        elif segment:
            xx,yy=segment[0]
            parts.append(f'<circle cx="{xx:.2f}" cy="{yy:.2f}" r="4" fill="#2563eb"/>')
        segment.clear()
    for index,rate in rolling_winrate(rows):
        if rate is None:
            flush()
            continue
        segment.append((x(index),bottom-(bottom-top)*rate))
    flush()
    if len(rows)<10:
        parts.append(text(580,1465,'Need at least 10 completed games for a rolling rate.',18,
                          '#526477','middle'))
    for j in range(5):
        index=round((n-1)*j/4) if n else 0
        parts.append(text(x(index),bottom+30,str(index+1 if n else 0),17,anchor='middle'))

    # Show the time-class composition of each SAME 50-played-game window.
    # A pooled win-rate change can reflect a different mix of game formats.
    colors = (('bullet','#7c3aed'), ('blitz','#0f766e'), ('rapid','#e87519'))
    band_top, band_height = 1635, 30
    bar_width = max(1.0, (right-left)*5/max(1,n-1))
    for index, mix, played in rolling_class_mix(rows):
        xx = max(left, min(right-bar_width, x(index)-bar_width/2))
        yy = band_top
        for kind, color in colors:
            hh = band_height*mix[kind]/played if played else 0
            if hh > 0:
                parts.append(f'<rect x="{xx:.2f}" y="{yy:.2f}" '
                             f'width="{bar_width:.2f}" height="{hh:.2f}" '
                             f'fill="{color}"/>')
                yy += hh
    parts.append(text(30,1694,'Blue: 50-game win rate · Mix: Bullet purple / Blitz teal / Rapid orange.',17,'#526477'))
    parts.append(text(30,1720,'At least 60% valid results · Missing is unknown · Shaded dates are context only.',17,'#526477'))
    parts.append('</svg>')
    return ''.join(parts)
