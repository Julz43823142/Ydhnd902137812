"""Discord is the case history. Runtime review data stays in bounded memory.

One dedicated executor/queue keeps engine work off Discord's loop and outside
the normal chess engine locks. No Discord token, ledger, wallet or punishments.
"""
import asyncio
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord

from fairplay_analysis import ReviewResult, review, review_interest
from fairplay_config import CHANNEL_ID, DISCLAIMER, NAMESPACE, CONFIG
from fairplay_data import AccountNotFound, ReviewError, username
from fairplay_progress import estimate, bar, label

RESERVED = '🛡️ This channel is reserved for Fair Play reviews.'
PANEL_TITLE = '🛡️ Fair Play Task Force'
REPORT_PREFIX = '🛡️ Fair Play Review — '
IDLE_SECONDS = CONFIG.intro_panel_seconds
PANEL_MARKER = 'shark:fairplay:panel:v1'
_service = None


def public_text(value):
    return discord.utils.escape_markdown(str(value))


def panel_embed():
    embed = discord.Embed(title=PANEL_TITLE, color=0x427CBA,
        description='Want to review a suspicious Chess.com account?\n\n'
                    "Submit the Chess.com username. SharkBot collects up to 200 eligible rated standard games, fully engine-screens the latest 100, then deeply reviews selected games for human decision, engine and timing evidence.\n\n"
                    '**This is an automated screening tool — not proof of cheating.**')
    embed.set_footer(text=PANEL_MARKER)
    return embed


def progress_embed(target, stage, value=None):
    value=estimate(stage) if value is None else value
    embed = discord.Embed(title=REPORT_PREFIX+target, description=bar(value)+"\n\n"+label(stage), color=0x427CBA)
    embed.set_footer(text="Overall analysis progress · stage-weighted, not a time estimate")
    embed.add_field(name='⚠️ Automated screening only', value=DISCLAIMER, inline=False)
    return embed


def number(value, suffix=''):
    return 'Unavailable' if value is None else f'{value:.1f}{suffix}'


def percentage(value):
    return number(None if value is None else value*100,'%')


def result_embed(result: ReviewResult):
    icons = {'LOW':'🟢','MODERATE':'🟡','HIGH':'🟠','VERY HIGH':'🔴','INSUFFICIENT DATA':'⚪'}
    colors = {'LOW':0x2E9E65,'MODERATE':0xE9B44C,'HIGH':0xEA8537,'VERY HIGH':0xD94F55,'INSUFFICIENT DATA':0x788491}
    embed = discord.Embed(title=REPORT_PREFIX+result.username,color=colors[result.priority])
    embed.description=bar(100)+'\nReview complete.'
    embed.add_field(name='Fair Play Review Priority',value=f'{icons[result.priority]} **{result.priority}**')
    embed.add_field(name='Data Confidence',value=result.confidence)
    totals = result.totals
    classes = ' • '.join(f'{kind.title()}: {data["games"]}' for kind,data in result.classes.items())
    coverage = result.coverage
    sample = (f'Rated context games: **{coverage.get("collected",result.selected_games)}**\n'
              f'Full engine-reviewed: **{coverage.get("fast_scanned",totals["games"])}** · gameplay-scoring games: **{coverage.get("used",totals["games"])}**\n'
              f'Deep-reviewed: **{coverage.get("deep_reviewed",result.deep_coverage["games"])}** · older context-only: **{coverage.get("context_only",max(0,result.selected_games-coverage.get("fast_scanned",totals["games"])))}**\n'
              f'Meaningful decisions: **{totals["decisions"]:,}**\n{classes}\n'
              f'Account age: {result.context["age_days"] if result.context["age_days"] is not None else "unavailable"} days')
    funnel=result.diagnostics.get('gameplay',{}).get('funnel',{})
    if funnel:sample+=f'\nCompetitive decisions: {funnel["competitive"]} · high-difficulty: {funnel["high_difficulty"]}'
    if coverage.get('history_probed'):
        sample += f'\nRecent rated primary sample: {coverage["primary_fast_scanned"]}/{coverage["primary_collected"]} · historical discovery probes: {coverage.get("history_probed",0)}'
    sample += f'\nSkipped unrated games while collecting history: {result.skipped.get("unrated",0)} · unknown rated status: {result.skipped.get("rated_status_unknown",0)}'
    if coverage.get('history_probe_complete') is False:sample += '\nExtended-history discovery is incomplete; primary coverage is shown separately.'
    if result.skipped:sample += f'\nSkipped archive/game entries: {sum(result.skipped.values())}'
    if coverage.get('optional_context_partial'):sample += '\n⚠️ Older context is incomplete; primary engine coverage is complete.'
    elif result.partial:sample += '\n⚠️ Partial scan / limited archive coverage. Missing data is not suspicious.'
    dates=[g.ended for g in (result.timeline or result.games) if g.ended>0]
    if dates:sample += f'\nEngine-covered dates: <t:{min(dates)}:d> → <t:{max(dates)}:d>'
    embed.add_field(name='Sample',value=sample,inline=False)
    embed.add_field(name='Signals',value='\n'.join(f'**{key}:** {value}' for key,value in result.families.items()),inline=False)
    embed.add_field(name='Review notes',value='\n'.join('• '+value for value in result.reasons)[:1024],inline=False)
    embed.add_field(name='⚠️ Automated screening only',value=DISCLAIMER,inline=False)
    embed.set_footer(text=f'{result.version} · {result.engine} · heuristic thresholds, not probabilities · details expire after restart')
    return embed


