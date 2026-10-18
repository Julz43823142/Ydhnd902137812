"""Discord presentation for results-only Method 3; no engine claims."""
import discord
from fairplay_config import DISCLAIMER


def fmt(value, percent=False):
    return 'Unknown' if value is None else f'{value*100:.1f}%' if percent else f'{value:.1f}'


def result_embed(result):
    from fairplay_ui import priority_color
    d=result.diagnostics['method3'];s=d['subject'];r=s['overall_results']
    icon={'LOW':'🟢','MODERATE':'🟡','HIGH':'🟠','INSUFFICIENT DATA':'⚪'}.get(result.priority,'⚪')
    e=discord.Embed(title='🛡️ Fair Play Review — '+result.username,color=priority_color(result.priority),
        description=f'{icon} **{result.priority} review priority**\n**Method 3 · experimental results-only screening.**\nNot an engine-confirmed gameplay finding.')
    e.add_field(name='Why this result?',value=result.reasons[0],inline=False)
    e.add_field(name='Sample / coverage',value=f'{len(result.games)} rated live games · coverage confidence: {result.confidence}\nStockfish: not run · Maia: not run · clocks: descriptive only.',inline=False)
    e.add_field(name='Expected versus observed points',value=f"Expected: **{fmt(r['expected_points'])}** · observed: **{fmt(r['actual_points'])}**\nActual winrate: **{fmt(r['win_fraction'],True)}**. Elo predicts points including draws, not win probability.",inline=False)
    selected=d.get('selected')
    if selected:
        q=selected['results']
        e.add_field(name='Selected chronological period',value=f"{selected['time_class'].title()} · games {selected['first_game']}–{selected['last_game']}\nExpected / observed points: {fmt(q['expected_points'])} / {fmt(q['actual_points'])}\nScore excess: {fmt(q['score_excess'],True)} · descriptive residual: {fmt(q['standardized_residual'])}",inline=False)
    e.add_field(name='Accuracy',value='Official Chess.com scores only, where published. Earlier matched personal references are descriptive; no universal Elo→Accuracy norm and no invented estimates. Accuracy alone can support MODERATE, never HIGH.',inline=False)
    e.add_field(name='Limits',value=('Collection incomplete. ' if result.partial else '')+d['limits'],inline=False)
    e.add_field(name='Automated screening only',value=DISCLAIMER,inline=False)
    e.set_footer(text=f'Version: {result.version} · Method 3 · uncalibrated heuristic, not probability')
    return e


def detail_embed(result,mode):
    from fairplay_ui import priority_color
    d=result.diagnostics['method3'];s=d['subject']
    e=discord.Embed(title=f'{mode} — {result.username}',description='Method 3: results-only experimental triage. No Stockfish/Maia screening.',color=priority_color(result.priority))
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
    files.append(('fairplay_method3_expected_points.png',cairosvg.svg2png(bytestring=''.join(parts).encode())))
    return files
