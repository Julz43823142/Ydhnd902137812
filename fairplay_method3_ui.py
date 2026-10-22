"""Discord presentation for results-only Method 3; no engine claims."""
import discord
from fairplay_config import DISCLAIMER


def fmt(value, percent=False):
    return 'Unknown' if value is None else f'{value*100:.1f}%' if percent else f'{value:.1f}'


def result_embed(result):
    from fairplay_ui import priority_color
    d=result.diagnostics['method3'];s=d['subject'];r=(d.get('selected') or {}).get('results',s['overall_results'])
    icon={'LOW':'🟢','MODERATE':'🟡','HIGH':'🟠','VERY HIGH':'🔴','INSUFFICIENT DATA':'⚪'}.get(result.priority,'⚪')
    e=discord.Embed(title='🛡️ Fair Play Review — '+result.username,color=priority_color(result.priority),
        description=f'{icon} **{result.priority} review priority**')
    accuracy_games=sum(g.accuracy is not None for g in result.games)
    e.add_field(name='Results confidence',value=f'**{result.confidence}**',inline=False)
    e.add_field(name='Sample / coverage',value=(
        f'{len(result.games)} live history games: '
        f'{d.get("rated_games",len(result.games))} rated · {d.get("unrated_games",0)} unrated'
        + (' · incomplete collection' if result.partial else '')
        + f'\nPublished Accuracy: {accuracy_games}/{len(result.games)} games.'),inline=False)
    frequency=d.get('accuracy_frequency',{}).get('by_time_class',{})
    if frequency:
        lines=[]
        for kind in ('blitz','rapid','bullet'):
            row=frequency.get(kind,{})
            n=row.get('accuracy_coverage',0)
            if not n:continue
            groups=row.get('opponent_context',{})
            near=groups.get('near',{})
            lines.append(
                f"{kind.title()}: **{row['at_least_90']}/{n} ≥90%**, "
                f"**{row['at_least_95']}/{n} ≥95%** "
                f"(near-Elo ±200: {near.get('at_least_90',0)}/{near.get('games',0)} ≥90%)")
        if lines:
            e.add_field(name='High Accuracy frequency · descriptive',
                value='\n'.join(lines)+'\nRaw frequencies alone are descriptive; a separate guarded historical change can support MODERATE.',
                inline=False)
    signals=d.get('accuracy_frequency_signals') or []
    if signals:
        lines=[]
        for signal in signals:
            line=(
                f"{signal['time_class'].title()} {signal.get('time_control','')}: recent {signal['recent_at_least_90']}/{signal['recent_games']} "
                f"vs earlier {signal['baseline_at_least_90']}/{signal['baseline_games']} "
                f"at ≥90% Accuracy · {signal['distinct_opponents']} recent opponents.")
            if len('\n'.join(lines+[line]))>850:break
            lines.append(line)
        if len(lines)<len(signals):
            lines.append(f'{len(signals)-len(lines)} additional time controls omitted from this card.')
        e.add_field(name='Earlier → recent Accuracy frequency · exploratory',
            value='\n'.join(lines)+'\nThis change can support MODERATE, never HIGH. Not a cheating probability.',
            inline=False)
    selected=d.get('selected')
    if selected:
        label=('Selected results window · separate from Accuracy signal'
               if d.get('priority_basis')=='accuracy_frequency'
               else 'Selected chronological period')
        e.add_field(name=label,value=f"{selected['time_class'].title()} · {selected['games']} games · {period_dates(selected)}\nExpected points: **{fmt(r['expected_points'])}** · scored: **{fmt(r['actual_points'])}**\nWinrate: **{fmt(r['win_fraction'],True)}**",inline=False)
    quality=d.get('data_quality') or {}
    if quality:
        n=quality.get('history_games',len(result.games))
        archives=quality.get('archive_coverage') or {}
        details=(f"Valid rating pairs: {quality.get('valid_rating_pairs',0)}/{n} · "
                 f"opponents identified: {quality.get('identified_opponents',0)}/{n} · "
                 f"published Accuracy: {quality.get('published_accuracy_games',0)}/{n}")
        if quality.get('collection_partial') or archives.get('incomplete_archive_months'):
            details+='\nArchive coverage incomplete; unobserved games are not evidence of misconduct.'
        e.add_field(name='Data reliability · observed coverage',value=details[:1024],inline=False)
    if selected and selected.get('classification_guards'):
        support=selected.get('rating_support') or {}
        ramp=support.get('opening_rating_ramp_evidence')
        if ramp:
            e.add_field(
                name='New-account rating calibration · Elo caveat',
                value=(f"First {ramp['sample_rated_games']} rated {selected['time_class']} games: "
                       f"first 10 median Elo {ramp['first_10_median_elo']:.0f} → "
                       f"last 10 {ramp['last_10_median_elo']:.0f} "
                       f"(+{ramp['rating_gain']:.0f}). "
                       'An early rating ramp can mimic an Elo results anomaly. '
                       'Results-only HIGH is capped at MODERATE, not cleared.'),inline=False)
        e.add_field(name='HIGH evidence safeguards',value=(
            'Applied: '+', '.join(selected['classification_guards']).replace('_',' ')
            +f"\nRated anchors in the previous year: {support.get('rated_anchor_games_365d','Unknown')}. "
            'Additional Accuracy cannot bypass an unreliable Elo or repeated-opponent safeguard.'
        )[:1024],inline=False)
    e.add_field(name='Important',value='Not proof of cheating. Human review is required before any action.',inline=False)
    e.set_footer(text=f'Version: {result.version} · uncalibrated heuristic, not probability')
    return e


