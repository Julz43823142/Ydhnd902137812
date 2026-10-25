"""Descriptive published-Accuracy charts from already collected Method 3 games."""
from collections import Counter
from datetime import datetime, timezone
from math import ceil

from fairplay_charts import start, text
from fairplay_method3_accuracy_frequency import summarize
from fairplay_method3_period_overlays import periods, draw

CLASSES=('bullet','blitz','rapid')
THRESHOLDS=(80,90)
COLORS=('#2563eb','#0f766e')


def frequency_summary(games,kind):
    """Reuse the existing published-score coverage and >=90/95 calculations."""
    row=summarize(games)['by_time_class'][kind]
    above80=sum(isinstance(g.accuracy,(int,float)) and not isinstance(g.accuracy,bool)
                and 80<=g.accuracy<=100 for g in games)
    return {'games':len(games),'available':row['accuracy_coverage'],
            'missing':len(games)-row['accuracy_coverage'],
            'counts':{80:above80,90:row['at_least_90'],95:row['at_least_95']}}


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


def rolling_accuracy_counts(values, threshold, *, window=100, stride=10):
    """O(n) rolling published-Accuracy fractions, preserving missing gaps.

    Windows cover PLAYED games, not only games with published scores. The last
    window is displayed even if it does not fall on a stride tick. Unknown
    denominators stay None rather than being treated as zero.
    """
    if window<1 or stride<1:raise ValueError('Positive window and stride required')
    from math import isfinite
    available=[0]
    hits=[0]
    for value in values:
        valid=(type(value) in (float,int) and isfinite(value) and 0<=value<=100)
        available.append(available[-1]+int(valid))
        hits.append(hits[-1]+int(valid and value>=threshold))
    output=[]
    for end in range(window-1,len(values)):
        if (end+1-window)%stride and end!=len(values)-1:continue
        known=available[end+1]-available[end+1-window]
        passed=hits[end+1]-hits[end+1-window]
        output.append((end,passed/known if known else None,known))
    return output


def accuracy_frequency_svg(result):
    """Two-column, real-landscape chart; identical figures and source games."""
    games=result.games
    diagnostic=result.diagnostics['method3']
    selected=diagnostic.get('selected')
    comparisons,omitted=selected_control_comparisons(games,selected)
    height=1290
    parts=start(1200,height)
    parts.extend((text(30,42,'Official Chess.com Accuracy · frequency',28),
        text(30,76,'Count thresholds use published Accuracy only. Missing scores are excluded, never zero.',19),
        text(30,105,'Rolling 100 played games · chronological game numbers · description, not misconduct proof.',17,'#526477')))
    marked_periods=periods(result)
    selected_tier=diagnostic.get('selected_period_priority',result.priority)
    neutral=selected_tier in ('LOW','INSUFFICIENT DATA')

    def class_panel(kind,panel_x,panel_y):
        rows=sorted((g for g in games if g.time_class==kind),
                    key=lambda g:(g.ended,g.identity))
        row=frequency_summary(rows,kind)
        left,right=panel_x+50,panel_x+532
        top,bottom=panel_y+96,panel_y+304
        parts.append(text(panel_x,panel_y,kind.title()+
                          f" · {row['games']} played · {row['available']} available / "
                          f"{row['missing']} missing",21))
        for col,threshold in enumerate(THRESHOLDS):
            parts.append(text(panel_x+col*275,panel_y+33,
                 f'≥{threshold}%: {frequency_label(row,threshold)}',18,COLORS[col]))
        parts.append(text(panel_x,panel_y+59,'Rolling proportion · exact played-game window',16,'#526477'))
        for level in (0,25,50,75,100):
            yy=bottom-(bottom-top)*level/100
            parts.append(f'<line x1="{left}" x2="{right}" y1="{yy:.1f}" y2="{yy:.1f}" '
                         f'stroke="#d6e1ea"/>')
            parts.append(text(left-9,yy+5,f'{level}%',14,anchor='end'))
        n=len(rows)
        for tick in range(5):
            game_no=1+round((n-1)*tick/4) if n else 0
            xx=left+(right-left)*tick/4
            parts.append(text(xx,bottom+23,str(game_no),14,anchor='middle'))
        draw(parts,rows,kind,marked_periods,top,bottom,left,right)
        valid=[g.accuracy if type(g.accuracy) in (int,float) and 0<=g.accuracy<=100
               else None for g in rows]
        for index,threshold in enumerate(THRESHOLDS):
            segments=[];current=[]
            for end_index,fraction,known in rolling_accuracy_counts(valid,threshold):
                if fraction is None:
                    if current:segments.append(current);current=[]
                    continue
                xx=left+(right-left)*end_index/max(1,n-1)
                yy=bottom-(bottom-top)*fraction
                current.append((xx,yy))
            if current:segments.append(current)
            for segment in segments:
                if len(segment)>1:
                    pts=' '.join(f'{x:.2f},{y:.2f}' for x,y in segment)
                    parts.append(f'<polyline points="{pts}" fill="none" '
                                 f'stroke="{COLORS[index]}" stroke-width="3.5"/>')
                else:
                    xx,yy=segment[0]
                    parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4" fill="{COLORS[index]}"/>')
        if n<100:
            parts.append(text(left+60,top+90,'Fewer than 100 games · rolling trend unavailable',
                              16,'#526477'))
        parts.append(text(panel_x,panel_y+366,
              'Blue ≥80% · green ≥90% · shaded periods preserve review context.',15,'#526477'))

    # Two top plots; lower-left third plot; lower-right exact-control table.
    class_panel('bullet',30,150)
    class_panel('blitz',620,150)
    class_panel('rapid',30,635)
    x=620
    parts.append(text(x,635,
        ('Context only · '+selected_tier+' results window' if neutral else 'Selected Method 3 period')
        if comparisons else 'Selected period · exact-control comparison',21))
    parts.append(text(x,662,'Before / during / after · nearest 50 played games each side.',16,'#526477'))
    if comparisons:
        parts.append(text(x,693,selected['time_class'].title()+
                          ' · '+str(selected['games'])+' selected games',17))
        base=718
        for index,comp in enumerate(comparisons):
            y=base+index*151
            parts.append(text(x,y,control_label(comp['time_control'])+
                              ' · available / missing',19))
            for col,threshold in enumerate(THRESHOLDS):
                parts.append(text(x+350+col*135,y,f'≥{threshold}%',16,COLORS[col]))
            for row,(phase,counts) in enumerate(comp['phases'].items()):
                yy=y+31+row*30
                parts.append(text(x,yy,phase+f": {counts['available']}/{counts['missing']} "
                                  f"({counts['games']} games)",15))
                for col,threshold in enumerate(THRESHOLDS):
                    parts.append(text(x+350+col*135,yy,frequency_label(counts,threshold),
                                      15,COLORS[col]))
        if omitted:
            parts.append(text(x,1195,str(omitted)+' more controls omitted from summary.',
                              16,'#526477'))
    else:
        parts.append(text(x,745,'No eligible exact-control selected-window comparison.',17,'#526477'))
    parts.append(text(30,1240,'Accuracy availability is not random; opponent strength, time and length affect observed rates.',17,'#526477'))
    parts.append('</svg>')
    return ''.join(parts)