def detail_embed(result, mode):
    embed = discord.Embed(title=f'{mode} — {result.username}',color=0x427CBA)
    if mode=='Review Gates':
        embed.description='Each HIGH route has its own requirements. Timing/results are optional for the absolute and acute gameplay routes. PASS is review evidence, not a misconduct verdict.'
        add_gate_fields(embed,result)
        embed.set_footer(text=DISCLAIMER)
        return embed
    if mode!='Highest-Signal Games':add_convergence_fields(embed,result)
    if mode=='Highest-Signal Games':
        joint=result.clusters.get('convergence',{})
        period_ids=set((joint.get('candidate') or {}).get('ids',[])) if joint.get('raised_priority') else set()
        if period_ids:embed.description='Games from the complete period supporting this review are shown first.'
        games = sorted(result.games,key=lambda g:(g.identity in period_ids,review_interest(g)),reverse=True)[:10]
        for index,game in enumerate(games,1):
            metrics = game.metrics
            rate = percentage(metrics['critical_top1'])
            text = (f'{game.time_class.title()} · {game.time_control} · {game.result} · <t:{game.ended}:d>\n'
                    f'Rating: {game.rating or "unavailable"} vs {game.opponent_rating or "unavailable"}\n'
                    f'Decisions: {metrics["decisions"]} · Top-1: {percentage(metrics["top1"])} · Unique hits: {metrics["unique_hits"]}/{metrics["unique"]}\n'
                    f'Critical top-1: {rate} / {metrics["critical"]} opportunities · '
                    f'Median CPL: {number(metrics["median_cpl"])}\n'
                    f'Timing: {"Elevated" if metrics["timing"].get("elevated") else "Not established"} · '
                    f'{"Deep reviewed" if game.deep else "Fast scan"}')
            if game.url:text += f'\n[Open Game]({game.url})'
            embed.add_field(name=f'Game {index}',value=text,inline=False)
    elif mode=='Clusters & History':
        embed.description = ('Legacy gameplay/timing periods remain exact-control. The new gameplay layer also compares real chronological Rapid/Blitz periods across controls. '
                             'Clock comparisons stay exact-control. Sessions use a 45-minute inactivity gap. '
                             'Overlapping windows do not multiply evidence. Only the full engine-reviewed sample supplies gameplay evidence; older games remain context.')
        strongest = result.clusters.get('strongest')
        if strongest:
            m = strongest['metrics']
            embed.add_field(name='Highest-signal chronological period',inline=False,
                value=f'<t:{strongest["start"]}:d> → <t:{strongest["end"]}:d> · {strongest["time_class"]} · {strongest["time_control"]}\n'
                      f'{m["games"]} games · {m["decisions"]} decisions · median CPL {number(m["median_cpl"])}\n'
                      f'Top-1 / top-3: {percentage(m["top1"])} / {percentage(m["top3"])}\n'
                      f'Critical: {percentage(m["critical_top1"])} / {m["critical"]} opportunities · unique hits {m["unique_hits"]}/{m["unique"]}\n'
                      f'Per-game strong evidence: {strongest["sustained_games"]}/{m["games"]} · pooled critical persistence: {strongest.get("pooled_critical_support",False)}\n'
                      f'Games with critical opportunities: {strongest.get("critical_opportunity_games",0)}/{m["games"]}\n'
                      f'Separate-period recurrence: {result.clusters.get("recurrence",False)}')
        else:
            embed.add_field(name='Highest-signal period',value='No sufficiently persistent same-control period.',inline=False)
            candidate=result.clusters.get('review_candidate')
            candidate_deep=result.clusters.get('candidate_deep',{})
            if candidate:
                embed.add_field(name='Exploratory candidate — persistence not established',inline=False,
                    value=f'{candidate["metrics"]["games"]} games · {candidate["time_class"]} · {candidate["time_control"]}\n'
                          f'Candidate engine evidence deep-confirmed: {candidate_deep.get("confirmed",False)}\n'
                          'Deep confirmation of selected moves does not establish a persistent period by itself.')
        deep = result.clusters.get('deep',{})
        embed.add_field(name='Cluster-specific deep confirmation',inline=False,
            value=f'Supported: {deep.get("confirmed",False)} · engine: {deep.get("engine",False)} · critical: {deep.get("critical",False)}\n'
                  f'{deep.get("games",0)} games · {deep.get("decisions",0)} decisions · {deep.get("critical_count",0)} critical opportunities\n'
                  f'Fast → deep evidence retention: {percentage(deep.get("stability"))}\n'
                  f'Baseline anomaly confirmed: {deep.get("anomaly_confirmed",False)} · representative baseline games: {deep.get("baseline_games",0)}\n'
                  f'Core family: {deep.get("core","unavailable")} · engine / critical retention: {percentage(deep.get("engine_retention"))} / {percentage(deep.get("critical_retention"))}')
        add_baseline_fields(embed,result)
        add_gate_fields(embed,result)
        if result.history:
            embed.add_field(name='Extended history',inline=False,
                value=f'{result.history.get("collected",0)} eligible rated games · {len(result.history.get("sessions",[]))} approximate sessions\n'
                      f'{result.coverage.get("history_probed",0)} discovery probes · {result.coverage.get("history_fast_scanned",0)} targeted full historical scans\n'
                      'A large lower-anomaly baseline does not dilute a localized high-signal period.')
    elif mode=='Timing':
        eligible = [g for g in (result.timeline or result.games) if g.metrics['timing']['count']>=15]
        regular = [g for g in eligible if g.metrics['timing'].get('elevated')]
        embed.description = (f'Games with ≥15 usable clock decisions: **{len(eligible)}**\n'
                             f'Repeated narrow-cadence games: **{len(regular)}**\n'
                             'Missing clocks, first moves and severe time trouble are excluded. '
                             'Premoves are counted separately; openings contribute only to the personal timing profile, never engine evidence. '
                             'Clock estimates use previous remaining time + increment − new remaining time. '
                             'Trivial decisions are timing-only structural proxies, never engine-match evidence. '
                             'Deliberate slow play, lag, rounding, increment, accessibility/input delay and habits are innocent alternatives. '
                             'Timing alone cannot produce HIGH or VERY HIGH priority.')
        coverage=result.diagnostics.get('timing_coverage',{})
        embed.add_field(name='Clock coverage',inline=False,
            value=f'Games with valid clocks: {coverage.get("games",0)} · post-opening moves: {coverage.get("usable_moves",0)}\n'
                  f'Linked engine decisions: {coverage.get("engine_linked_moves",0)} · excluded/unreliable clock estimates: {coverage.get("excluded_clocks",0)}\n'
                  f'Games below engine opportunity minimum retaining clocks: {coverage.get("below_engine_minimum_games",0)}\n'
                  'Simple and already-won positions retain timing evidence. Only rated games contribute to analytical comparisons.')
        for key,row in sorted(result.timing.get('delay_floors',{}).items(),key=lambda pair:(pair[1]['elevated'],pair[1]['games']),reverse=True)[:3]:
            cats=row['samples']
            embed.add_field(name=f'Cross-category delay · {key}',inline=False,
                value=f'{"Elevated absolute-delay pattern" if row.get("raw_elevated",row["elevated"]) else "Absolute-delay pattern not established" if row["sufficient"] else "Insufficient absolute-delay clocks"} · {row["games"]} games\n'
                      f'Trivial / ordinary / critical medians: {number(cats["trivial"]["median"])} / {number(cats["normal"]["median"])} / {number(cats["critical"]["median"])} sec\n'
                      f'Trivial near-instant fraction: {percentage(cats["trivial"]["near_instant"])}\n'
                      f'Trivial–critical distribution overlap: {percentage(row["overlap"]["trivial_critical"])}\n'
                      'Similar delayed timing across easy and hard moves is supporting evidence, not proof; input habits or lag can cause it.')
            local=row.get('normalized',{})
            if local.get('sufficient'):
                add_local_clock_field(embed,local,key)
        for key,row in sorted(result.timing.get('cadence_groups',{}).items(),key=lambda pair:pair[1]['games'],reverse=True)[:2]:
            if row['games']<2:continue
            embed.add_field(name=f'Repeated cadence · {key}',inline=False,
                value=f'{row["games"]} games · {row["moves"]} clocks · {number(row["modal_seconds"])} ± {number(row["band_halfwidth"])} sec\n'
                      f'Median per-game band share: {percentage(row["fraction"])} · recurring: {row["recurrent"]}\n'
                      'Band width scales with the observed delay; occasional long pauses do not erase a repeated pattern.')
        for row in sorted(result.history.get('timing_baselines',[]),key=lambda r:r['profile']['games'],reverse=True)[:2]:
            p=row['profile'];cats=p['categories']
            embed.add_field(name=f'Extended timing reference · {row["class"]} · {row["control"]}',inline=False,
                value=f'{p["games"]} historical games; includes unselected games, not a claim of fair play.\n'
                      f'Opening / trivial / ordinary medians: {number(cats.get("opening",{}).get("median"))} / {number(cats.get("trivial",{}).get("median"))} / {number(cats.get("ordinary",{}).get("median"))} sec\n'
                      f'Per-game delayed MAD: {number(p["comparison"].get("delayed_mad"))} · entropy: {number(p["comparison"].get("entropy"))}\n'
                      'Critical timing requires actual engine classification; unscanned historical decisions are not labelled critical.')
        personal = result.timing.get('personal',[])
        if not personal:
            embed.add_field(name='Personal Timing Behavior Shift',value='Insufficient Data — no comparable same-control timing groups.',inline=False)
        for row in sorted(personal,key=lambda r:(r['state'] not in ('Strong','Very Strong'),r['time_class'],r['time_control']))[:4]:
            b,h = row['baseline'],row['high_signal']
            lines = [f'Ranked-group comparison: **{row.get("ranked_state",row["state"])}** · lower-anomaly / high-signal games: {b["games"]} / {h["games"]}',
                     'Baseline → high-signal medians (seconds), with near-instant fraction:']
            for category,label in (('opening','Opening'),('middlegame','Middlegame'),('critical','Critical'),('trivial','Trivial')):
                a,c = b['categories'].get(category,{}),h['categories'].get(category,{})
                lines.append(f'{label}: {number(a.get("median"))} → {number(c.get("median"))} · {percentage(a.get("near_instant_fraction"))} → {percentage(c.get("near_instant_fraction"))}')
            bc,hc = b['comparison'],h['comparison']
            lines += [f'Delayed MAD: {number(bc["delayed_mad"])} → {number(hc["delayed_mad"])} · IQR: {number(bc["iqr"])} → {number(hc["iqr"])}',
                      f'CV: {number(bc["cv"])} → {number(hc["cv"])} · entropy: {number(bc["entropy"])} → {number(hc["entropy"])}',
                      f'Modal band: {number(bc["modal_seconds"])} → {number(hc["modal_seconds"])} sec · concentration: {percentage(bc["cluster_fraction"])} → {percentage(hc["cluster_fraction"])}',
                      f'Critical extra time: {number(b["critical_extra_seconds"])} → {number(h["critical_extra_seconds"])} sec',
                      f'Median CPL: {number(row["baseline_quality"]["cpl"])} → {number(row["high_signal_quality"]["cpl"])}']
            shift = row['chronological_shift']
            if shift:lines.append(f'Sustained chronological change: {shift["state"]} near <t:{shift["boundary"]}:d>.')
            if row['time_class']=='bullet':lines.append('Bullet timing has low reliability and reduced weight.')
            embed.add_field(name=f'Personal Timing Behavior Shift · {row["time_class"].title()} · {row["time_control"]}',value='\n'.join(lines)[:1024],inline=False)
        for kind,m in result.timing.get('trivial_delay',{}).items():
            if ' · ' not in kind and any(' · ' in k for k in result.timing.get('trivial_delay',{})):continue
            if not m['samples']['trivial']['count']:continue
            samples = m['samples'];overlap = m['overlap'];trivial = samples['trivial']
            embed.add_field(name=f'Trivial-Move Delay Anomaly · {kind.title()}',inline=False,
                value=f'Trivial / normal / critical samples: {trivial["count"]} / {samples["normal"]["count"]} / {samples["critical"]["count"]}\n'
                      f'Median seconds: {number(trivial["median"])} / {number(samples["normal"]["median"])} / {number(samples["critical"]["median"])}\n'
                      f'Overlap T/N · T/C · N/C: {percentage(overlap["trivial_normal"])} · {percentage(overlap["trivial_critical"])} · {percentage(overlap["normal_critical"])}\n'
                      f'Common band: {number(m["common_band_seconds"])} ± 1 sec\n'
                      f'Delayed trivial (≥2 sec): {trivial["delayed"]} · near-instant (≤0.5 sec): {trivial["near_instant"]}\n'
                      f'Same-cadence games: {m["same_cadence_games"]} · recurrent pattern: {m["recurrent"]}\n'
                      f'Coverage: {"sufficient" if m["sufficient"] else "limited; no anomaly assigned"} · {"low Bullet reliability" if kind.startswith("bullet") else "screening only"}')
        shifts=[g for g in eligible if g.metrics['timing'].get('regime_shift')]
        for game in shifts[:2]:
            segments=game.metrics['timing'].get('segments',{})
            b,h=segments.get('earlier',{}),segments.get('later',{})
            embed.add_field(name=f'Within-game transition · <t:{game.ended}:d>',inline=False,
                value=f'Earlier / later usable decisions: {b.get("decisions",0)} / {h.get("decisions",0)}\n'
                      f'Median CPL: {number(b.get("median_cpl"))} → {number(h.get("median_cpl"))} · top-1: {percentage(b.get("top1"))} → {percentage(h.get("top1"))}\n'
                      f'Cadence concentration: {percentage(b.get("cadence",{}).get("cluster_fraction"))} → {percentage(h.get("cadence",{}).get("cluster_fraction"))}\n'
                      'A supporting behavioral transition, not proof.')
        for game in sorted(eligible,key=lambda g:g.metrics['timing'].get('cluster_fraction',0),reverse=True)[:5]:
            m = game.metrics['timing']
            embed.add_field(name=f'{game.time_class.title()} · <t:{game.ended}:d>',
                value=f'Modal band: {m.get("modal_seconds",0)} ± {number(m.get("band_halfwidth"))} sec · {percentage(m.get("cluster_fraction"))}\n'
                      f'CV: {number(m.get("cv"))} · entropy: {number(m.get("entropy"))}\n'
                      f'Ordinary / critical median: {number(m.get("ordinary_median"))} / {number(m.get("critical_median"))} sec\n'
                      f'Gap–time correlation: {number(m.get("complexity_response"))} · within-game regime change: {bool(m.get("regime_shift"))}\n'
                      f'Critical-hit cadence: {percentage(m.get("critical_cadence",{}).get("cluster_fraction"))} · reliability: {m["reliability"]}',inline=False)
    elif mode=='Performance':
        embed.description = 'Sustained changes compare adjacent 5/8/10-game windows with the same base time and increment, using robust CPL and MAD. One exceptional game is insufficient. Engine-derived changes locate periods but do not count as independent corroboration.'
        extended=result.history.get('context',{}).get('classes',{})
        if extended:
            embed.add_field(name='Extended rating / result history',inline=False,
                value='\n'.join(f'{kind.title()}: {m["games"]} games · rating change {number(m["rating_gain"])} · expected / actual score {number(m["expected"])} / {number(m["actual"])}' for kind,m in extended.items() if m['games']))
        for kind,m in result.performance['classes'].items():
            c = result.context['classes'][kind]
            shift = m['shift']
            text = f'W/L/D: {m["wins"]}/{m["losses"]}/{m["draws"]}\nWin / loss / draw robust CPL: {number(m["win_cpl"])} / {number(m["loss_cpl"])} / {number(m["draw_cpl"])}\n'
            text += f'Expected / actual points: {c["expected"]:.1f} / {c["actual"]:.1f} in {c["rated_games"]} explicitly rated games\nRating change: {number(c["rating_gain"])} · volatility: {number(c["rating_volatility"])}\nLongest win streak: {c["longest_win_streak"]}'
            if shift:text += f'\nStrongest window change: {number(shift["before_cpl"])} → {number(shift["after_cpl"])} CPL · sustained criteria met: {shift["elevated"]}'
            if len(m['rolling_cpl'])>=2:text += f'\nRolling 10-game CPL: {number(m["rolling_cpl"][0])} → {number(m["rolling_cpl"][-1])}'
            embed.add_field(name=kind.title(),value=text,inline=False)
    else:
        a = result.totals
        embed.description = (f'Meaningful decisions: **{a["decisions"]:,}**\nMedian / 90th percentile CPL: {number(a["median_cpl"])} / {number(a["p90_cpl"])}\n'
                             f'Top-1 / top-3: {percentage(a["top1"])} / {percentage(a["top3"])}\n'
                             f'Near-best (≤15 cp): {percentage(a.get("near_best"))} · contradictory searches excluded: {a.get("search_inconsistent",0)}\n'
                             f'Critical opportunities: {a["critical"]} · top-1 / top-3: {percentage(a["critical_top1"])} / {percentage(a["critical_top3"])}\n'
                             f'Critical median CPL: {number(a["critical_cpl"])} · unique-best hits: {a["unique_hits"]}/{a["unique"]}\n'
                             f'Mistake / blunder-like losses: {a["mistakes"]}/{a["blunders"]} · critical mistakes: {a["critical_mistakes"]}\n'
                             f'Deep confirmation: {result.deep_confirmed} · {result.deep_coverage["games"]} games / {result.deep_coverage["decisions"]} decisions\n\n'
                             'Known opening-reference moves and genuinely forced/trivial decisions are de-weighted; early off-book decisions can contribute. Difficult check responses and nontrivial recaptures remain analyzable. Strongly won/lost positions are de-weighted. '
                             'Critical positions require several choices, a best–second gap and candidate spread; quiet unique choices receive the strongest evidence. '
                             'These heuristic measures have innocent explanations and do not establish misconduct.')
        add_gate_fields(embed,result)
        cluster = result.clusters.get('strongest')
        if cluster:
            m = cluster['metrics']
            embed.add_field(name='Highest-signal period (fast discovery)',inline=False,
                value=f'{m["games"]} games · top-1 {percentage(m["top1"])} · median CPL {number(m["median_cpl"])}\n'
                      f'Critical top-1 {percentage(m["critical_top1"])} / {m["critical"]} positions · quiet / tactical {m["quiet_critical"]}/{m["tactical_critical"]}')
        deep = result.clusters.get('deep',{}).get('metrics')
        if deep:
            embed.add_field(name='Same-period deep results',inline=False,
                value=f'{deep["games"]} games · top-1 {percentage(deep["top1"])} · median CPL {number(deep["median_cpl"])}\n'
                      f'Critical top-1 {percentage(deep["critical_top1"])} / {deep["critical"]} positions')
        for kind,a in result.classes.items():
            embed.add_field(name=kind.title(),value=f'{a["games"]} games · {a["decisions"]} decisions · median CPL {number(a["median_cpl"])} · top-1 {percentage(a["top1"])} · critical top-1 {percentage(a["critical_top1"])}',inline=False)
    embed.set_footer(text=DISCLAIMER)
    # Discord enforces 6000 characters across all embed components.
    trimmed = False
    while (len(embed)>5800 or len(embed.fields)>25) and embed.fields:
        embed.remove_field(len(embed.fields)-1);trimmed=True
    if trimmed:embed.set_footer(text=DISCLAIMER+' Additional detail rows omitted to fit Discord’s card limit.')
    return embed


