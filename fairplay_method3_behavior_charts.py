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
        valid=[d for d in g.decisions if valid_clock(d)]
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


def opening_svg(result,c):
    groups=sorted(c['openings'].items(),key=lambda row:(-len(row[1]),row[0]))
    height=1160;p=header('7 · Opening choices and repertoire breadth','Matched color + exact control + opponent first move; first-choice families, not full ECO variants.',height)
    marked=periods(result)
    covered=sum(len(rows) for _,rows in groups)
    p.append(text(30,110,f'{covered}/{len(result.games)} games recognizable · {max(0,len(groups)-3)} additional strata omitted',20))
    displayed=[]
    for color in ('white','black'):
        found=next((row for row in groups if row[0][2]==color),None)
        if found:displayed.append(found)
    displayed += [row for row in groups if row not in displayed][:3-len(displayed)]
    for index,(key,rows) in enumerate(displayed):
        y=150+index*315
        p.append(text(30,y,' · '.join(map(str,key)),22))
        counts=Counter(v for _,v in rows)
        top='; '.join(f'{name}: {n} ({n/len(rows):.0%})' for name,n in counts.most_common(3))
        p.append(text(30,y+30,top,19))
        edges=calendar(result.games,4)
        for i in range(4):
            values=[v for g,v in rows if bucket(g.ended,edges)==i]
            counts=Counter(values);x=30+i*290
            labels=[str(v['number']) for v in marked if v['first_ended']<edges[i+1] and v['last_ended']>=edges[i]]
            p.append(text(x,y+64,date(edges[i])+(' · periods '+','.join(labels[:3]) if labels else ''),16))
            if len(values)<MIN_GAMES:
                p.append(text(x,y+97,f'n={len(values)} · limited',20));continue
            name,n=counts.most_common(1)[0]
            effective=exp(-sum((v/len(values))*log(v/len(values)) for v in counts.values()))
            p.append(f'<rect x="{x}" y="{y+80}" width="{250*n/len(values)}" height="12" fill="#2563eb"/>')
            p += [text(x,y+120,f'{name} {n/len(values):.0%}',18),text(x,y+148,f'n={len(values)} · {len(counts)} choices',19),text(x,y+175,f'Effective breadth: {effective:.1f}',18)]
            ratings=[g.opponent_rating for g,_ in rows if bucket(g.ended,edges)==i and finite(getattr(g,'opponent_rating',None))]
            p.append(text(x,y+200,'Opponent Elo: '+(str(round(median(ratings))) if ratings else 'unknown'),17,'#526477'))
        for j,(label,members) in enumerate(phases(result,[g for g,_ in rows])):
            ids={g.identity for g in members};v=[v for g,v in rows if g.identity in ids];freq=Counter(v)
            desc=f'{len(v)} games · limited' if len(v)<MIN_GAMES else f'{len(v)} games · {len(freq)} choices · top {freq.most_common(1)[0][1]/len(v):.0%}'
            p.append(text(30+j*390,y+225,label,20));p.append(text(30+j*390,y+253,desc,18))
    p.append(text(30,1110,'Breadth = exp(entropy); first-move choices do not identify transposed or memorized variations.',18,'#526477'))
    return finish(p,height)


