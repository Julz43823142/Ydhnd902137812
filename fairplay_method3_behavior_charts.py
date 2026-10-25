"""Engine-free descriptive context; never consumed by priority scoring."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import exp, log
from statistics import median

from fairplay_charts import finite, start, text
from fairplay_method3_frequency_charts import control_label
from fairplay_method3_opening_shift import opening_choice
from fairplay_method3_period_overlays import periods, COLORS

KINDS=('bullet','blitz','rapid')
COLORS_LINE=('#2563eb','#d97706','#059669')
MIN_GAMES=5


def summary(values):
    values=sorted(values)
    if len(values)<MIN_GAMES:return None
    return (median(values),values[int((len(values)-1)*.25)],values[int((len(values)-1)*.75)])


def calendar(games, count=6):
    lo=min(g.ended for g in games); hi=max(g.ended for g in games)+1
    return [lo+(hi-lo)*i/count for i in range(count+1)]


def bucket(stamp, edges):
    return min(len(edges)-2,max(0,int((stamp-edges[0])/(edges[-1]-edges[0])*(len(edges)-1))))


def date(stamp):return datetime.fromtimestamp(stamp,timezone.utc).strftime('%d %b %y')


def valid_clock(d):
    return (getattr(d,'clock_valid',False) is True and finite(getattr(d,'think',None))
            and 0<=d.think<120)


def descriptive_clock(game,d):
    if valid_clock(d):return True
    # Engine timing deliberately rejects time trouble; these descriptive bars must not.
    from fairplay_data import clock_control, think_time
    before=getattr(d,'clock_before',None);after=getattr(d,'clock_after',None)
    if not (finite(before) and finite(after) and min(before,after)>=0):return False
    base,increment=clock_control(game.time_control)
    if base is None:return False
    expected=think_time(before,after,increment)
    return expected is not None and 0<=expected<120 and finite(getattr(d,'think',None)) and abs(expected-d.think)<.001


def build_context(games):
    """One bounded pass over clocks; each game contributes one median per category."""
    edges=calendar(games)
    controls={kind:Counter(g.time_control for g in games if g.time_class==kind) for kind in KINDS}
    chosen={kind:sorted(c,key=lambda k:(-c[k],k))[0] for kind,c in controls.items() if c}
    timing={kind:[[] for _ in range(6)] for kind in KINDS}
    clocks={}
    openings=defaultdict(list)
    coverage=Counter()
    for g in games:
        valid=[d for d in g.decisions if descriptive_clock(g,d)]
        coverage['moves']+=len(valid);coverage['excluded_moves']+=len(g.decisions)-len(valid)
        categories=defaultdict(list)
        for d in valid:
            move=getattr(d,'fullmove',None)
            if type(move) is int and move>0:
                categories['Early 1–10' if move<=10 else 'Middle 11–30' if move<=30 else 'Late 31+'].append(d.think)
            before=getattr(d,'clock_before',None)
            if finite(before) and before>=0:
                categories['Start <10s' if before<10 else 'Start ≥10s'].append(d.think)
        clocks[g.identity]={k:median(v) for k,v in categories.items() if len(v)>=5}
        if len(valid)>=5 and chosen.get(g.time_class)==g.time_control:
            timing[g.time_class][bucket(g.ended,edges)].append(median(d.think for d in valid))
        choice=opening_choice(g) if getattr(g,'moves',None) and type(getattr(g,'color',None)) is bool else None
        if choice:openings[choice[0]].append((g,choice[1]))
    return dict(edges=edges,controls=controls,chosen=chosen,timing=timing,clocks=clocks,openings=openings,coverage=coverage)


def header(title,subtitle,height=1100):
    p=start(1200,height)
    p += [text(30,42,title,28),text(30,78,subtitle,19,'#526477')]
    return p


def finish(p,height):
    p += [text(30,height-24,'Descriptive context only. Missing data, habits and changing opponents can explain patterns.',18,'#526477'),'</svg>']
    return ''.join(p)


def phases(result, rows):
    selected=result.diagnostics['method3'].get('selected')
    if not selected:return [('All history',rows)]
    a,b=selected['first_ended'],selected['last_ended']
    return [('Before',[g for g in rows if g.ended<a]),('During dates',[g for g in rows if a<=g.ended<=b]),('After',[g for g in rows if g.ended>b])]


def bands(p,result,edges,top,bottom):
    for span in periods(result):
        x=lambda t:90+1040*max(0,min(1,(t-edges[0])/(edges[-1]-edges[0])))
        a,b=x(span['first_ended']),x(span['last_ended'])
        fill,stroke=COLORS[span['priority']]
        p.append(f'<rect x="{a}" y="{top}" width="{max(2,b-a)}" height="{bottom-top}" fill="{fill}" fill-opacity=".18"/>')
        p.append(text(min(1050,a+2),top+20,f"{span['priority']} {span['number']}",14,stroke))


PALETTE=('#2563eb','#f59e0b','#10b981','#a855f7','#94a3b8')

def opening_svg(result,c):
    p=header('7 · Opening choices and rating history','Each bar is 100% of games in that period. Colors identify the opening choice.',2200)
    groups=sorted(c['openings'].items(),key=lambda r:-len(r[1]))
    shown=[]
    for color in ('white','black'):
        row=next((r for r in groups if r[0][2]==color),None)
        if row:shown.append(row)
    shown += [r for r in groups if r not in shown][:3-len(shown)]
    names={'e2e4':'1.e4','d2d4':'1.d4','c2c4':'1.c4','g1f3':'1.Nf3'}
    for i,(key,rows) in enumerate(shown):
        y=145+i*285
        p.append(text(30,y,f'{key[0].title()} {control_label(key[1])} · {key[2]} · {key[3]}',25))
        counts=Counter(v for _,v in rows);choices=[v for v,_ in counts.most_common(4)];labels=choices+['Other']
        for j,label in enumerate(labels):
            p.append(text(30+j*230,y+34,names.get(label,label),17,PALETTE[j]))
        edges=calendar(result.games,4)
        for j in range(4):
            vals=[v for g,v in rows if bucket(g.ended,edges)==j];yy=y+64+j*42
            p.append(text(30,yy+18,date(edges[j]),18))
            x=210
            if not vals:p.append(text(x,yy+18,'No games',18));continue
            freq=Counter(v if v in choices else 'Other' for v in vals)
            for k,label in enumerate(labels):
                fraction=freq[label]/len(vals);width=760*fraction
                p.append(f'<rect x="{x}" y="{yy}" width="{width}" height="27" fill="{PALETTE[k]}"/>')
                if fraction>=.09:p.append(text(x+width/2,yy+20,f'{fraction:.0%}',17,'white','middle'))
                x+=width
            p.append(text(990,yy+20,f'{len(vals)} games'+(' *' if len(vals)<5 else ''),17))
    p.append(text(30,1030,f'{len(result.games)} games · {max(0,len(groups)-len(shown))} smaller groups not shown · * small sample',18))
    p.append(text(30,1060,'First-move choices/families, not complete opening variations. Compare within the same row.',18))
    p.extend(rating_panels(result,1120))
    return finish(p,2200)


def rating_panels(result, offset):
    """Observed pre-game ratings, never inferred post-game rating changes."""
    from bisect import bisect_left, bisect_right
    rows=sorted(result.games,key=lambda g:(g.ended,g.identity))
    times=[g.ended for g in rows]
    denominator=max(1,len(rows)-1)
    x=lambda index:100+1030*index/denominator
    p=[text(30,offset+25,'Rating through the games',28),
       text(30,offset+58,'Shared game numbers · rated games only · observed ratings at each game, not after it.',18,'#526477')]
    for k,kind in enumerate(KINDS):
        entries=[(index,g) for index,g in enumerate(rows) if g.time_class==kind and g.rated is True]
        points=[(index,g.rating) for index,g in entries if finite(g.rating) and 100<=g.rating<=4000]
        top=offset+125+k*290;bottom=top+175
        title=kind.title()
        if points:
            values=[value for _,value in points];first,last=values[0],values[-1]
            title+=f' · {first:.0f} → {last:.0f} · {last-first:+.0f} points · {len(points)} games'
        p.append(text(30,top-25,title,23,COLORS_LINE[k]))
        if not points:
            p.append(text(100,top+75,'No rated games with available ratings',20,'#526477'))
            continue
        pad=max(25,(max(values)-min(values))*.12)
        low=max(0,min(values)-pad);high=max(values)+pad
        y=lambda value:bottom-175*(value-low)/(high-low)
        for span in periods(result):
            a=bisect_left(times,span['first_ended']);b=bisect_right(times,span['last_ended'])-1
            if a>b:continue
            own=span['time_class']==kind
            fill,stroke=COLORS[span['priority']] if own else ('#c4b5fd','#6d28d9')
            p.append(f'<rect x="{x(a)}" y="{top}" width="{max(2,x(b)-x(a))}" height="175" fill="{fill}" fill-opacity=".25"/>')
            p.append(text(min(x(a),980),top+17,(span['priority'] if own else 'Context')+' '+str(span['number']),15,stroke))
        for j in range(4):
            value=low+(high-low)*j/3;yy=y(value)
            p.append(f'<line x1="100" x2="1130" y1="{yy}" y2="{yy}" stroke="#d6e1ea"/>')
            p.append(text(88,yy+6,f'{value:.0f}',17,anchor='end'))
        segment=[]
        def flush():
            if segment:
                coords=' '.join(f'{x(index):.2f},{y(value):.2f}' for index,value in segment)
                p.append(f'<polyline points="{coords}" fill="none" stroke="{COLORS_LINE[k]}" stroke-width="2"/>')
                if len(segment)==1:
                    index,value=segment[0]
                    p.append(f'<circle cx="{x(index)}" cy="{y(value)}" r="3" fill="{COLORS_LINE[k]}"/>')
                segment.clear()
        for index,g in entries:
            if finite(g.rating) and 100<=g.rating<=4000:segment.append((index,g.rating))
            else:flush()
        flush()
        for j in range(5):
            index=round((len(rows)-1)*j/4)
            p.append(text(x(index),bottom+26,str(index+1),17,anchor='middle'))
        p.append(text(100,bottom+50,f'Range {min(values):.0f}–{max(values):.0f} · own rating scale · line connects observed games',17,'#526477'))
    p.append(text(30,offset+1000,'Rating changes alone are context, not proof. No smoothing; missing ratings are not filled in.',18,'#526477'))
    return p


def timing_svg(result,c):
    p=header('8 · Thinking time through the games','Shared x-axis: chronological game number across ALL time controls. Lines: recent game medians.',1200)
    rows=sorted(result.games,key=lambda g:(g.ended,g.identity));series={k:[] for k in KINDS}
    for index,g in enumerate(rows):
        valid=[d.think for d in g.decisions if valid_clock(d)]
        if g.time_class in series and c['chosen'].get(g.time_class)==g.time_control and len(valid)>=5:
            series[g.time_class].append((index,median(valid)))
    smooth={}
    for kind,points in series.items():
        smooth[kind]=[]
        for j,(index,value) in enumerate(points):
            recent=[v for pos,v in points[max(0,j-9):j+1] if index-pos<=60]
            smooth[kind].append((index,median(recent) if len(recent)>=5 else None))
    maximum=max([3]+[v*1.15 for pts in smooth.values() for _,v in pts if v is not None])
    from bisect import bisect_left,bisect_right
    times=[g.ended for g in rows]
    for k,kind in enumerate(KINDS):
        top=155+k*315;bottom=top+210
        p.append(text(30,top-25,f'{kind.title()} · {control_label(c["chosen"].get(kind,"unknown"))}',25,COLORS_LINE[k]))
        for span in periods(result):
            a=bisect_left(times,span['first_ended']);b=bisect_right(times,span['last_ended'])-1
            own=span['time_class']==kind;fill,stroke=COLORS[span['priority']] if own else ('#c4b5fd','#6d28d9')
            x=90+1040*a/max(1,len(rows)-1);w=max(2,1040*(b-a)/max(1,len(rows)-1))
            p.append(f'<rect x="{x}" y="{top}" width="{w}" height="210" fill="{fill}" fill-opacity=".25"/>')
            p.append(text(min(x,980),top+18,(span['priority'] if own else 'Context')+' '+str(span['number']),15,stroke))
        for j in range(4):
            yy=bottom-210*j/3
            p.append(f'<line x1="90" x2="1130" y1="{yy}" y2="{yy}" stroke="#d6e1ea"/>');p.append(text(25,yy+6,f'{maximum*j/3:.1f}s',18))
        previous=None
        for index,value in smooth[kind]:
            if value is None:previous=None;continue
            x=90+1040*index/max(1,len(rows)-1);y=bottom-210*value/maximum
            if previous and index-previous[2]<=60:p.append(f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{x}" y2="{y}" stroke="{COLORS_LINE[k]}" stroke-width="3"/>')
            p.append(f'<circle cx="{x}" cy="{y}" r="2" fill="{COLORS_LINE[k]}"/>');previous=(x,y,index)
        for j in range(5):p.append(text(90+1040*j/4,bottom+28,str(1+round((len(rows)-1)*j/4)),18,anchor='middle'))
    p.append(text(30,1120,'Gaps mean insufficient recent games. Minimum 5, at most 10 games within 60 shared game positions.',18))
    return finish(p,1200)


def phase_svg(result,c):
    legend=('Blue: before the selected period · orange: during its dates · purple: after.'
            if result.diagnostics['method3'].get('selected') else 'Blue: all available history; no selected period.')
    p=header('9 · Where is thinking time spent?',legend+' Bar length = seconds.',1200)
    cats=('Early 1–10','Middle 11–30','Late 31+','Start <10s','Start ≥10s')
    colors=('#2563eb','#f97316','#a855f7')
    for i,kind in enumerate(KINDS):
        control=c['chosen'].get(kind);rows=[g for g in result.games if g.time_class==kind and g.time_control==control]
        phase=phases(result,rows);cells=[]
        for label,games in phase:
            cells.append([summary([c['clocks'][g.identity][name] for g in games if name in c['clocks'][g.identity]]) for name in cats])
        maximum=max([1]+[v[0]*1.2 for row in cells for v in row if v])
        y=155+i*310;p.append(text(30,y-25,f'{kind.title()} · {control_label(control) if control else "unavailable"}',25))
        for j,name in enumerate(cats):
            x=80+j*225;p.append(text(x,y+215,name,19))
            for k,row in enumerate(cells):
                value=row[j];bx=x+k*48
                if value:
                    h=170*value[0]/maximum
                    p.append(f'<rect x="{bx}" y="{y+180-h}" width="39" height="{h}" fill="{colors[k]}"/>')
                    p.append(text(bx+19,y+172-h,f'{value[0]:.1f}s',16,colors[k],'middle'))
                else:p.append(text(bx+19,y+177,'—',20,colors[k],'middle'))
        p.append(text(30,y+250,'— = fewer than 5 contributing games; never treated as zero.',18,'#526477'))
    p.append(text(30,1125,'Pressure uses the clock BEFORE the move. Each game needs 5 valid moves in the displayed category.',18))
    return finish(p,1200)


def behavior_svgs(result):
    c=build_context(result.games)
    return [(name,with_period_timelines(svg,result)) for name,svg in [
            ('fairplay_07_openings.png',opening_svg(result,c)),
            ('fairplay_08_clock_convergence.png',timing_svg(result,c)),
            ('fairplay_09_phase_pressure.png',phase_svg(result,c))]]


def with_period_timelines(svg,result):
    """Same numbered calendar spans on every page; never infer a new HIGH."""
    import re
    marked=periods(result)
    if not marked:return svg
    height=int(re.search(r'<svg[^>]* height="(\d+)"',svg).group(1))
    edges=calendar(result.games)
    # Reserve enough lanes for labels as well as actual temporal spans.
    lanes=[]
    ends=[]
    for span in marked:
        x1=70+1060*(span['first_ended']-edges[0])/(edges[-1]-edges[0])
        x2=70+1060*(span['last_ended']-edges[0])/(edges[-1]-edges[0])
        label_end=max(x2,x1+155)
        lane=next((i for i,end in enumerate(ends) if end+8<x1),len(ends))
        if lane==len(ends):ends.append(label_end)
        else:ends[lane]=label_end
        lanes.append((span,x1,x2,lane))
    row_height=50+32*len(ends)
    added=110+3*row_height
    new_height=height+added
    svg=svg.replace(f'height="{height}"',f'height="{new_height}"',1)
    svg=svg.replace(f'viewBox="0 0 1200 {height}"',f'viewBox="0 0 1200 {new_height}"',1)
    parts=[f'<rect x="0" y="{height}" width="1200" height="{added}" fill="white"/>',
           text(30,height+35,'Shared periods · identical numbers and dates across all pages',24),
           text(30,height+64,'Orange/red: original class priority. Purple: same dates in another class, context only.',18)]
    for index,kind in enumerate(KINDS):
        y=height+100+index*row_height
        parts.append(text(30,y,kind.title(),20))
        for span,x1,x2,lane in lanes:
            own=span['time_class']==kind
            fill,stroke=COLORS[span['priority']] if own else ('#c4b5fd','#6d28d9')
            yy=y+12+32*lane
            parts.append(f'<rect x="{x1:.1f}" y="{yy}" width="{max(2,x2-x1):.1f}" height="23" fill="{fill}" fill-opacity=".6"/>')
            label=(span['priority'] if own else 'Context')+' '+str(span['number'])
            parts.append(text(min(x1,980),yy+17,label,16,stroke))
    parts.extend([text(70,new_height-15,date(edges[0]),16),text(1130,new_height-15,date(edges[-1]-1),16,anchor='end')])
    return svg.replace('</svg>',''.join(parts)+'</svg>')