def add_convergence_fields(embed,result):
    joint=result.clusters.get('convergence',{})
    candidate=joint.get('candidate')
    if not candidate:return
    proof=candidate['convergence'];deep=joint.get('confirmation',{})
    m=candidate['metrics'];clock=proof['clocks']['profile']['samples']
    outcomes=proof['results']
    embed.add_field(name='HIGH trigger — convergent period' if joint.get('raised_priority') else 'Convergent period review',inline=False,
        value=f'<t:{candidate["start"]}:d> → <t:{candidate["end"]}:d> · {candidate["time_class"]} · {candidate["time_control"]}\n'
              f'Complete period: {m["games"]} games · {m["decisions"]} meaningful decisions\n'
              f'Critical top-1: {percentage(m["critical_top1"])} / {m["critical"]} opportunities\n'
              f'Engine / clock replication in both halves: {all(r["supported"] for r in proof["engine_halves"])} / {all(r["supported"] for r in proof["clock_halves"])}\n'
              f'Games containing all three clock categories: {proof["clocks"]["shared_games"]}\n'
              f'Confirmed clock comparison: {proof.get("timing_method","absolute")}\n'
              f'Median easy / ordinary / critical delay: {number(clock["trivial"]["median"])} / {number(clock["normal"]["median"])} / {number(clock["critical"]["median"])} sec')
    dm=deep.get('metrics',{})
    blockers=deep.get('blockers',[])
    text=(f'Deep confirmation: {deep.get("deep_confirmed",False)} · qualified: {deep.get("qualified",False)}\n'
          f'{deep.get("games",0)} games · critical top-1 {percentage(dm.get("critical_top1"))} / {dm.get("critical",0)} opportunities\n'
          f'Actual / expected score with {outcomes.get("rating_margin",CONFIG.result_rating_margin)} Elo allowance: '
          f'{number(outcomes.get("actual"))} / {number(outcomes.get("expected_with_margin"))}\n'
          f'Outcome screen accounts for {proof["examined_periods"]} examined complete periods.\n'
          'No personal regime change is required. Habit, lag, underrating and improvement remain possible. This route is capped at HIGH.')
    if blockers:text+='\n'+'\n'.join('• '+v for v in blockers)
    embed.add_field(name='Deep corroboration & outcomes',value=text[:1024],inline=False)
    if proof.get('timing_method')=='within-game':
        add_local_clock_field(embed,proof['clocks']['profile']['normalized'],candidate['time_control'])


