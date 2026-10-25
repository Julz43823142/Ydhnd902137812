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


PALETTE=('#2563eb','#f59e0b','#10b981','#a855f7','#e11d48','#94a3b8')

def opening_svg(result,c):
    p=header('7 · Openings and rating history','White and black separately · all live time controls combined · each bar totals 100%.',2670)
    for i,color in enumerate(('white','black')):
        from fairplay_opening_names import opening_family
        rows=[(g,opening_family(g)) for g in result.games if g.time_class in KINDS and type(getattr(g,'color',None)) is bool and ('white' if g.color else 'black')==color]
        rows.sort(key=lambda row:(row[0].ended,row[0].identity))
        y=130+i*680
        p.append(text(30,y,color.title()+' · all Bullet, Blitz and Rapid games',25))
        count=min(20,max(1,len(rows)//10))
        counts=Counter(v for _,v in rows)
        eligible={v for v,n in counts.items() if n/len(rows)>=.05} if rows else set()
        for j in range(count):
            chunk=rows[len(rows)*j//count:len(rows)*(j+1)//count]
            eligible.update(v for v,n in Counter(v for _,v in chunk).items() if n>=2 and n/max(1,len(chunk))>=.05)
        choices=[v for v,n in counts.most_common() if v in eligible][:5]
        labels=choices+['Other']
        names={'e2e4':'1.e4','d2d4':'1.d4','c2c4':'1.c4','g1f3':'1.Nf3'}
        for j,label in enumerate(labels):p.append(text(30+(j%2)*570,y+30+(j//2)*23,names.get(label,label),16,PALETTE[j]))
        count=min(20,max(1,len(rows)//10))
        for j in range(count):
            chunk=rows[len(rows)*j//count:len(rows)*(j+1)//count]
            if not chunk:continue
            yy=y+100+j*26
            overlap=[v for v in periods(result) if v['first_ended']<=chunk[-1][0].ended and v['last_ended']>=chunk[0][0].ended]
            if overlap:p.append(f'<rect x="25" y="{yy-2}" width="1140" height="25" fill="#ede9fe"/>')
            p.append(text(30,yy+16,f'{date(chunk[0][0].ended)}',15))
            freq=Counter(v if v in choices else 'Other' for _,v in chunk);x=180
            for k,label in enumerate(labels):
                share=freq[label]/len(chunk);width=690*share
                p.append(f'<rect x="{x}" y="{yy}" width="{width}" height="21" fill="{PALETTE[k]}"/>')
                if share>=.1:p.append(text(x+width/2,yy+16,f'{share:.0%}',15,'white','middle'))
                x+=width
            note=f'{len(chunk)} games'
            if overlap:note+=' · Context '+','.join(str(v['number']) for v in overlap)
            p.append(text(885,yy+16,note,14))
    p.append(text(30,1490,'Purple row: overlaps a flagged period. Opening mix is context only; time-control mix can change it.',17,'#526477'))
    p.extend(rating_panels(result,1590))
    return finish(p,2670)


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
    from bisect import bisect_left,bisect_right
    p=header('8 · Do thinking times move together?','One seconds scale · matching periods · blue Bullet, orange Blitz, green Rapid.',900)
    rows=sorted(result.games,key=lambda g:(g.ended,g.identity));times=[g.ended for g in rows]
    count=min(30,max(1,len(rows)//20));series={k:[] for k in KINDS}
    for j in range(count):
        lo=len(rows)*j//count;hi=len(rows)*(j+1)//count;chunk=rows[lo:hi]
        for kind in KINDS:
            values=[]
            for g in chunk:
                valid=[d.think for d in g.decisions if valid_clock(d)]
                if g.time_class==kind and g.time_control==c['chosen'].get(kind) and len(valid)>=5:values.append(median(valid))
            series[kind].append(((lo+hi-1)/2,median(values) if len(values)>=5 else None))
    top=200;bottom=680;maximum=max([1]+[v*1.15 for points in series.values() for _,v in points if v is not None])
    x=lambda i:100+1030*i/max(1,len(rows)-1)
    for span in periods(result):
        a=bisect_left(times,span['first_ended']);b=bisect_right(times,span['last_ended'])-1
        if a>b:continue
        fill,stroke=COLORS[span['priority']]
        p.append(f'<rect x="{x(a)}" y="{top}" width="{max(2,x(b)-x(a))}" height="480" fill="{fill}" fill-opacity=".2"/>')
        p.append(text(min(x(a),930),top+20,span['time_class'].title()+' '+span['priority']+' '+str(span['number']),16,stroke))
    for j in range(6):
        yy=bottom-480*j/5
        p.append(f'<line x1="100" x2="1130" y1="{yy}" y2="{yy}" stroke="#d6e1ea"/>')
        p.append(text(25,yy+6,f'{maximum*j/5:.1f}s',18))
    for k,kind in enumerate(KINDS):
        p.append(text(30+k*390,130,kind.title()+' · '+control_label(c['chosen'].get(kind,'unknown')),22,COLORS_LINE[k]))
        previous=None
        for index,value in series[kind]:
            if value is None:previous=None;continue
            xx=x(index);yy=bottom-480*value/maximum
            if previous:p.append(f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{xx}" y2="{yy}" stroke="{COLORS_LINE[k]}" stroke-width="3"/>')
            p.append(f'<circle cx="{xx}" cy="{yy}" r="4" fill="{COLORS_LINE[k]}"/>');previous=(xx,yy)
    for j in range(5):p.append(text(x((len(rows)-1)*j/4),720,str(1+round((len(rows)-1)*j/4)),18,anchor='middle'))
    p.append(text(30,770,'Shared chronological game number · all history retained. Gaps = fewer than 5 timed games.',18))
    p.append(text(30,810,'Only compare lines in the same period. Similar pace alone does not establish misconduct.',18))
    selected=result.diagnostics['method3'].get('selected')
    if selected:
        before=[];during=[]
        for j in range(count):
            chunk=rows[len(rows)*j//count:len(rows)*(j+1)//count]
            a=series['bullet'][j][1];b=series['blitz'][j][1]
            if not chunk or a is None or b is None:continue
            if chunk[-1].ended<selected['first_ended']:before.append(abs(a-b))
            elif chunk[0].ended>=selected['first_ended'] and chunk[-1].ended<=selected['last_ended']:during.append(abs(a-b))
        note=(f'Bullet–Blitz gap: before {median(before):.1f}s → during {median(during):.1f}s'
              if before and during else 'Bullet–Blitz before/during comparison: insufficient overlapping coverage.')
        p.append(text(30,845,note,18))
    return finish(p,900)


def phase_svg(result,c):
    p=header('9 · Where is thinking time spent?','Class color: blue Bullet · orange Blitz · green Rapid. Light = comparison; dark = flagged dates.',1100)
    cats=('Early 1–10','Middle 11–30','Late 31+','Start <10s','Start ≥10s')
    cells={}
    for k,kind in enumerate(KINDS):
        rows=[g for g in result.games if g.time_class==kind and g.time_control==c['chosen'].get(kind)]
        phase=phases(result,rows)
        if len(phase)==3:phase=phase[:2] if phase[0][1] else [phase[2],phase[1]]
        p.append(text(30,120+k*27,kind.title()+' '+control_label(c['chosen'].get(kind,'unknown'))+' · comparison: '+phase[0][0],18,COLORS_LINE[k]))
        cells[k]=[[summary([c['clocks'][g.identity][cat] for g in games if cat in c['clocks'][g.identity]]) for cat in cats] for _,games in phase]
    for section,indices in enumerate(((0,1,2),(3,4))):
        top=240+section*390;bottom=top+260
        maximum=max([1]+[row[j][0]*1.2 for group in cells.values() for row in group for j in indices if row[j]])
        p.append(text(30,top-15,'Move phases' if section==0 else 'Clock remaining before the move',23))
        for column,j in enumerate(indices):
            base=110+column*(1000/len(indices))
            p.append(text(base,bottom+30,cats[j],19))
            for k,group in cells.items():
                for n,row in enumerate(group):
                    value=row[j];xx=base+k*96+n*40
                    if value:
                        h=230*value[0]/maximum
                        p.append(f'<rect x="{xx}" y="{bottom-h}" width="26" height="{h}" fill="{COLORS_LINE[k]}" fill-opacity="{.35 if n==0 else 1}"/>')
                        p.append(text(xx+13,bottom-h-8,f'{value[0]:.1f}s',15,COLORS_LINE[k],'middle'))
                    else:p.append(text(xx+13,bottom-5,'—',18,COLORS_LINE[k],'middle'))
    p.append(text(30,1020,'Bar length = seconds. — means insufficient data, not zero. At least 5 games per bar.',18))
    return finish(p,1100)


def behavior_svgs(result):
    c=build_context(result.games)
    return [(name,svg) for name,svg in [
            ('fairplay_07_openings.png',opening_svg(result,c)),
            ('fairplay_08_clock_convergence.png',timing_svg(result,c)),
            ('fairplay_09_phase_pressure.png',phase_svg(result,c))]]
