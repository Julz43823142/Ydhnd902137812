"""Descriptive published-Accuracy charts from already collected Method 3 games."""
from collections import Counter
from datetime import datetime, timezone
from math import ceil

from fairplay_charts import start, text
from fairplay_method3_accuracy_frequency import summarize

CLASSES=('bullet','blitz','rapid')
THRESHOLDS=(80,90,95)
COLORS=('#2563eb','#0f766e','#9333ea')


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
        text(35,108,'Cumulative thresholds · descriptive, not evidence of misconduct · calendar dates use UTC.',19,'#526477')))
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
        buckets=calendar_buckets(rows,kind,selected)
        if not buckets:
            parts.append(text(35,y+125,'No collected games in this time class.',23,'#526477'))
            continue
        parts.append(text(35,y+66,'0–100% bars · all exact controls in this class · outlined buckets overlap selected dates.',18,'#526477'))
        width=1120/len(buckets)
        for index,bucket in enumerate(buckets):
            left=40+index*width
            center=left+width/2
            if bucket['selected_dates']:
                own=selected['time_class']==kind
                color='#64748b' if neutral else '#c2410c' if own else '#7c3aed'
                parts.append(f'<rect x="{left+5:.1f}" y="{y+78}" width="{width-10:.1f}" height="316" fill="#f8fafc" stroke="{color}" stroke-width="2"/>')
            for level in (0,50,100):
                yy=y+205-level
                parts.append(f'<line x1="{center-88:.1f}" x2="{center+88:.1f}" y1="{yy}" y2="{yy}" stroke="#d6e1ea"/>')
            for offset,threshold in enumerate(THRESHOLDS):
                available=bucket['available']
                rate=100*bucket['counts'][threshold]/available if available else None
                if rate is not None:
                    parts.append(f'<rect x="{center-70+offset*48:.1f}" y="{y+205-rate:.1f}" width="35" height="{rate:.1f}" fill="{COLORS[offset]}"/>')
                parts.append(text(center,y+272+offset*25,
                    f'≥{threshold}%: {frequency_label(bucket,threshold)}',20,COLORS[offset],'middle'))
            parts.append(text(center,y+237,bucket['label'],21,anchor='middle'))
            parts.append(text(center,y+354,
                f"{bucket['available']} available · {bucket['missing']} missing",19,'#526477','middle'))
            if bucket['selected_dates']:
                parts.append(text(center,y+382,'Overlaps selected dates',18,'#526477','middle'))
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