def timing_svg(result,c):
    height=1180;p=header('8 · Do thinking times converge?','Same UTC calendar bins · fixed most-played exact control per class · median of game medians.',height)
    for i,kind in enumerate(KINDS):
        n=sum(len(v) for v in c['timing'][kind]);alln=sum(c['controls'][kind].values())
        control=c['chosen'].get(kind,'unavailable'); selectedn=c['controls'][kind].get(control,0)
        p.append(text(30,112+i*27,f'{kind.title()} {control_label(control)}: {n}/{selectedn} usable games · {selectedn-n} missing/short · {alln-selectedn} other-control games',19,COLORS_LINE[i]))
    values=[summary(v) for bins in c['timing'].values() for v in bins]
    maximum=max([4]+[v[2]*1.15 for v in values if v]); top,bottom=240,620
    # Cross-class period identity is shown in the shared timelines below.
    for i in range(5):
        v=maximum*i/4;y=bottom-(bottom-top)*i/4
        p.append(f'<line x1="90" x2="1130" y1="{y}" y2="{y}" stroke="#d6e1ea"/>');p.append(text(25,y+5,f'{v:.1f}s',18))
    for color,kind in zip(COLORS_LINE,KINDS):
        previous=None
        for i,vals in enumerate(c['timing'][kind]):
            s=summary(vals);x=90+1040*(i+.5)/6+(KINDS.index(kind)-1)*6
            if not s:previous=None;continue
            mid,q1,q3=s;y=bottom-(bottom-top)*mid/maximum
            p.append(f'<line x1="{x}" x2="{x}" y1="{bottom-(bottom-top)*q1/maximum}" y2="{bottom-(bottom-top)*q3/maximum}" stroke="{color}" stroke-width="5"/>')
            if previous:p.append(f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{x}" y2="{y}" stroke="{color}" stroke-width="3"/>')
            p.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{color}"/>');previous=(x,y)
    for i in range(6):p.append(text(90+1040*(i+.5)/6,655,date(c['edges'][i]),17,anchor='middle'))
    p.append(text(30,700,'Whiskers: middle 50% of game medians. Missing bins are gaps; minimum 5 games per point.',19))
    for i in range(6):
        x=30+(i%3)*390;y=755+(i//3)*145
        s=[summary(c['timing'][k][i]) for k in KINDS]
        gap=f'{abs(s[0][0]-s[1][0]):.2f}s' if s[0] and s[1] else 'unknown'
        p.append(text(x,y,date(c['edges'][i]),21));p.append(text(x,y+30,f'Bullet–Blitz distance: {gap}',19))
        for j,k in enumerate(KINDS):p.append(text(x,y+55+j*22,f'{k}: n={len(c["timing"][k][i])}',17,COLORS_LINE[j]))
        spread=f'{max(v[0] for v in s)-min(v[0] for v in s):.2f}s' if all(s) else 'unknown'
        p.append(text(x,y+124,'Three-class range: '+spread,17))
    p.append(text(30,1090,'Controls are never pooled. Increment effects, time trouble and habits remain possible explanations.',18))
    return finish(p,height)


def phase_svg(result,c):
    height=1240;p=header('9 · Move-number phases and time pressure','Per-game medians, then median across games. Each cell needs ≥5 moves/game and ≥5 games.',height)
    selected=result.diagnostics['method3'].get('selected')
    if selected:p.append(text(30,137,'During dates: '+date(selected['first_ended'])+' – '+date(selected['last_ended'])+' · selected '+result.diagnostics['method3'].get('selected_period_priority',result.priority),18,'#526477'))
    coverage=c['coverage'];p.append(text(30,110,f"Valid clock decisions: {coverage['moves']} · excluded/missing: {coverage['excluded_moves']}",20))
    for i,kind in enumerate(KINDS):
        control=c['chosen'].get(kind);rows=[g for g in result.games if g.time_class==kind and g.time_control==control]
        y=165+i*335;p.append(text(30,y,f'{kind.title()} · exact control {control_label(control) if control else "unavailable"}',24))
        categories=('Early 1–10','Middle 11–30','Late 31+','Start <10s','Start ≥10s')
        for j,name in enumerate(categories):p.append(text(335+j*175,y+42,name,19,anchor='middle'))
        for j,(label,games) in enumerate(phases(result,rows)):
            yy=y+90+j*65;p.append(text(30,yy,label,20));p.append(text(30,yy+24,f'{len(games)} played games',17,'#526477'))
            for col,name in enumerate(categories):
                vals=[c['clocks'][g.identity][name] for g in games if name in c['clocks'][g.identity]]
                s=summary(vals);value=f'{s[0]:.2f}s · n={len(vals)}' if s else f'Unknown · n={len(vals)}'
                p.append(text(335+col*175,yy,value,18,anchor='middle'))
    p.append(text(30,1180,'Pressure uses the clock BEFORE the move (increment is not pre-added). Zero-second premoves remain valid.',18))
    return finish(p,height)


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