def detail_embed(result,mode):
    from fairplay_ui import priority_color
    d=result.diagnostics['method3'];s=d['subject']
    e=discord.Embed(title=f'{mode} — {result.username}',description='Results-based screening using game outcomes and published Accuracy; no engine analysis.',color=priority_color(result.priority))
    if mode in ('Performance', 'Report Details'):
        e.add_field(name='Explanation',value='\n'.join(result.reasons)[:1024],inline=False)
        e.add_field(name='Limits',value=d['limits'][:1024],inline=False)
        selected=d.get('selected')
        if selected:
            q=selected['results']
            e.add_field(name='Selected chronological period',value=(
                f"{selected['time_class'].title()} · games {selected['first_game']}–{selected['last_game']} · {period_dates(selected)}\n"
                f"Expected / observed points: {fmt(q['expected_points'])} / {fmt(q['actual_points'])}\n"
                f"Score excess: {fmt(q['score_excess'],True)} · descriptive residual: {fmt(q['standardized_residual'])}"),inline=False)

    if mode in ('Performance','Report Details','Clusters & History'):
        rows=[]
        for change in d.get('history_changes',[])[:4]:
            a,b=change['baseline'],change['recent']
            rows.append(f"{change['time_class'].title()} {change['time_control']} · prior/recent games {a['games']}/{b['games']}\n"
                f"Points above Elo expectation: {fmt(a['score_excess'],True)} → {fmt(b['score_excess'],True)}\n"
                f"Published Accuracy median: {fmt(a['accuracy_median'])} → {fmt(b['accuracy_median'])}\n"
                f"Median game think time: {fmt(a['think_median'])} → {fmt(b['think_median'])} sec\n"
                + ('Comparable reference' if change['results_comparable'] else 'Limited reference'))
        e.add_field(name='Earlier → recent · same control',value=('\n\n'.join(rows) or 'Not enough comparable earlier/recent games.')[:1024],inline=False)
        e.add_field(name='History scope',value=f"All {len(result.games)} collected live games are eligible, rated and unrated, searched in {d.get('blocks_checked',0)} chronological blocks of at most 200 games per time class, plus bridges across block boundaries. {d.get('searched_windows',0)} distinct windows were checked with 5/10/20/50/100/200-game widths. Earlier references stay strictly earlier. History-change charts are descriptive context, not separate HIGH votes.",inline=False)
    if mode in ('Engine Analysis','Human Moves'):
        e.add_field(name='Not performed',value='This method does not run Stockfish or Maia. No engine ranks, human-move likelihoods or deep confirmation are claimed.',inline=False)
    elif mode=='Review Gates':
        chosen=d.get('selected') or {}
        if chosen.get('classification_guards'):
            rating=chosen.get('rating_support') or {}
            opponents=chosen.get('opponent_support') or {}
            e.add_field(name='Applied evidence safeguards',value=(
                ' · '.join(chosen['classification_guards']).replace('_',' ')+'\n'
                +f"Rated anchors (365d): {rating.get('rated_anchor_games_365d','?')} · "
                +f"Rating baseline: {rating.get('anchor_status','unknown')}\n"
                +f"Identified opponents in window: {opponents.get('period_named_games',0)}/{chosen.get('games',0)}. "
                "These safeguards cap results-backed HIGH and VERY HIGH even when historical Accuracy supports the review."
            )[:1024],inline=False)
        e.add_field(name='Configured boundaries',value='Rated and unrated share outcome thresholds, but rated-game anchors affect Elo reliability. HIGH: valid chronological results, Elo-points residual ≥2.4, score excess ≥20 percentage points and a search-adjusted Elo-model bound ≤0.01. Five–nine-game HIGH also requires every game to be a win with complete result coverage. MODERATE: results pass the looser 0.10 model guard, or adequately covered matched earlier Accuracy rises ≥8 points with ≥60% above its upper reference and a search-adjusted reference-model bound ≤0.10. No one-game HIGH. VERY HIGH requires HIGH results evidence, at least 20 games, a search-adjusted results bound ≤0.001, and a matched earlier Accuracy reference check passing its own bound ≤0.01. Model assumptions are uncalibrated; these bounds are not cheating probabilities.',inline=False)
        if d.get('selected') and d['selected'].get('search_guard'):
            g=d['selected']['search_guard']
            a=d['selected'].get('accuracy_search_guard',{})
            e.add_field(name='History-search guard',value=f"{g['searched_windows']} distinct windows · HIGH guard: {'PASS' if g['high_pass'] else 'FAIL'} · short-sample coverage: {'PASS' if g['short_sample_complete'] else 'FAIL'} · Accuracy review guard: {'PASS' if a.get('moderate_pass') else 'FAIL'}. Ratings may be inaccurate in either rated or unrated games; game independence is a model assumption.",inline=False)
            model=a.get('reference_model','unavailable').replace('_',' ')
            e.add_field(name='Accuracy reference uncertainty',value=(
                f'Model: {model}. Earlier sample size and reused reference pools are accounted for. '
                'Comparable, stationary scores remain an assumption; this is not a cheating probability.'),inline=False)
        counts=d.get('period_guard_counts') or {}
        if counts:
            e.add_field(name='Evidence limits across searched periods',value='\n'.join(
                f"{reason.replace('_',' ')}: {count} periods" for reason,count in sorted(counts.items())
            )[:1024]+'\nOverlapping periods are not independent evidence.',inline=False)
    elif mode=='Timing':
        e.add_field(name='Descriptive clocks only',value='PGN clocks supply the think-time charts. Timing does not change this method’s priority.',inline=False)
    for group in s['opponent_groups'][:9]:
        r=group['results'];a=group['accuracy']
        e.add_field(name=f"{group['time_class'].title()} {group['time_control']} · opponent {group['opponent_elo_min']}–{group['opponent_elo_max']}",
            value=f"n={group['games']} · {'limited sample' if group['limited_sample'] else 'descriptive group'}\nPoints expected / actual: {fmt(r['expected_points'])} / {fmt(r['actual_points'])} · winrate {fmt(r['win_fraction'],True)}\nPublished Accuracy median: {fmt(a['observed_median'])} · cross-player norm unavailable.",inline=False)
    selected=d.get('selected') or {}
    if selected.get('reference_mode')=='personal_earlier' and (selected.get('accuracy') or {}).get('matched_games'):
        e.add_field(name='Selected period · earlier personal Accuracy',
                    value=selected_accuracy_reference(selected),inline=False)
    else:e.add_field(name='Accuracy reference',value='Not enough earlier, same-control and rating-matched official scores. Reference unknown; missing does not mean LOW.',inline=False)
    if len(s['opponent_groups'])>9:e.add_field(name='Display limit',value='Additional opponent groups omitted from this card.',inline=False)
    e.add_field(name='Automated screening only',value=DISCLAIMER,inline=False)
    shift=d.get('opening_shift')
    if shift:
        counts=shift['counts']
        e.add_field(name='Temporary opening switch · context',value=(
            f"{shift['time_class'].title()} {shift['time_control']} · {shift['color']} {shift['opponent_opening']}\\n"
            f"Before: {shift['before']} ({counts['before']} games) → "
            f"During: {shift['during']} ({counts['during']} games) → "
            f"After: {shift['after']} ({counts['after']} games). "
            "Context only; not proof of cheating."),inline=False)
    e.set_footer(text=result.version)
    return e


