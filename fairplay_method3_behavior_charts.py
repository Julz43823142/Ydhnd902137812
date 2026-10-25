"""Engine-free descriptive context; never consumed by priority scoring."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import exp, log
from statistics import median

from fairplay_charts import finite, start, text
from fairplay_method3_frequency_charts import control_label
from fairplay_method3_opening_shift import opening_choice
from fairplay_method3_period_overlays import periods, COLORS
from fairplay_method3_behavior_evidence import (
    matched_clock_controls, opening_repetition, time_pressure_response)

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
    per_game_timing={}
    pressure_ratios={}
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
        pressure=[d.think for d in valid if finite(getattr(d,'clock_before',None))
                  and 0<=d.clock_before<10]
        ordinary=[d.think for d in valid if finite(getattr(d,'clock_before',None))
                  and d.clock_before>=30]
        # Use each game's OWN normal pace to avoid comparing games with
        # different players, phases or time controls. Undefined normal pace
        # (including all premoves) is missing, not infinite.
        if pressure and ordinary and median(ordinary)>=.25:
            pressure_ratios[g.identity]=median(pressure)/median(ordinary)
        # A single validated time-pressure move is useful as one *game*
        # observation; summary() still requires >=5 distinct games per plotted
        # point. Requiring 5 pressure moves within ONE game lost short episodes.
        clocks[g.identity]={k:median(v) for k,v in categories.items()
                            if len(v)>=(1 if k.startswith('Start ') else 5)}
        if len(valid)>=5:
            # Same verified/reconstructed PGN clocks for charts 8 and 9.
            game_median=median(d.think for d in valid)
            per_game_timing[g.identity]=game_median
            if chosen.get(g.time_class)==g.time_control:
                timing[g.time_class][bucket(g.ended,edges)].append(game_median)
        choice=opening_choice(g) if getattr(g,'moves',None) and type(getattr(g,'color',None)) is bool else None
        if choice:openings[choice[0]].append((g,choice[1]))
    return dict(edges=edges,controls=controls,chosen=chosen,timing=timing,
                per_game_timing=per_game_timing,
                pressure_ratios=pressure_ratios,clocks=clocks,
                openings=openings,coverage=coverage)


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


PALETTE=('#2563eb','#d97706','#059669','#9333ea','#e11d48','#0891b2','#4f46e5','#65a30d','#c026d3','#a16207','#64748b','#94a3b8')

def opening_svg(result,c,marked=None):
    if marked is None:marked=periods(result)
    p=header('7 · Favourite opening move patterns and rating history','First five own moves · opponent first reply shown · all live controls · each bar totals 100%.',2980)
    for i,color in enumerate(('white','black')):
        from fairplay_opening_names import opening_pattern
        rows=[(g,opening_pattern(g)) for g in result.games if g.time_class in KINDS and type(getattr(g,'color',None)) is bool and ('white' if g.color else 'black')==color]
        rows.sort(key=lambda row:(row[0].ended,row[0].identity))
        y=130+i*780
        p.append(text(30,y,color.title()+' · all Bullet, Blitz and Rapid games',25))
        count=min(20,max(1,len(rows)//10))
        counts=Counter(v for _,v in rows)
        eligible={v for v,n in counts.items() if n/len(rows)>=.01} if rows else set()
        for j in range(count):
            chunk=rows[len(rows)*j//count:len(rows)*(j+1)//count]
            eligible.update(v for v,n in Counter(v for _,v in chunk).items() if n>=2 and n/max(1,len(chunk))>=.01)
        choices=[v for v,n in counts.most_common() if v in eligible and v!='Unknown'][:10]
        labels=choices+(['Unknown'] if counts.get('Unknown') else [])+['Other']
        choices=labels[:-1]
        names={'e2e4':'1.e4','d2d4':'1.d4','c2c4':'1.c4','g1f3':'1.Nf3'}
        for j,label in enumerate(labels):p.append(text(30+(j%2)*570,y+30+(j//2)*23,names.get(label,label),14,PALETTE[j]))
        count=min(20,max(1,len(rows)//10))
        for j in range(count):
            chunk=rows[len(rows)*j//count:len(rows)*(j+1)//count]
            if not chunk:continue
            yy=y+190+j*26
            # Count only ACTUAL matching games in a flagged class and period.
            # Date-range intersection alone can falsely color an entire bar.
            from bisect import bisect_left,bisect_right
            dates_by_kind=defaultdict(list)
            for g,_ in chunk:dates_by_kind[g.time_class].append(g.ended)
            overlap=[]
            for span in marked:
                dates=dates_by_kind.get(span['time_class'],())
                n=(bisect_right(dates,span['last_ended'])
                   -bisect_left(dates,span['first_ended']))
                if n:overlap.append((span,n))
            if overlap:
                fraction=sum(n for _,n in overlap)/len(chunk)
                p.append(f'<rect x="25" y="{yy-2}" width="1140" height="25" '
                         f'fill="#ede9fe" fill-opacity="{min(1.,.10+.65*fraction):.3f}"/>')
            p.append(text(30,yy+16,f'{date(chunk[0][0].ended)}',15))
            freq=Counter(v if v in choices else 'Other' for _,v in chunk);x=180
            for k,label in enumerate(labels):
                share=freq[label]/len(chunk);width=690*share
                p.append(f'<rect x="{x}" y="{yy}" width="{width}" height="21" fill="{PALETTE[k]}"/>')
                if share>=.1:p.append(text(x+width/2,yy+16,f'{share:.0%}',15,'white','middle'))
                x+=width
            note=f'n={len(chunk)}'
            if overlap:
                note+=' · Context '+','.join(
                    f"{span['number']}:{n}/{len(chunk)}" for span,n in overlap[:2])
                if len(overlap)>2:note+=f' +{len(overlap)-2} more'
            p.append(text(885,yy+16,note,14))
    p.append(text(30,1690,'Purple: only the labelled flagged games per bar, not the entire opening bar.',17,'#526477'))
    observed=opening_repetition(result)
    if observed:
        context=f"{observed['time_class'].title()} {control_label(observed['time_control'])} · "+(
            f"{observed['color']} · {observed['opponent_context']}")
        p.append(text(30,1730,'Same-choice pair frequency (matched): '+context,17,'#526477'))
        def fmt_repeats(entry):
            return f"{entry['repeat_fraction']:.0%} (n={entry['games']})" if entry else 'unavailable'
        p.append(text(30,1765,'Before '+fmt_repeats(observed['before'])+
                      ' → during '+fmt_repeats(observed['during'])+
                      ' → after '+fmt_repeats(observed['after']),19))
    else:
        p.append(text(30,1730,'Matched repertoire repetition: unavailable (need ≥8 games before/during).',17,'#526477'))
    p.append(text(30,1800,'Controls pooled: top bars mix modes; matched repetition fixes class/control/color/opponent reply.',17,'#526477'))
    p.extend(rating_panels(result,1900,marked=marked))
    return finish(p,2980)


def rating_panels(result, offset, marked=None):
    """Observed pre-game ratings, never inferred post-game rating changes."""
    if marked is None:marked=periods(result)
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
        for span in marked:
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



def window_clock_comparison(result,c,min_games=MIN_GAMES):
    """Backward-compatible interface for exact-control matched date windows."""
    return matched_clock_controls(result,c.get('per_game_timing',{}),
                                  minimum=min_games)


def timing_svg(result,c,marked=None):
    if marked is None:marked=periods(result)
    from bisect import bisect_left,bisect_right
    p=header('8 · Do thinking times move together?','UTC calendar time · shared seconds scale · blue Bullet, orange Blitz, green Rapid.',1040)
    rows=sorted(result.games,key=lambda g:(g.ended,g.identity));times=[g.ended for g in rows]
    # Use equal *calendar-duration* bins, not equal game counts. A single
    # heavily played day must not occupy as much horizontal space as months.
    count=min(30,max(1,len(rows)//20))
    edges=calendar(rows,count)
    grouped={kind:[[] for _ in range(count)] for kind in KINDS}
    for g in rows:
        if g.time_class in grouped and g.time_control==c['chosen'].get(g.time_class):
            value=c['per_game_timing'].get(g.identity)
            if value is not None:grouped[g.time_class][bucket(g.ended,edges)].append(value)
    series={kind:[((edges[j]+edges[j+1])/2,
                   median(values) if len(values)>=MIN_GAMES else None)
                  for j,values in enumerate(grouped[kind])] for kind in KINDS}
    top=200;bottom=680;maximum=max([1]+[v*1.15 for points in series.values() for _,v in points if v is not None])
    x=lambda stamp:100+1030*(stamp-edges[0])/(edges[-1]-edges[0])
    for span in marked:
        a,b=span['first_ended'],span['last_ended']
        if b<edges[0] or a>edges[-1]:continue
        fill,stroke=COLORS[span['priority']]
        p.append(f'<rect x="{x(max(a,edges[0]))}" y="{top}" '
                 f'width="{max(2,x(min(b,edges[-1]))-x(max(a,edges[0])))}" '
                 f'height="480" fill="{fill}" fill-opacity=".2"/>')
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
    for j in range(5):
        stamp=edges[0]+(edges[-1]-edges[0])*j/4
        label=(datetime.fromtimestamp(stamp,timezone.utc).strftime('%d %b %H:%M')
               if edges[-1]-edges[0]<172800 else date(stamp))
        p.append(text(x(stamp),720,label,17,anchor='middle'))
    p.append(text(30,770,'Shared UTC calendar axis · ≥5 timed games/bin/control · gaps indicate low coverage.',18))
    p.append(text(30,810,'Visual bins are descriptive; selected-period numbers use exact game dates.',18))
    comparison=window_clock_comparison(result,c)
    if comparison is not None:
        before,during=comparison['before'],comparison['during']
        if before is not None and during is not None:
            kinds=comparison['comparable']
            name='–'.join(k.title() for k in kinds)
            p.append(text(30,846,f'{name} gap: before {before["gap_seconds"]:.1f}s → '
                          f'during {during["gap_seconds"]:.1f}s (exact selected dates)',18))
            counts=', '.join(
                f'{k.title()} {before["classes"][k]["games"]}/{before["classes"][k]["total_games"]}'
                f' → {during["classes"][k]["games"]}/{during["classes"][k]["total_games"]}'
                for k in kinds)
            p.append(text(30,878,'Valid/total clocks before → during: '+counts,17,'#526477'))
            if comparison['after'] is not None:
                p.append(text(30,910,f'After-period gap: {comparison["after"]["gap_seconds"]:.1f}s '
                              'using the same classes',17,'#526477'))
        else:
            p.append(text(30,846,'Exact-date comparison unavailable: need ≥5 timed games and '
                          '≥60% clock coverage in ≥2 classes.',18,'#526477'))
        controls=', '.join(f'{k.title()} {control_label(value)}'
                           for k,value in comparison['controls'].items())
        if controls:p.append(text(30,933,'Period-matched controls: '+controls,16,'#526477'))
        p.append(text(30,960,'Flanks: same calendar span as selected period, minimum 24 hours.',16,'#526477'))
    return finish(p,1040)


def phase_trends(result,c):
    rows=sorted(result.games,key=lambda g:(g.ended,g.identity))
    count=min(30,max(1,len(rows)//20))
    edges=calendar(rows,count)
    categories=('Early 1–10','Middle 11–30','Late 31+','Start <10s','Start ≥10s')
    # All panels now share an actual UTC calendar axis with Graph 8. Each
    # game contributes its own per-category median to a fixed-control bin.
    grouped={cat:{kind:[[] for _ in range(count)] for kind in KINDS}
             for cat in categories}
    for game in rows:
        kind=game.time_class
        if kind not in KINDS or game.time_control!=c['chosen'].get(kind):continue
        index=bucket(game.ended,edges)
        for cat,value in c['clocks'][game.identity].items():
            if cat in grouped:grouped[cat][kind][index].append(value)
    trends={cat:{kind:[((edges[j]+edges[j+1])/2,summary(grouped[cat][kind][j]))
                       for j in range(count)] for kind in KINDS}
            for cat in categories}
    return rows,trends


def phase_svg(result,c,marked=None):
    from bisect import bisect_left,bisect_right
    if marked is None:marked=periods(result)
    rows,trends=phase_trends(result,c);times=[g.ended for g in rows]
    edges=calendar(rows,min(30,max(1,len(rows)//20)))
    p=header('9 · How does thinking time change?','Blue Bullet · orange Blitz · green Rapid. Lines: median; shaded bands: middle 50% of game medians.',2020)
    for k,kind in enumerate(KINDS):p.append(text(30+k*390,115,kind.title()+' · '+control_label(c['chosen'].get(kind,'unknown')),20,COLORS_LINE[k]))
    x=lambda stamp:100+1030*(stamp-edges[0])/(edges[-1]-edges[0])
    for panel,(cat,series) in enumerate(trends.items()):
        top=180+panel*305;bottom=top+205
        p.append(text(30,top-20,cat+' · seconds per move',23))
        maximum=max([.5]+[value[2]*1.15 for points in series.values() for _,value in points if value])
        y=lambda value:bottom-205*value/maximum
        for span in marked:
            a,b=span['first_ended'],span['last_ended']
            if b<edges[0] or a>edges[-1]:continue
            fill,stroke=COLORS[span['priority']]
            p.append(f'<rect x="{x(max(a,edges[0]))}" y="{top}" '
                     f'width="{max(2,x(min(b,edges[-1]))-x(max(a,edges[0])))}" '
                     f'height="205" fill="{fill}" fill-opacity=".16"/>')
            p.append(text(min(x(a),930),top+16,span['time_class'].title()+' '+span['priority']+' '+str(span['number']),14,stroke))
        for j in range(4):
            yy=y(maximum*j/3)
            p.append(f'<line x1="100" x2="1130" y1="{yy}" y2="{yy}" stroke="#d6e1ea"/>')
            p.append(text(25,yy+6,f'{maximum*j/3:.1f}s',17))
        for k,kind in enumerate(KINDS):
            previous=None
            for index,value in series[kind]:
                if value is None:previous=None;continue
                xx=x(index)
                if previous:
                    px,pv=previous
                    coords=f'{px},{y(pv[1])} {xx},{y(value[1])} {xx},{y(value[2])} {px},{y(pv[2])}'
                    p.append(f'<polygon points="{coords}" fill="{COLORS_LINE[k]}" fill-opacity=".12"/>')
                    p.append(f'<line x1="{px}" y1="{y(pv[0])}" x2="{xx}" y2="{y(value[0])}" stroke="{COLORS_LINE[k]}" stroke-width="2.5"/>')
                p.append(f'<circle cx="{xx}" cy="{y(value[0])}" r="3" fill="{COLORS_LINE[k]}"/>')
                previous=(xx,value)
        for j in range(5):
            stamp=edges[0]+(edges[-1]-edges[0])*j/4
            label=(datetime.fromtimestamp(stamp,timezone.utc).strftime('%d %b %H:%M')
                   if edges[-1]-edges[0]<172800 else date(stamp))
            p.append(text(x(stamp),bottom+25,label,16,anchor='middle'))
    p.append(text(30,1730,'Shared UTC calendar axis · gaps mean fewer than 5 contributing games, never zero.',18))
    p.append(text(30,1760,'Each panel has its own seconds scale. Bands show between-game variation, not proof.',18))
    p.append(text(30,1797,'≥5 contributing games/point; one verified time-pressure move/game is sufficient.',17,'#526477'))
    response=time_pressure_response(result,c.get('pressure_ratios',{}))
    if response:
        def fmt_pair(value):
            return f"{value['median_ratio']:.2f}× (n={value['games']})" if value else 'unavailable'
        p.append(text(30,1844,'Paired time-pressure response · '+
                      response['time_class'].title()+' '+control_label(response['time_control']),19))
        p.append(text(30,1876,'<10s / ≥30s thinking pace: before '+fmt_pair(response['before'])+
                      ' → during '+fmt_pair(response['during'])+' → after '+fmt_pair(response['after']),17))
        p.append(text(30,1908,'Below 1× = faster in time trouble. Matched exact control may differ from plotted control.',17,'#526477'))
        p.append(text(30,1940,'Paired games / all games: before '+
                      str(response['before']['games'])+'/'+str(response['before']['total_games'])+
                      ', during '+str(response['during']['games'])+'/'+str(response['during']['total_games'])+
                      '; interpretation is conditional on reaching both clock states.',16,'#526477'))
    else:
        p.append(text(30,1850,'Paired time-pressure comparison unavailable: need ≥5 games with both clock states '
                      'before/during.',17,'#526477'))
    return finish(p,2020)


def behavior_svgs(result):
    c=build_context(result.games)
    marked=periods(result)  # Stable period identities on all three pages.
    return [(name,svg) for name,svg in [
            ('fairplay_07_openings.png',opening_svg(result,c,marked=marked)),
            ('fairplay_08_clock_convergence.png',timing_svg(result,c,marked=marked)),
            ('fairplay_09_phase_pressure.png',phase_svg(result,c,marked=marked))]]
