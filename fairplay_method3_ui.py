"""Discord presentation for results-only Method 3; no engine claims."""
import discord
from fairplay_config import DISCLAIMER


def fmt(value, percent=False):
    return 'Unknown' if value is None else f'{value*100:.1f}%' if percent else f'{value:.1f}'


def result_embed(result):
    from fairplay_ui import priority_color
    d=result.diagnostics['method3'];s=d['subject'];r=(d.get('selected') or {}).get('results',s['overall_results'])
    icon={'LOW':'🟢','MODERATE':'🟡','HIGH':'🟠','INSUFFICIENT DATA':'⚪'}.get(result.priority,'⚪')
    e=discord.Embed(title='🛡️ Fair Play Review — '+result.username,color=priority_color(result.priority),
        description=f'{icon} **{result.priority} review priority**\n**Method 3 · experimental results-only screening.**')
    explanations={
        'HIGH':'A recent stretch scored well above the results expected from the ratings. Check those games.',
        'MODERATE':'Published Accuracy was higher than in comparable earlier games. Take a closer look.',
        'LOW':'No strong results-based review flag was found. This does not prove fair play.',
        'INSUFFICIENT DATA':'There are not enough usable games for a reliable comparison.'}
    e.add_field(name='Why this result?',value=explanations.get(result.priority,'See the detail buttons.'),inline=False)
    accuracy_games=sum(g.accuracy is not None for g in result.games)
    e.add_field(name='Sample / coverage',value=f'{len(result.games)} rated history games · latest {d.get("priority_games",len(result.games))} used for priority · results confidence: {result.confidence}'+(' · incomplete collection' if result.partial else '')+f'\nPublished Accuracy: {accuracy_games}/{len(result.games)} games. Missing scores were not engine-reviewed.',inline=False)
    e.add_field(name='Selected recent period',value=f"Expected points: **{fmt(r['expected_points'])}** · scored: **{fmt(r['actual_points'])}**\nWinrate: **{fmt(r['win_fraction'],True)}** · a draw counts as half a point.",inline=False)
    e.add_field(name='Limits',value='Stockfish: not run · Maia: not run. Results-only screening can flag honest improving players. Technical details are available below.',inline=False)
    if any(row['results_increase'] for row in d.get('history_changes',[])):
        e.add_field(name='History comparison',value='Recent results rose against matched earlier games. This is supporting context, not a separate cheating signal.',inline=False)
    e.add_field(name='Important',value='Not proof of cheating. Human review is required before any action.',inline=False)
    e.set_footer(text=f'Version: {result.version} · Method 3 · uncalibrated heuristic, not probability')
    return e