def add_local_clock_field(embed,local,label):
    samples=local['samples'];anchors=local['anchor_range']
    embed.add_field(name=f'Within-game delay comparison · {label}',inline=False,
        value=f'{"Elevated supporting pattern" if local["elevated"] else "Not elevated"} · {local["anchor_games"]} adequately timed games\n'
              f'Ordinary pace between games: {number(anchors[0])}–{number(anchors[1])} sec\n'
              f'Trivial / ordinary / critical relative medians: {number(samples["trivial"]["median"])} / {number(samples["normal"]["median"])} / {number(samples["critical"]["median"])}\n'
              f'Trivial near-instant fraction: {percentage(samples["trivial"]["near_instant"])} · shared-category games: {local["shared_games"]}\n'
              f'Relative trivial–critical distribution overlap: {percentage(local["overlap"]["trivial_critical"])}\n'
              '1.0 is that game’s ordinary-move median. Premoves remain counted. Changing absolute pace between games can retain a repeated easy/hard relationship. Habits or lag can explain this; timing alone cannot establish HIGH.')


def add_baseline_fields(embed,result):
    comparison=result.clusters.get('personal',{})
    if not comparison:return
    for key,label in (('baseline','Same-control rated baseline'),('cluster','Highest-signal rated cluster')):
        row=comparison[key]
        embed.add_field(name=label,inline=False,
            value=f'Games: {row["games"]} · meaningful decisions: {row["decisions"]}\n'
                  f'Weighted top-1: {percentage(row["weighted_top1"])} · top-3: {percentage(row["top3"])}\n'
                  f'Median / robust CPL: {number(row["median_cpl"])} / {number(row["robust_cpl"])}\n'
                  f'Critical top-1: {percentage(row["critical_top1"])} ({row["critical"]} opportunities)')
    opposition=comparison.get('opponent_context',{})
    if opposition:
        lines=[]
        for key,label in (('baseline','Baseline'),('cluster','Cluster')):
            row=opposition[key]
            lines.append(f'{label}: player / opponent median Elo {number(row["player_rating"])} / {number(row["opponent_rating"])} · gap {number(row["elo_difference"])}\n'
                         f'Expected / actual score: {percentage(row["expected_score"])} / {percentage(row["actual_score"])}')
        row=comparison['cluster']
        lines.append(f'Competitive decisions: {row.get("competitive_decisions",0)} · top-1: {percentage(row.get("competitive_top1"))} · CPL: {number(row.get("competitive_cpl"))}\n'
                     f'Competitive critical top-1: {percentage(row.get("competitive_critical_top1"))} ({row.get("competitive_critical",0)})\n'
                     f'Easy conversion decisions: {row.get("easy_conversion_decisions",0)} · opponent-error exposure: {row.get("opponent_blunder_exposure",0)}\n'
                     f'Already winning fraction: {percentage(row.get("easy_winning_position_fraction"))}')
        embed.add_field(name='Opponent strength & position ease',inline=False,value='\n'.join(lines)[:1024])
    delta=comparison['deltas']
    embed.add_field(name='Leave-cluster-out difference',inline=False,
        value=f'Personal engine anomaly: {comparison["state"]} · baseline sufficient: {comparison["sufficient"]}\n'
              f'Weighted top-1: {number(None if delta["weighted_top1"] is None else delta["weighted_top1"]*100," pp")}\n'
              f'Top-3: {number(None if delta["top3"] is None else delta["top3"]*100," pp")} · median / robust CPL: {number(delta["median_cpl"])} / {number(delta["robust_cpl"])}\n'
              f'Critical top-1: {number(None if delta["critical_top1"] is None else delta["critical_top1"]*100," pp")}\n'
              f'Mistake / blunder rate change: {number(None if delta["mistakes_rate"] is None else delta["mistakes_rate"]*100," pp")} / {number(None if delta["blunders_rate"] is None else delta["blunders_rate"]*100," pp")}\n'
              f'Comparison games before / after: {comparison["before_games"]} / {comparison["after_games"]}\n'
              'The selected period is excluded from its baseline. These are heuristic effect sizes, not cheating probabilities.')

    timing=comparison.get('timing',{})
    if timing:
        a,b=timing['baseline']['comparison'],timing['cluster']['comparison']
        embed.add_field(name='Leave-cluster-out timing comparison',inline=False,
            value=f'Delayed MAD: {number(a["delayed_mad"])} → {number(b["delayed_mad"])} sec\n'
                  f'IQR: {number(a["iqr"])} → {number(b["iqr"])} sec · entropy: {number(a["entropy"])} → {number(b["entropy"])}\n'
                  f'Modal band share: {percentage(a["cluster_fraction"])} → {percentage(b["cluster_fraction"])}\n'
                  'Clock coverage and same-control matching apply; changes are supporting evidence, not proof.')