def chart_pngs(result, progress=None):
    from fairplay_charts import class_svg
    from fairplay_method3_frequency_charts import accuracy_frequency_svg
    from fairplay_method3_rolling_chart import rolling_history_svg
    from fairplay_method3 import observations
    import cairosvg
    live_games=result.games
    grouped=observations(live_games)
    files=[]
    zoom=selected_window_svg(result)
    total=sum(bool(rows) for rows in grouped.values())+(2 if live_games else 0)+bool(zoom)
    def add_chart(name, data):
        files.append((name,data))
        if progress:progress(len(files),total)
    for kind,rows in grouped.items():
        if not rows:continue
        svg=class_svg(kind,rows).replace('blue: SharkBot estimate · orange dashed: official Chess.com',
            'orange dashed: official Chess.com · no engine estimate')
        svg=mark_selected(svg,result,kind,rows,(150,470,790))
        add_chart(f'fairplay_{kind}_charts.png',cairosvg.svg2png(bytestring=svg.encode()))
    if live_games:
        add_chart('fairplay_method3_accuracy_frequency.png',
                  cairosvg.svg2png(bytestring=accuracy_frequency_svg(result).encode()))
        add_chart('fairplay_method3_rolling_history.png',cairosvg.svg2png(bytestring=rolling_history_svg(result).encode()))
    if zoom:
        add_chart('fairplay_method3_selected_period.png',cairosvg.svg2png(bytestring=zoom.encode()))
    return files


