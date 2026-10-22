"""Descriptive published-Accuracy charts from already collected Method 3 games."""
from collections import Counter
from datetime import datetime, timezone
from math import ceil

from fairplay_charts import start, text
from fairplay_method3_accuracy_frequency import summarize

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


def accuracy_frequency_svg(result):
    """One bounded image: class histories plus the selected exact controls."""
    games=result.games
    diagnostic=result.diagnostics['method3']
    selected=diagnostic.get('selected')
    comparisons,omitted=selected_control_comparisons(games,selected)
    height=1455+(85+190*len(comparisons) if comparisons else 60)
    parts=start(1200,height)
    parts.extend((text(35,42,'Official Chess.com Accuracy · frequency',30),
        text(35,77,'Counts and percentages use available scores; missing scores never count as zero.',21),
        text(35,108,'Cumulative thresholds · descriptive, not evidence of misconduct · chronological game numbers.',19,'#526477')))
    selected_tier=diagnostic.get('selected_period_priority',result.priority)
    neutral=selected_tier in ('LOW','INSUFFICIENT DATA')
    for class_index,kind in enumerate(CLASSES):
        rows=[g for g in games if g.time_class==kind]
        summary=frequency_summary(rows,kind)
        y=150+class_index*420
        parts.append(text(35,y,kind.title(),28))
        parts.append(text(200,y,f"{summary['available']} available · {summary['missing']} missing · {summary['games']} games",21))
        for index,threshold in enumerate(THRESHOLDS):
            parts.append(text(35+index*385,y+34,
                f'≥{threshold}%: {frequency_label(summary,threshold)}',23,COLORS[index]))
        rows=sorted(rows,key=lambda g:(g.ended,g.identity))
        parts.append(text(35,y+66,'Rolling 100 played games · X-axis is game number, not calendar time.',18,'#526477'))
        left,right,top,bottom=70,1140,y+100,y+310
        for level in (0,25,50,75,100):
            yy=bottom-(bottom-top)*level/100
            parts.append(f'<line x1="{left}" x2="{right}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#d6e1ea"/>')
            parts.append(text(55,yy+5,f'{level}%',15,anchor='end'))
        n=len(rows)
        for tick in range(5):
            game_no=1+round((n-1)*tick/4)
            xx=left+(right-left)*(game_no-1)/max(1,n-1)
            parts.append(text(xx,bottom+28,str(game_no),15,anchor='middle'))
        # Show only genuinely qualifying review windows; LOW periods are not shaded.
        from bisect import bisect_left, bisect_right
        times=[g.ended for g in rows]
        palette={'MODERATE':('#facc15','#854d0e'),
                 'HIGH':('#fb923c','#9a3412'),
                 'VERY HIGH':('#f87171','#991b1b')}
        marked=sorted((p for p in diagnostic.get('marked_periods',[])
                       if p.get('time_class')==kind and p.get('priority') in palette),
                      key=lambda p:(p['first_ended'],p['last_ended']))
        for number,period in enumerate(marked,1):
            first=bisect_left(times,period['first_ended'])
            last=bisect_right(times,period['last_ended'])-1
            if first>last or first>=n or last<0:continue
            x1=left+(right-left)*first/max(1,n-1)
            x2=left+(right-left)*last/max(1,n-1)
            fill,stroke=palette[period['priority']]
            parts.append(f'<rect x="{x1:.1f}" y="{top}" width="{max(2,x2-x1):.1f}" height="{bottom-top}" fill="{fill}" fill-opacity=".22" stroke="{stroke}" stroke-width="2"/>')
            parts.append(text(max(left+28,min(right-28,(x1+x2)/2)),top+19,
                              f"{period['priority']} {number}",15,stroke,anchor='middle'))
        window=100
        # Every plotted value covers the previous 100 played games; unavailable
        # official scores are excluded from the denominator, never treated as zero.
        valid=[g.accuracy if isinstance(g.accuracy,(int,float)) and not isinstance(g.accuracy,bool)
               and 0<=g.accuracy<=100 else None for g in rows]
        for offset,threshold in enumerate(THRESHOLDS):
            segments=[]
            current=[]
            for end_index in range(window-1,n):
                if (end_index+1-window)%10 and end_index!=n-1:
                    continue
                sample=valid[end_index+1-window:end_index+1]
                known=[value for value in sample if value is not None]
                if not known:
                    if current:segments.append(current);current=[]
                    continue
                fraction=sum(value>=threshold for value in known)/len(known)
                xx=left+(right-left)*end_index/max(1,n-1)
                yy=bottom-(bottom-top)*fraction
                current.append((xx,yy))
            if current:segments.append(current)
            for segment in segments:
                if len(segment)>1:
                    pts=' '.join(f'{xx:.1f},{yy:.1f}' for xx,yy in segment)
                    parts.append(f'<polyline points="{pts}" fill="none" stroke="{COLORS[offset]}" stroke-width="4"/>')
                else:
                    xx,yy=segment[0]
                    parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4" fill="{COLORS[offset]}"/>')
        if n<window:
            parts.append(text(450,top+105,'Fewer than 100 games; rolling trend unavailable.',19,'#526477'))
        parts.append(text(35,y+365,'Blue ≥80% · green ≥90% · yellow MODERATE / orange HIGH / red VERY HIGH windows',17,'#526477'))
    y=1445
    if comparisons:
        label=f'Context only · {selected_tier} results window' if neutral else 'Selected Method 3 period'
        parts.append(text(35,y,f"{label} · {selected['time_class'].title()}",26))
        parts.append(text(35,y+31,'Exact controls · up to 50 played games before/after; During uses the exact selected games.',19,'#526477'))
        for comparison in comparisons:
            y+=190
            parts.append(text(35,y-120,f"{control_label(comparison['time_control'])} · available / missing",23))
            for column,threshold in enumerate(THRESHOLDS):
                parts.append(text(590+column*220,y-120,f'≥{threshold}%',23,COLORS[column],'middle'))
            for row,(phase,summary) in enumerate(comparison['phases'].items()):
                yy=y-80+row*35
                parts.append(text(35,yy,f"{phase}: {summary['available']} / {summary['missing']} ({summary['games']} games)",21))
                for column,threshold in enumerate(THRESHOLDS):
                    parts.append(text(590+column*220,yy,frequency_label(summary,threshold),21,COLORS[column],'middle'))
        if omitted:
            parts.append(text(35,y+35,f'{omitted} additional selected exact controls omitted; the three most represented are shown.',18,'#526477'))
    else:
        parts.append(text(35,y,'No selected period available for exact-control before/during/after comparison.',22,'#526477'))
    parts.append(text(35,height-24,'Published scores may be selectively available. Different opponent strengths and game lengths can affect these frequencies.',17,'#526477'))
    parts.append('</svg>')
    return ''.join(parts)