def add_gate_fields(embed,result):
    add_gameplay_fields(embed,result)
    if result.diagnostics.get('high_paths'):
        if result.priority in ('HIGH','VERY HIGH'):
            embed.add_field(name='HIGH trigger',value=result.diagnostics.get('high_path') or 'Required evidence established.',inline=False)
        for name,row in result.diagnostics['high_paths'].items():
            status='PASS' if row['passed'] else 'FAIL'
            embed.add_field(name=name+' — '+status,value=('\n'.join('• '+v for v in row['blockers']) or 'Required evidence established.')[:1024],inline=False)
        return
    if result.diagnostics.get('gameplay',{}).get('qualified'):
        embed.add_field(name='HIGH trigger',value=result.diagnostics['high_path'],inline=False)
        return
    # The convergent route has its own explicitly displayed period and gates;
    # unrelated legacy-cluster failures must not contradict its HIGH trigger.
    if result.clusters.get('convergence',{}).get('raised_priority'):return
    d=result.diagnostics;g=d.get('gate_scores',{});c=result.clusters.get('strongest') or {};m=c.get('metrics',{})
    embed.add_field(name='Priority Gate',inline=False,
        value=f'Absolute engine / critical evidence: {g.get("Engine Precision",0):.2f} / {g.get("Critical Position Precision",0):.2f}\n'
              f'Personal anomaly: {result.clusters.get("personal",{}).get("state","Unavailable")}\n'
              f'Timing / result support: {g.get("Move-Time Pattern",0):.2f} / {g.get("Account / Results",0):.2f}\n'
              f'Separate recurrence: {result.clusters.get("recurrence",False)}\n'
              f'Qualifying persistent cluster: {d.get("qualifying_cluster",False)}\n'
              f'Cluster games / decisions / critical opportunities: {m.get("games",0)} / {m.get("decisions",0)} / {m.get("critical",0)}\n'
              f'Deep core / baseline anomaly confirmed: {result.deep_confirmed} / {d.get("baseline_anomaly_confirmed",False)}\n'
              'Engine-derived performance change does not supply independent support.')
    blocked=d.get('high_blocked',[])
    embed.add_field(name='HIGH blocked because' if blocked else 'HIGH trigger',inline=False,
                    value=('\n'.join('• '+v for v in blocked) if blocked else
                           str(d.get('high_path') or 'Persistent, deep-confirmed anomaly with independent support.')+(' · small-sample exception' if d.get('small_sample_high') else ''))[:1024])
    candidate=result.clusters.get('review_candidate')
    if candidate and candidate is not result.clusters.get('strongest'):
        review=result.clusters['candidate_deep'];m=candidate['metrics']
        embed.add_field(name='Deep discovery candidate',inline=False,
            value=f'Chronological {m["games"]}-game period · {m["eligible_games"]} adequately covered games\n'
                  f'Deep-reviewed: {review["games"]} · core confirmation: {review["confirmed"]}\n'
                  'This exploratory review does not bypass persistence or independent-support requirements.')
    support=d.get('result_support',{})
    if support.get('bound') is not None:
        embed.add_field(name='Same-period result context',inline=False,
            value=f'{support["games"]} rated games · actual score {number(support["actual"])} / expected {number(support["expected_with_margin"])}\n'
                  f'Expected score allows {support["rating_margin"]} Elo of underrating. Conservative bounded-score support: {support["score"]:.2f}.\n'
                  'Supporting result context only. Underrating, improvement and correlated games remain alternatives.')