def period_dates(selected):
    from datetime import datetime,timezone
    a,b=selected.get('first_ended'),selected.get('last_ended')
    if a is None or b is None:return 'Dates unavailable'
    return datetime.fromtimestamp(a,timezone.utc).strftime('%d %b %Y')+' → '+datetime.fromtimestamp(b,timezone.utc).strftime('%d %b %Y')


def selected_accuracy_reference(selected):
    """Describe only the Accuracy reference belonging to this exact candidate."""
    accuracy=selected.get('accuracy') or {}
    matched=accuracy.get('matched_games',0)
    if selected.get('reference_mode')!='personal_earlier' or not matched:
        return 'Not enough earlier, same-control and rating-matched official scores for this selected period. Reference unknown.'
    return (f"{matched}/{selected['games']} selected games matched\n"
            f"Observed / earlier reference median: {fmt(accuracy.get('matched_observed_median'))} / "
            f"{fmt(accuracy.get('matched_reference_median'))}\n"
            'Partial matching is not a complete-period norm; earlier games are not labelled honest.')


def selected_window_svg(result):
    """Keep a short candidate readable within a history of up to 5000 games."""
    from fairplay_charts import plot, start, text
    from fairplay_method3 import observations
    from fairplay_outcome_study import expected_points
    diagnostic=result.diagnostics['method3']
    selected=diagnostic.get('selected')
    if not selected or not result.games:return None
    kind=selected['time_class']
    games=sorted((g for g in result.games if g.time_class==kind),
                 key=lambda g:(g.ended,g.identity))
    first,last=selected['first_game']-1,selected['last_game']
    if not 0<=first<last<=len(games):return None
    start_index=max(0,first-100)
    end_index=min(len(games),last+100)
    window=games[start_index:end_index]
    rows=observations(window)[kind]
    tier=diagnostic.get('selected_period_priority',result.priority)
    frequency_only=diagnostic.get('priority_basis')=='accuracy_frequency'
    label='Context only · LOW results window' if frequency_only else f'{tier} results window'
    parts=start(1440,1480)
    points=selected.get('results') or {}
    parts.extend((
        text(45,42,f'Selected period · {kind.title()} · {label}',28),
        text(45,76,f"History games #{first+1}–#{last} · {period_dates(selected)} · up to 100 surrounding games on each side",18),
        text(45,106,f"Selected window: expected {fmt(points.get('expected_points'))} / scored {fmt(points.get('actual_points'))} points · valid results {points.get('games','Unknown')}/{selected['games']}",18),
        text(45,134,'Overall MODERATE comes from a separate Accuracy-frequency change; this results window did not qualify.'
             if frequency_only else 'Shading identifies the selected window. Raw observations and missing-data gaps are preserved; not proof of cheating.',15,'#526477')))
    positions=(180,500,820,1140)
    plot(parts,rows,positions[0],'Official Chess.com Accuracy by game',
         [r['official_accuracy'] for r in rows],'%','#e87519',first_game=start_index+1)
    selected_rows=rows[first-start_index:last-start_index]
    published=sum(r['official_accuracy'] is not None for r in selected_rows)
    accuracy=selected.get('accuracy') or {}
    matched=accuracy.get('matched_games',0) if selected.get('reference_mode')=='personal_earlier' else 0
    reference=(f"matched earlier {matched}/{selected['games']} · median observed / reference "
               f"{fmt(accuracy.get('matched_observed_median'))} / {fmt(accuracy.get('matched_reference_median'))}"
               if matched else 'earlier matched reference unknown')
    parts.append(text(1375,positions[0]+23,
                      f"Selected published scores {published}/{selected['games']} · {reference}",
                      13,'#526477',anchor='end'))
    plot(parts,rows,positions[1],'Rating by game · blue player / orange opponent',
         [g.rating for g in window],'','#2563eb',second=[g.opponent_rating for g in window],first_game=start_index+1)
    expected=[expected_points(g.rating,g.opponent_rating) for g in window]
    plot(parts,rows,positions[2],'Points per game · blue Elo expectation / orange observed',
         [p*100 if p is not None else None for p in expected],'%','#2563eb',
         second=[g.score*100 if g.score in (0,.5,1) else None for g in window],first_game=start_index+1)
    plot(parts,rows,positions[3],'Median think time by game · valid PGN clocks only',
         [r['median_think_seconds'] for r in rows],'s','#7c3aed',first_game=start_index+1)
    colors={'MODERATE':('#eab308','#854d0e'),'HIGH':('#f97316','#9a3412'),
            'VERY HIGH':('#dc2626','#991b1b')}
    fill,stroke=colors.get(tier,('#94a3b8','#475569'))
    left=115+1260*(first-start_index)/max(1,len(window)-1)
    right=115+1260*(last-1-start_index)/max(1,len(window)-1)
    for y in positions:
        parts.append(f'<rect x="{left:.1f}" y="{y+30}" width="{max(3,right-left):.1f}" height="200" '
                     f'fill="{fill}" fill-opacity=".12" stroke="{stroke}" stroke-width="1.5"/>')
    parts.append(text(45,1452,'Axes zoom to observed data. Timing is descriptive and does not change priority. Missing values remain gaps.',15,'#526477'))
    parts.append('</svg>')
    return ''.join(parts)