def detail_embed(result,mode):
    from fairplay_ui import priority_color
    d=result.diagnostics['method3'];s=d['subject']
    e=discord.Embed(title=f'{mode} — {result.username}',description='Method 3: results-only experimental triage. No Stockfish/Maia screening.',color=priority_color(result.priority))
    if mode in ('Performance', 'Report Details'):
        e.add_field(name='Explanation',value='\n'.join(result.reasons)[:1024],inline=False)
        e.add_field(name='Limits',value=d['limits'][:1024],inline=False)
        selected=d.get('selected')
        if selected:
            q=selected['results']
            e.add_field(name='Selected chronological period',value=(
                f"{selected['time_class'].title()} · games {selected['first_game']}–{selected['last_game']}\n"
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
        e.add_field(name='History scope',value=f"{len(result.games)} collected; priority searches only the latest {d.get('priority_games',len(result.games))}. Older games can supply strictly earlier matched references and charts; no dilution into an account-wide average. Changes are descriptive, not an independent HIGH vote.",inline=False)
    if mode in ('Engine Analysis','Human Moves'):
        e.add_field(name='Not performed',value='This method does not run Stockfish or Maia. No engine ranks, human-move likelihoods or deep confirmation are claimed.',inline=False)
    elif mode=='Review Gates':
        e.add_field(name='Configured boundaries',value='HIGH: a ≥10-game chronological period with ≥80% valid results, Elo-points residual ≥2.4 and score excess ≥20 percentage points. MODERATE: official Accuracy exceeds an adequately covered earlier matched personal reference by ≥8 points, with ≥60% above its upper reference. VERY HIGH is unavailable. These thresholds were chosen on development data, not calibrated on independent holdouts.',inline=False)
    elif mode=='Timing':
        e.add_field(name='Descriptive clocks only',value='PGN clocks supply the think-time charts. Timing does not change this method’s priority.',inline=False)
    for group in s['opponent_groups'][:12]:
        r=group['results'];a=group['accuracy']
        e.add_field(name=f"{group['time_class'].title()} {group['time_control']} · opponent {group['opponent_elo_min']}–{group['opponent_elo_max']}",
            value=f"n={group['games']} · {'limited sample' if group['limited_sample'] else 'descriptive group'}\nPoints expected / actual: {fmt(r['expected_points'])} / {fmt(r['actual_points'])} · winrate {fmt(r['win_fraction'],True)}\nPublished Accuracy median: {fmt(a['observed_median'])} · cross-player norm unavailable.",inline=False)
    personal=[p for p in s['periods'] if p['reference_mode']=='personal_earlier' and p['accuracy']['matched_games']]
    if personal:
        p=max(personal,key=lambda p:p['last_game']);a=p['accuracy']
        e.add_field(name='Earlier personal Accuracy comparison',value=f"{p['time_class'].title()} · {a['matched_games']}/{p['games']} matched subject games\nObserved / reference median: {fmt(a['matched_observed_median'])} / {fmt(a['matched_reference_median'])}\nPartial matching is not a complete-period norm; reference games are earlier, not labelled honest.",inline=False)
    else:e.add_field(name='Accuracy reference',value='Not enough earlier, same-control and rating-matched official scores. Reference unknown; missing does not mean LOW.',inline=False)
    if len(s['opponent_groups'])>12:e.add_field(name='Display limit',value='Additional opponent groups omitted from this card.',inline=False)
    e.add_field(name='Automated screening only',value=DISCLAIMER,inline=False)
    e.set_footer(text=result.version)
    return e


def chart_pngs(result):
    from fairplay_charts import class_svg, comparison_svg, plot, text
    from fairplay_method3 import observations
    import cairosvg
    from fairplay_outcome_study import expected_points
    grouped=observations(result.games)
    files=[]
    for kind,rows in grouped.items():
        if not rows:continue
        svg=class_svg(kind,rows).replace('blue: SharkBot estimate · orange dashed: official Chess.com',
            'orange dashed: official Chess.com · no engine estimate')
        svg=mark_recent(svg,rows,(150,470,790))
        files.append((f'fairplay_{kind}_charts.png',cairosvg.svg2png(bytestring=svg.encode())))
    files.append(('fairplay_opponent_elo_comparison.png',cairosvg.svg2png(bytestring=comparison_svg(grouped).encode())))
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1120" viewBox="0 0 1440 1120"><rect width="1440" height="1120" fill="white"/>',text(45,45,'Method 3 — rolling 10-game expected / observed points',26)]
    for index,kind in enumerate(('rapid','blitz','bullet')):
        games=[g for g in result.games if g.time_class==kind]
        predicted=[];actual=[]
        for i in range(len(games)):
            window=games[max(0,i-9):i+1]
            valid=[(g,expected_points(g.rating,g.opponent_rating)) for g in window]
            valid=[(g,p) for g,p in valid if p is not None and g.score in (0,.5,1)]
            predicted.append(sum(p for g,p in valid)/len(valid)*100 if valid else None)
            actual.append(sum(g.score for g,p in valid)/len(valid)*100 if valid else None)
        if games:plot(parts,games,105+index*325,kind.title()+' · blue expected / orange observed points',predicted,'%', '#2563eb',second=actual)
    parts.append('</svg>')
    svg=''.join(parts)
    for index,kind in enumerate(('rapid','blitz','bullet')):
        if grouped[kind]:svg=mark_recent(svg,grouped[kind],(105+index*325,))
    files.append(('fairplay_method3_expected_points.png',cairosvg.svg2png(bytestring=svg.encode())))
    files.append(('fairplay_method3_history_change.png',cairosvg.svg2png(bytestring=history_svg(result).encode())))
    return files


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
    from fairplay_charts import text
    rows=result.diagnostics['method3'].get('history_changes',[])[:12]
    height=max(280,175+len(rows)*100)
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="{height}" viewBox="0 0 1440 {height}"><rect width="100%" height="100%" fill="white"/>',
        text(35,40,'Earlier → recent behaviour · matched opponents, same time control',26),
        text(35,75,'Descriptive context only. Missing scores/clocks stay unknown; improvement does not establish misconduct.',18)]
    for x,label in ((35,'Control · earlier/recent games'),(390,'Points above Elo expectation'),(810,'Published Accuracy'),(1130,'Median game think time')):
        parts.append(text(x,125,label,17))
    for i,row in enumerate(rows):
        a,b=row['baseline'],row['recent'];y=175+100*i
        parts.extend((text(35,y,f"{row['time_class'].title()} {row['time_control']} · {a['games']}/{b['games']}",19),
            text(390,y,f"{fmt(a['score_excess'],True)} → {fmt(b['score_excess'],True)}",20),
            text(810,y,f"{fmt(a['accuracy_median'])} → {fmt(b['accuracy_median'])}",20),
            text(1130,y,f"{fmt(a['think_median'])} → {fmt(b['think_median'])} sec",20),
            text(35,y+30,'Comparable earlier reference' if row['results_comparable'] else 'Limited earlier reference',15,'#526477')))
    if not rows:parts.append(text(35,190,'Not enough same-control earlier/recent games for a comparison.',20))
    parts.append('</svg>')
    return ''.join(parts)