def add_gameplay_fields(embed,result):
    d=result.diagnostics.get('gameplay')
    if not d:return
    best=d.get('best') or {};s=best.get('summary',{});deep=best.get('deep',{}).get('summary',{})
    embed.add_field(name='Human / difficulty evidence — heuristic',inline=False,
        value=f'High-information decisions: {s.get("hits",0)} / {s.get("opportunities",0)} capped opportunities\n'
              f'Contributor games: {s.get("contributors",0)} / {s.get("games",0)} · rating reference: {s.get("rating_reference") or "unavailable"}\n'
              f'Opportunity-bearing games: {s.get("opportunity_games",0)} · bounded information: {number(s.get("information"))}\n'
              f'Expected / observed quality: {number(s.get("quality_reference"))} / {number(s.get("observed_quality"))}\n'
              f'Raw quality excess / headroom anomaly: {number(s.get("quality_excess"))} / {number(s.get("anomaly_strength"))}\n'
              f'Descriptive move accuracy: {number(s.get("accuracy_index"))} · selective-assistance games: {s.get("selective_games",0)}\n'
              f'Difficulty inversion strength: {number(s.get("inversion_strength"))} · deep-stable high-information decisions: {deep.get("stable_hits",0)}\n'
              f'Gameplay class: {best.get("class","unavailable")} · controls: {", ".join(best.get("controls",[])) or "unavailable"}\n'
              'Related rank, loss and difficulty measurements form one gameplay family, not independent probabilities.')
    embed.add_field(name='Gameplay HIGH route gates',inline=False,
        value='\n'.join(f'{key.replace("_"," ").title()}: {"PASS" if value else "FAIL"}' for key,value in d['gates'].items())+
              '\nTiming/results are optional for this route. Legacy gates are separate.')
    f=d['funnel'];flow=f.get('_flow',{})
    overlap={key:value for key,value in f.items() if key!='_flow'}
    embed.add_field(name='Evidence coverage — overlapping categories',inline=False,
        value=' · '.join(f'{key.replace("_"," ").title()}: {value}' for key,value in overlap.items())[:1024])
    if flow:
        embed.add_field(name='Evidence survivor funnel',inline=False,
            value=' → '.join(f'{key.replace("_"," ")} {value}' for key,value in flow.items())[:1024])


async def channel_check(ctx):
    if ctx.channel_id==CHANNEL_ID and not ctx.user.bot:return True
    await ctx.response.send_message('🛡️ Fair Play reviews are only available in the Fair Play Task Force channel.',ephemeral=True)
    return False