def selected_period_embed(result):
    """Small, actionable description of the exact highlighted game window."""
    from datetime import datetime, timezone
    from fairplay_ui import priority_color
    diagnostic=result.diagnostics.get('method3') or {}
    selected=diagnostic.get('selected')
    e=discord.Embed(title='Selected Period — '+result.username,
                    color=priority_color(result.priority))
    if not selected:
        e.description='No period selected.'
        return e
    kind=selected['time_class']
    games=sorted((g for g in result.games if g.time_class==kind),
                 key=lambda g:(g.ended,g.identity))
    first,last=selected['first_game'],selected['last_game']
    points=selected.get('results') or {}
    selected_tier=diagnostic.get('selected_period_priority',result.priority)
    frequency_only=diagnostic.get('priority_basis')=='accuracy_frequency'
    explanation=(
        "This results window did not qualify on its own. Overall MODERATE "
        "is based on a separate historical Accuracy-frequency signal."
        if frequency_only else
        "Chart shading marks the selected results period, not proof of cheating."
    )
    e.description=(
        f"**{kind.title()} · {period_dates(selected)}**\n"
        f"**{selected['games']} games · {selected_tier} results window**\n"
        f"Expected: **{fmt(points.get('expected_points'))} points** · "
        f"Scored: **{fmt(points.get('actual_points'))} points**\n\n"
        + explanation
    )
    if 'games' in points:
        e.description+=f"\nValid result/rating pairs: **{points['games']}/{selected['games']}**."
    if selected.get('accuracy') is not None:
        e.add_field(name='Selected period · earlier personal Accuracy',
                    value=selected_accuracy_reference(selected),inline=False)
    additional=(result.diagnostics.get('method3') or {}).get('period_context') or {}
    classes=additional.get('by_class',{})
    other=[]
    for other_kind in ('rapid','blitz','bullet'):
        if other_kind==kind or other_kind not in classes:
            continue
        row=classes[other_kind]
        n=row['games']
        if n==0:
            other.append(f"**{other_kind.title()}:** 0 games · no comparison")
        elif n<10:
            other.append(f"**{other_kind.title()}:** {n} games · too few for a reliable pattern")
        else:
            other.append(f"**{other_kind.title()}:** {n} games · "
                         f"expected {fmt(row['expected_points'])}, "
                         f"scored {fmt(row['scored_points'])} · "
                         f"Accuracy {row['accuracy_games']}/{n}")
    if other:
        e.add_field(name='Other time controls · same dates',
                    value='\n'.join(other)+'\nOnly supporting context; not a second cheating flag.',
                    inline=False)
    timing=additional.get('timing') or {}
    if timing:
        def timing_part(key,value):
            row=timing[key]
            total=row['games']
            seconds=row['median_think_seconds']
            return (f"{value}: **{seconds:.1f}s** ({row['clock_games']}/{total} games)"
                    if seconds is not None else f"{value}: **Unknown** (0/{total} games)")
        parts=[timing_part('before50','Before'),
               timing_part('during','Selected'),
               timing_part('after50','After')]
        e.add_field(name=f"Thinking time · {kind.title()} {timing['time_control']}",
                    value='\n'.join(parts)+'\nSame time control · up to 50 games before/after.',
                    inline=False)
        def accuracy_part(key,name):
            row=timing[key]
            n=row['games']
            value=row['median_accuracy']
            return (f"{name}: **{value:.1f}** ({row['accuracy_games']}/{n})"
                    if value is not None else f"{name}: **Unknown** (0/{n})")
        e.add_field(name='Chess.com Accuracy · same time control',
                    value='\n'.join(accuracy_part(k,v) for k,v in
                          (('before50','Before'),('during','Selected'),('after50','After'))),
                    inline=False)
    for label,index in (('First game',first-1),('Last game',last-1)):
        if 0<=index<len(games):
            game=games[index]
            opponent=getattr(game,'opponent_username',None) or 'Unknown'
            opponent=discord.utils.escape_markdown(opponent)
            date=datetime.fromtimestamp(game.ended,timezone.utc).strftime('%d %b %Y')
            url=getattr(game,'url',None)
            link=f'[Open game]({url})' if isinstance(url,str) and url.startswith('https://www.chess.com/game/') else 'Game link unavailable'
            e.add_field(name=f'{label} · #{index+1}',value=(
                f"Opponent: **{opponent}**\n"
                f"Date: **{date}** (UTC)\n"
                f"Result: **{game.result}**\n"
                f"{link}"),inline=True)
    return e