class SubmitModal(discord.ui.Modal, title='Fair Play Review'):
    account = discord.ui.TextInput(label='Chess.com username',min_length=2,max_length=25,required=True)

    def __init__(self):
        super().__init__(custom_id=NAMESPACE+'submit-modal',timeout=300)

    async def on_submit(self,ctx):
        if await channel_check(ctx):await submit(ctx,str(self.account.value))

    async def on_error(self,ctx,error):
        # Never let discord.py's default error logger include a case/traceback.
        if not ctx.response.is_done():await ctx.response.send_message('Could not submit the review. Please try again.',ephemeral=True)


class SubmitView(discord.ui.View):
    def __init__(self):super().__init__(timeout=None)

    async def interaction_check(self,ctx):return await channel_check(ctx)

    @discord.ui.button(label='Submit Chess.com Account',emoji='🔎',custom_id=NAMESPACE+'submit')
    async def open(self,ctx,button):await ctx.response.send_modal(SubmitModal())

    async def on_error(self,ctx,error,item):
        if not ctx.response.is_done():await ctx.response.send_message('Fair Play is temporarily unavailable.',ephemeral=True)


class ReportView(SubmitView):
    def __init__(self,target=None):
        super().__init__()
        self.clear_items()
        for label,emoji,action in [('Highest-Signal Games','🎯','games'),('Timing','⏱️','timing'),
                                   ('Performance','📈','performance'),('Clusters & History','🔬','clusters'),('Engine Analysis','♟️','engine'),('Review Gates','🛡️','gates'),('Re-scan','🔄','rescan')]:
            button = discord.ui.Button(label=label,emoji=emoji,custom_id=NAMESPACE+action)
            async def show(ctx,action=action,label=label):
                if _service is None:
                    await ctx.response.send_message('Fair Play is reconnecting. Please try again shortly.',ephemeral=True);return
                if action=='rescan':
                    try:target = username(ctx.message.embeds[0].title.removeprefix(REPORT_PREFIX))
                    except (ValueError,IndexError,ReviewError):
                        await ctx.response.send_message('Open the submission panel to start a new review.',ephemeral=True);return
                    await submit(ctx,target);return
                result = _service.result_for(ctx.message.id)
                if result is None:
                    await ctx.response.send_message('Detailed data expired or was cleared after a restart. Re-scan to rebuild it. The public review remains in Discord.',ephemeral=True);return
                await ctx.response.send_message(embed=detail_embed(result,label),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
            button.callback = show;self.add_item(button)
        if target:self.add_item(discord.ui.Button(label='Chess.com Profile',emoji='🔗',url=f'https://www.chess.com/member/{username(target)}'))


@dataclass
class Job:
    target: str
    ctx: object
    stage: str = 'Fetching profile…'
    progress_percent: int = 0
    message: object = None
    stop: threading.Event = field(default_factory=threading.Event)
    message_deleted: bool = False
    delivery_unknown: bool = False
    token: str = field(default_factory=lambda:uuid.uuid4().hex[:12])


class FairPlayService:
    def __init__(self,client,channel,analyzer=review):
        self.client,self.channel,self.analyzer = client,channel,analyzer
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='fairplay')
        self.queue = asyncio.Queue(maxsize=2)
        self.jobs = {}
        self.results = OrderedDict()
        self.cache = OrderedDict()
        self.last_human = time.time()
        self.restore_grace = time.time()+60
        self.panel_id = None
        self.panel_lock = asyncio.Lock()
        self.worker = None
        self.idle_task = None
        self.closed = False

    def note_human(self):self.last_human = time.time()

    def result_for(self,message_id):
        self.expire_cache()
        entry = self.results.get(int(message_id))
        return entry[1] if entry else None

    def expire_cache(self):
        now = time.time()
        for key,(at,_) in list(self.results.items()):
            if now-at>7200:self.results.pop(key,None)
        for key,(at,_,_) in list(self.cache.items()):
            if now-at>1800:self.cache.pop(key,None)
        while len(self.results)>50:self.results.popitem(last=False)
        while len(self.cache)>20:self.cache.popitem(last=False)

    async def enqueue(self,ctx,target):
        self.expire_cache()
        if self.closed:
            await ctx.followup.send('Fair Play is restarting. Please try again shortly.',ephemeral=True);return
        if target in self.jobs:
            job = self.jobs[target]
            text = 'This account already has a queued or running review. The existing scan is reused.'
            if job.message:text += f' [View progress]({job.message.jump_url})'
            await ctx.followup.send(text,ephemeral=True);return
        if target in self.cache:
            _,result,message = self.cache[target]
            await ctx.followup.send(f'A recent review is cached. [View review]({message.jump_url}). Re-scan becomes available after 30 minutes.',ephemeral=True);return
        if len(self.jobs)>=2:
            await ctx.followup.send('The Fair Play queue is full (one scan and one waiting review). Please try again later.',ephemeral=True);return
        job = Job(target,ctx)
        self.jobs[target] = job
        self.queue.put_nowait(job)
        import feature_usage
        feature_usage.note('use:fairplay-scan',ctx.user.id)
        await ctx.followup.send('Review queued. Public progress appears after the account is validated. One heavy scan runs at a time.',ephemeral=True)

    async def safe_progress(self,job,stage,*,view=None,embed=None):
        if job.message_deleted:return False
        try:
            if job.delivery_unknown and job.message is None:
                async for candidate in self.channel.history(limit=100):
                    if candidate.author.id==self.client.user.id and candidate.embeds and f'Review ID: {job.token}' in str(candidate.embeds[0].footer.text):
                        job.message = candidate;job.delivery_unknown = False;break
                if job.message is None:return False  # an uncertain ACK is not permission to repost
            job.progress_percent=max(job.progress_percent,estimate(stage))
            card = (embed.copy() if embed is not None else progress_embed(job.target,stage,job.progress_percent))
            card.set_footer(text=(card.footer.text or '')+f' · Review ID: {job.token}')
            if job.message is None:
                job.delivery_unknown = True
                job.message = await self.channel.send(embed=card,view=view,nonce=job.token,
                                                      allowed_mentions=discord.AllowedMentions.none())
                job.delivery_unknown = False
            else:await job.message.edit(embed=card,view=view)
            return True
        except discord.NotFound:job.message_deleted = True
        except discord.HTTPException:pass  # retry later; never create a second progress card
        return False

    async def process(self,job):
        loop = asyncio.get_running_loop()
        def progress(stage):
            if not self.closed:loop.call_soon_threadsafe(setattr,job,'stage',stage)
        future = None
        last = None
        try:
            future = loop.run_in_executor(self.executor,lambda:self.analyzer(job.target,progress,cancel=job.stop))
            while not future.done():
                if job.stage!='Fetching profile…' and job.stage!=last:
                    if await self.safe_progress(job,job.stage):last = job.stage
                await asyncio.wait({future},timeout=3)
            result = await future
            # All detailed position caches remain bounded in the engine worker.
            # Discord detail pages need game summaries, not thousands of FENs.
            for game in result.games:game.decisions.clear()
            if await self.safe_progress(job,'Complete',view=ReportView(result.username),embed=result_embed(result)):
                self.results[job.message.id] = (time.time(),result)
                self.cache[job.target] = (time.time(),result,job.message)
                self.expire_cache()
        except AccountNotFound:
            try:await job.ctx.followup.send('❌ **Chess.com account not found**\nCheck the username and try again.',ephemeral=True)
            except discord.HTTPException:pass
        except ReviewError as error:
            if job.message is not None:await self.safe_progress(job,'❌ '+str(error))
            else:
                try:await job.ctx.followup.send('❌ '+str(error),ephemeral=True)
                except discord.HTTPException:pass
        except asyncio.CancelledError:
            job.stop.set()
            # Await bounded cleanup so a replacement task cannot launch a second
            # engine while the previous executor is still finishing.
            try:
                if future is not None:await asyncio.wait_for(asyncio.shield(future),timeout=20)
            except Exception:pass
            raise
        except Exception:
            # Deliberately do not log exception values/targets/reports.
            if job.message is not None:await self.safe_progress(job,'❌ Analysis could not finish safely. Please try again later.')
            else:
                try:await job.ctx.followup.send('Analysis could not finish safely. Please try again later.',ephemeral=True)
                except discord.HTTPException:pass

    async def run_queue(self):
        try:
            while not self.closed and not self.client.is_closed():
                job = await self.queue.get()
                try:await self.process(job)
                finally:self.jobs.pop(job.target,None);self.queue.task_done()
        finally:
            for job in self.jobs.values():job.stop.set()
            self.executor.shutdown(wait=False,cancel_futures=True)

    def is_panel(self,message):
        return (message.author.id==self.client.user.id and bool(message.embeds)
                and message.embeds[0].title==PANEL_TITLE and message.embeds[0].footer.text==PANEL_MARKER)

    async def restore_history(self):
        try:
            anchor = None
            baseline = self.last_human
            # Stream until the newest panel is found, even if it is >200 posts
            # up. Do not retain a case/history list in memory or on disk.
            async for message in self.channel.history(limit=None):
                if not message.author.bot:
                    anchor = max(anchor or 0,message.created_at.timestamp())
                elif self.is_panel(message):
                    if self.panel_id is None:self.panel_id = message.id
                    anchor = max(anchor or 0,message.created_at.timestamp())
                    break
                elif message.author.id==self.client.user.id and message.embeds and str(message.embeds[0].title).startswith(REPORT_PREFIX):
                    anchor = max(anchor or 0,message.created_at.timestamp())
            if anchor is not None:
                self.last_human = anchor if self.last_human==baseline else max(self.last_human,anchor)
        except discord.HTTPException:pass  # default to a fresh hour if history is inaccessible

    async def ensure_panel(self):
        if self.jobs or time.time()<self.restore_grace or time.time()-self.last_human<IDLE_SECONDS:return False
        async with self.panel_lock:
            # A human can arrive while the first caller was waiting for the lock.
            if self.jobs or time.time()-self.last_human<IDLE_SECONDS:return False
            try:
                messages = [m async for m in self.channel.history(limit=200)]
                if any(not m.author.bot and m.created_at.timestamp()>self.last_human for m in messages):
                    self.last_human = max(m.created_at.timestamp() for m in messages if not m.author.bot)
                    if time.time()-self.last_human<IDLE_SECONDS:return False
                panels = [m for m in messages if self.is_panel(m)]
                if messages and self.is_panel(messages[0]):
                    self.panel_id = messages[0].id
                    for old in panels[1:]:await old.delete()
                    current=panel_embed()
                    if messages[0].embeds[0].description!=current.description:
                        await messages[0].edit(embed=current,view=SubmitView(),
                                               allowed_mentions=discord.AllowedMentions.none())
                    return False
                # Refuse to add another panel if deleting an old one fails.
                for old in panels:await old.delete()
                if self.panel_id and not any(m.id==self.panel_id for m in panels):
                    try:await self.channel.get_partial_message(self.panel_id).delete()
                    except discord.NotFound:pass
                if time.time()-self.last_human<IDLE_SECONDS:return False
                message = await self.channel.send(embed=panel_embed(),view=SubmitView(),allowed_mentions=discord.AllowedMentions.none())
                self.panel_id = message.id
                return True
            except discord.HTTPException:return False

    async def idle_loop(self):
        while not self.closed and not self.client.is_closed():
            await self.ensure_panel()
            await asyncio.sleep(60)

    async def close(self):
        self.closed = True
        for job in self.jobs.values():job.stop.set()
        for task in (self.idle_task,self.worker):
            if task and not task.done():task.cancel()
        await asyncio.gather(*(task for task in (self.idle_task,self.worker) if task),return_exceptions=True)
        self.executor.shutdown(wait=False,cancel_futures=True)