def mark_selected(svg,result,kind,rows,positions):
    """Number priority windows and show their calendar dates in other controls."""
    from bisect import bisect_left,bisect_right
    from fairplay_charts import text
    palette={'MODERATE':('#eab308','#854d0e'),
             'HIGH':('#f97316','#9a3412'),
             'VERY HIGH':('#dc2626','#991b1b')}
    periods=result.diagnostics['method3'].get('marked_periods',[])
    if not rows or not periods or not getattr(result,'games',None):return svg
    # Observations and charts are chronological, independently per time class.
    games=sorted((g for g in result.games if g.time_class==kind),
                 key=lambda g:(g.ended,getattr(g,'identity','')))
    if not games:return svg
    times=[g.ended for g in games]
    marks=[]
    # Stable numbering by start date, shared across every chart.
    numbered=sorted((p for p in periods if p.get('priority') in palette),
                    key=lambda p:(p['first_ended'],p['last_ended'],p['time_class']))
    for number,period in enumerate(numbered,1):
        first=bisect_left(times,period['first_ended'])
        last=bisect_right(times,period['last_ended'])-1
        if first>last or first>=len(rows) or last<0:continue
        first=max(0,first);last=min(len(rows)-1,last)
        left=115+1260*first/max(1,len(rows)-1)
        right=115+1260*last/max(1,len(rows)-1)
        if right-left<3:
            left=max(115,left-2);right=min(1375,right+2)
        own=period['time_class']==kind
        fill,stroke=palette[period['priority']] if own else ('#a78bfa','#7c3aed')
        for y in positions:
            marks.append(f'<rect x="{left:.1f}" y="{y+30}" width="{right-left:.1f}" '
                         f'height="200" fill="{fill}" fill-opacity=".16" '
                         f'stroke="{stroke}" stroke-width="1.5" stroke-opacity=".8"/>')
            # Number beneath the panel, outside the plotted 200px area.
            marks.append(text((left+right)/2,y+247,str(number),15,stroke,anchor='middle'))
    if marks:
        for y in positions:
            marks.append(text(1375,y+23,
                'Priority color = detected control · purple = same dates elsewhere · numbers link periods · not proof',
                12,'#526477',anchor='end'))
    return svg.replace('</svg>',''.join(marks)+'</svg>')