async def submit(ctx,target):
    await ctx.response.defer(ephemeral=True,thinking=True)
    try:target = username(target)
    except ReviewError as error:
        await ctx.followup.send(str(error),ephemeral=True);return
    if _service is None:
        await ctx.followup.send('Fair Play is reconnecting. Please try again shortly.',ephemeral=True);return
    _service.note_human()
    await _service.enqueue(ctx,target)


async def handle_message(message):
    if message.channel.id!=CHANNEL_ID:return False
    if not message.author.bot:
        if _service:_service.note_human()
        if message.content.strip().startswith('!'):
            # User text is never parsed as a chess move or account URL here.
            await message.channel.send(RESERVED,allowed_mentions=discord.AllowedMentions.none(),delete_after=15)
    return True


async def startup(client):
    global _service
    if _service is not None and not _service.closed:return
    client.add_view(SubmitView())
    client.add_view(ReportView())
    try:channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
    except discord.HTTPException:
        print('Fair Play channel unavailable; normal SharkBot startup continues.',flush=True);return
    _service = FairPlayService(client,channel)
    _service.worker = asyncio.create_task(_service.run_queue(),name='fairplay-queue')
    async def restore_and_idle():
        await _service.restore_history()
        await _service.idle_loop()
    _service.idle_task = asyncio.create_task(restore_and_idle(),name='fairplay-idle')