def mark_recent(svg,rows,positions):
    """Mark the priority tail; keep original chronological game coordinates."""
    from fairplay_charts import text
    index=next((i for i,row in enumerate(rows) if row.get('priority_sample')),None)
    if index is None or index==0:
        label='History only — no current priority games' if index is None else 'Current priority sample'
        return svg.replace('</svg>',''.join(text(1375,y+23,label,14,'#9a3412',anchor='end') for y in positions)+'</svg>')
    x=115+1260*index/max(1,len(rows)-1)
    marks=[]
    for y in positions:
        marks.append(f'<line x1="{x:.1f}" y1="{y+30}" x2="{x:.1f}" y2="{y+230}" stroke="#c2410c" stroke-dasharray="6 5"/>')
        marks.append(text(min(x,1200),y+23,'Recent priority sample →',14,'#9a3412',anchor='end'))
    return svg.replace('</svg>',''.join(marks)+'</svg>')


def history_svg(result):
    """Short earlier-vs-recent chart with unambiguous sample sizes and dates."""
    from datetime import datetime, timezone
    from fairplay_charts import text
    rows=result.diagnostics['method3'].get('history_changes',[])[:12]
    height=max(270,150+len(rows)*155)
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="{height}" viewBox="0 0 1440 {height}"><rect width="100%" height="100%" fill="white"/>',
        text(35,42,'Earlier vs recent · same time control',27),
        text(35,76,'Up to 200 older matched games vs 20 newer games per control; not the last 220 games overall.',17)]
    for x,label in ((35,'Games / dates'),(430,'Points vs Elo'),(820,'Chess.com Accuracy'),(1160,'Move time')):
        parts.append(text(x,119,label,18))
    def span(value):
        if not value:return 'dates unavailable'
        a,b=value
        return (datetime.fromtimestamp(a,timezone.utc).strftime('%d %b %y')+' – '+
                datetime.fromtimestamp(b,timezone.utc).strftime('%d %b %y'))
    def points(value):
        return 'Unknown' if value is None else f'{value*100:+.1f}%'
    def secs(value):
        return 'Unknown' if value is None else f'{value:.1f}s'
    def control_label(value):
        try:
            base,inc=map(int,str(value).split('+'))
            return f'{base//60}+{inc}' if base%60==0 else f'{base}s+{inc}s'
        except (ValueError,TypeError):
            return str(value)
    for i,row in enumerate(rows):
        a,b=row['baseline'],row['recent']
        y=155+155*i
        if i:parts.append(f'<line x1="35" y1="{y-18}" x2="1405" y2="{y-18}" stroke="#d4d9df"/>')
        parts.extend((
            text(35,y,f"{row['time_class'].title()} · {control_label(row['time_control'])}",20),
            text(35,y+28,f"Earlier {a['games']}: {span(row.get('earlier_range'))}",14,'#526477'),
            text(35,y+53,f"Recent {b['games']}: {span(row.get('recent_range'))}",14,'#526477'),
            text(430,y+12,f"{points(a['score_excess'])} → {points(b['score_excess'])}",22),
            text(430,y+43,'Above/below expected score' if row['results_comparable'] else 'Limited comparison',14,'#526477'),
            text(820,y+12,f"{fmt(a['accuracy_median'])} → {fmt(b['accuracy_median'])}",22),
            text(820,y+43,f"Coverage: {a['accuracy_games']}/{a['games']} → {b['accuracy_games']}/{b['games']}",14,'#526477'),
            text(1160,y+12,f"{secs(a['think_median'])} → {secs(b['think_median'])}",22),
            text(1160,y+43,'Median time per move',14,'#526477')))
    if not rows:parts.append(text(35,182,'Not enough earlier and recent games to compare.',20))
    parts.append('</svg>')
    return ''.join(parts)
