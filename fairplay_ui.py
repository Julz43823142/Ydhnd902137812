"""Discord is the case history. Runtime review data uses encrypted checkpoints.

A bounded single-review executor/queue keeps engine work off Discord's loop and
gives the active review exclusive access to the shared Stockfish pool. No Discord
token, ledger, wallet or punishments.
"""
import asyncio
import os
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord

from fairplay_analysis import (ReviewResult, review, review_interest,
                               get_shared_engine_pool, close_shared_engine_pool,
                               available_engine_cpus, available_engine_memory_mb)
from fairplay_config import CHANNEL_ID, DISCLAIMER, NAMESPACE, CONFIG
from fairplay_data import AccountNotFound, ReviewError, username
from fairplay_progress import estimate, bar, label, duration, LiveTiming, ReliableTiming
from fairplay_checkpoint import CheckpointStore

RESERVED = '🛡️ This channel is reserved for Fair Play reviews.'
PANEL_TITLE = '🛡️ Fair Play Task Force'
REPORT_PREFIX = '🛡️ Fair Play Review — '
IDLE_SECONDS = CONFIG.intro_panel_seconds
PANEL_MARKER = 'shark:fairplay:panel:v1'
MAX_CONCURRENT_SCANS = 1
MAX_WAITING_SCANS = 5
MAX_JOBS = MAX_CONCURRENT_SCANS + MAX_WAITING_SCANS
_service = None


def public_text(value):
    return discord.utils.escape_markdown(str(value))


def panel_embed():
    parallel=os.getenv("FAIRPLAY_DISTRIBUTED")=="1"
    scope=("up to 200 comparable-opponent rated games and 50 stratified historical games, "
           "with five on-demand encrypted Stockfish compute jobs"
           if parallel else
           "up to 100 comparable-opponent rated games and 25 stratified historical games")
    embed = discord.Embed(title=PANEL_TITLE, color=0x427CBA,
        description='Want to review a suspicious Chess.com account?\n\n'
                    "Submit a Chess.com username. SharkBot screens up to 500 rated live games (fast MultiPV 1), then deeply reviews "
                    +scope+". Fast and deep candidate verification use MultiPV 3, depth 18 rapid/blitz and depth 12 bullet. "
                    "History metadata extends to 1,000 rated games when available. Only confirmed deep games support the final score.\n\n"
                    '**This is an automated screening tool — not proof of cheating.**')
    embed.set_footer(text=PANEL_MARKER)
    return embed


def progress_embed(target, stage, value=None, timing=None):
    value=estimate(stage) if value is None else value
    stopped=stage.startswith(('❌', 'Stopped by moderator'))
    description=('**Review stopped — no result.**\n\n'+label(stage) if stopped
                 else bar(value)+"\n\n"+label(stage))
    if timing is not None:description+='\n'+timing.summary(stage)
    embed = discord.Embed(title=REPORT_PREFIX+target, description=description, color=0x427CBA)
    embed.set_footer(text="Work progress (stage-weighted), not time percentage · ETA is approximate across phases")
    embed.add_field(name='⚠️ Automated screening only', value=DISCLAIMER, inline=False)
    return embed


def number(value, suffix=''):
    return 'Unavailable' if value is None else f'{value:.1f}{suffix}'


def percentage(value):
    return number(None if value is None else value*100,'%')


def capture_human_examples(result, limit=4):
    """Keep only bounded public-game pointers when full decision trees expire.

    The service deliberately drops in-memory positions/FENs after posting the
    report, but the Human Moves button must still work afterwards. Never copy
    model policy distributions or board positions to Discord history.
    """
    from fairplay_maia import policy_evidence
    informative=[]
    for game in getattr(result,'games',()):
        for decision in game.decisions:
            if not decision.human_policy:continue
            row=policy_evidence(decision,decision.human_policy)
            if not row or not row['eligible'] or row['information']<=0:continue
            informative.append((row['information'],game.ended,decision.ply,game,decision,row))
    informative.sort(key=lambda item:(-item[0],item[1],item[2]))
    return [
        {'class':game.time_class,'move_number':(decision.ply+1)//2,
         'url':game.url,'move':decision.move,'rank':row['played_move_rank'],
         'cpl':decision.metrics.get('cpl',0),'deep':bool(game.deep)}
        for _,_,_,game,decision,row in informative[:max(0,limit)]
    ]


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
              f'Deep-budget reviewed: **{coverage.get("deep_reviewed",result.deep_coverage["games"])}** · deep-confirmed evidence: **{"yes" if result.deep_confirmed else "not established"}** · older context-only: **{coverage.get("context_only",max(0,result.selected_games-coverage.get("fast_scanned",totals["games"])))}**\n'
              f'Meaningful decisions: **{totals["decisions"]:,}**\n{classes}\n'
              f'Account age: {result.context["age_days"] if result.context["age_days"] is not None else "unavailable"} days')
    funnel=result.diagnostics.get('gameplay',{}).get('funnel',{})
    if funnel:sample+=f'\nCompetitive decisions: {funnel["competitive"]} · high-difficulty: {funnel["high_difficulty"]}'
    depth_contract=result.diagnostics.get('run_contract',{})
    if depth_contract.get('required_primary_depth_by_class'):
        sample+='\nStockfish depth: Rapid/Blitz 18 · Bullet 12 (screening; lower precision)'
    elif depth_contract.get('selected_required_depth_by_class'):
        sample+='\nSelected deep sample only: Rapid/Blitz depth 18 · Bullet depth 12'
    if depth_contract.get('fast',{}).get('multipv') == 1:
        sample+='\nBroad: MultiPV 1 + played-move check · Selected fast + deep: MultiPV 3' if depth_contract.get('required_selected_full_depth') else '\nFast: MultiPV 1 + played-move check · Deep: MultiPV 3'
    v21=result.diagnostics.get('v21_selection',{})
    if v21:
        sample += (f"\nScoped deep coverage: {v21['deep_games_completed']}/"
                   f"{v21['recent_peer_deep_games']+v21['historical_extra_deep_games']} "
                   "selected games; 500-wide coverage is FAST-ONLY outside scope.")
    if coverage.get('history_probed'):
        sample += f'\nRecent rated primary sample: {coverage["primary_fast_scanned"]}/{coverage["primary_collected"]} · historical discovery probes: {coverage.get("history_probed",0)}'
    sample += f'\nSkipped unrated games while collecting history: {result.skipped.get("unrated",0)} · unknown rated status: {result.skipped.get("rated_status_unknown",0)}'
    if coverage.get('history_probe_complete') is False:sample += '\nExtended-history discovery is incomplete; primary coverage is shown separately.'
    scope=[]
    if coverage.get('requested_primary_limit'):
        if v21:
            scope.append(f"Review scope: up to {coverage['requested_primary_limit']} rated games fast-screened; "
                         f"{v21['deep_games_completed']} peer-matched/stratified games deep-reviewed; "
                         "the other fast games are NOT depth-confirmed.")
        else:
            scope.append(f"Engine scope: latest up to {coverage['requested_primary_limit']} eligible rated games. "
                         "An account with thousands of games is NOT exhaustively analyzed.")
    if coverage.get('available_archive_months') is not None:
        scope.append(f"Archive months visited: {coverage.get('visited_archive_months',0)}/"
                     f"{coverage['available_archive_months']}; "
                     f"older months not visited: {coverage.get('unvisited_archive_months',0)}.")
    if coverage.get('eligible_games_capped'):
        scope.append('Context/history was capped by the configured game limit.')
    if result.skipped:sample += f'\nSkipped archive/game entries: {sum(result.skipped.values())}'
    if coverage.get('optional_context_partial'):sample += '\n⚠️ Older context is incomplete; primary engine coverage is complete.'
    elif result.partial:sample += '\n⚠️ Partial scan / limited archive coverage. Missing data is not suspicious.'
    dates=[g.ended for g in (result.timeline or result.games) if g.ended>0]
    if dates:sample += f'\nEngine-covered dates: <t:{min(dates)}:d> → <t:{max(dates)}:d>'
    embed.add_field(name='Sample',value=sample[:1024],inline=False)
    if scope:embed.add_field(name='Review scope — bounded archive',value='\n'.join(scope)[:1024],inline=False)
    history=result.diagnostics.get('public_history_stats',{})
    if history:
        period_lines=[]
        for span in ('7d','30d','90d','365d'):
            rows=history.get('periods',{}).get(span,{})
            details=[]
            for kind in ('blitz','rapid','bullet'):
                item=rows.get(kind,{})
                if not item.get('rated_games'):continue
                details.append(f"{kind.title()}: {item['rated_games']} rated / {item['wins']}W "
                               f"(Accuracy reported {item['official_accuracy_coverage']})")
            period_lines.append(f"**{span}:** "+('; '.join(details) if details else 'No sampled rated games'))
        embed.add_field(name='Public rating & accuracy timeline (descriptive only)',
                        value=('\n'.join(period_lines)+
                               '\nAccuracy values may be missing or review-selected; never an accusation.')[:1024],
                        inline=False)
    parallel=result.diagnostics.get('distributed_compute',{})
    if parallel.get('enabled'):
        embed.add_field(name='Distributed Stockfish compute',
                        value=(f"Five compute jobs requested: **{parallel.get('jobs_requested',0)}** · "
                               f"Remotely verified games: **{parallel.get('games_verified_remotely',0)}** · "
                               f"Locally recovered games: **{parallel.get('games_recomputed_locally',0)}**. "
                               "All 250 selected games must be complete before one joint assessment."),
                        inline=False)
    embed.add_field(name='Signals',value='\n'.join(f'**{key}:** {value}' for key,value in result.families.items()),inline=False)
    embed.add_field(name='Review notes',value='\n'.join('• '+value for value in result.reasons)[:1024],inline=False)
    if result.priority=='LOW':
        coverage_ok=coverage.get('primary_engine_complete',not result.partial)
        maia=result.diagnostics.get('human_reference',{})
        model_status=('evaluated' if maia.get('available') else 'not evaluated')
        blocked=result.diagnostics.get('high_blocked',[])
        explanation=('LOW means the measured evidence did not qualify for an elevated review priority; '
                     'it does not establish fair play.\n'
                     f'Primary engine sample: {"complete" if coverage_ok else "incomplete"} · '
                     f'Maia reference: {model_status}.\n')
        if blocked:
            explanation+='Most relevant missing HIGH criteria: '+'; '.join(blocked[:2])
        else:
            explanation+='See Review Gates for the exact evidence requirements.'
        embed.add_field(name='Why LOW is not a clearance',value=explanation[:1024],inline=False)
    embed.add_field(name='⚠️ Automated screening only',value=DISCLAIMER,inline=False)
    revision=(result.diagnostics.get('run_contract') or {}).get('code_revision')
    embed.set_footer(text=(f'{result.version} · {result.engine}'
                           + (f' · code {revision}' if revision else '')
                           + ' · heuristic thresholds, not probabilities · details expire after restart'))
    return embed


def detail_embed(result, mode):
    embed = discord.Embed(title=f'{mode} — {result.username}',color=0x427CBA)
    if mode=='Human Moves':
        reference=result.diagnostics.get('human_reference',{})
        embed.description=(reference.get('role') or reference.get('reason') or
            'No local human-model data is available for this review.')
        if reference.get('available'):
            embed.add_field(name='Local human reference',value=
                f"{reference.get('model', 'Maia-3')} · {reference.get('positions',0)} positions across "
                f"{reference.get('games',0)} games. Real preceding board history; CPU inference.",inline=False)
            rows=[g.human_reference for g in result.games if g.human_reference]
            embed.add_field(name='What was measured',value=
                f"Eligible sampled decisions: {sum(r.get('eligible',0) for r in rows)}\n"
                f"Low-policy strong moves: {sum(r.get('low_policy_strong_moves',0) for r in rows)}\n"
                'Policy likelihood estimates human move choice, not cheating. Unknown engine alternatives '
                'receive a conservative perfect-quality upper bound. Rare bad moves provide no strong-play evidence.',inline=False)
            maia_audit=result.diagnostics.get('maia_evidence_audit',{})
            whole=maia_audit.get('whole_engine_sample',{})
            selected=maia_audit.get('selected_period')
            if whole:
                def brief_audit(report):
                    return ' · '.join(f"{stage['gate']}: {stage['pass']}/{stage['before']} "
                        f"(unknown {stage['unknown']})" for stage in report['stages'])
                overview=("Whole engine sample: "+brief_audit(whole)+
                          ("\nSelected learned-policy period: "+brief_audit(selected) if selected else
                           "\nNo selected learned-policy period."))
                embed.add_field(name='Maia evidence funnel — explicit scopes',inline=False,
                                value=overview[:1024])
            comparison=result.diagnostics.get('learned_gameplay',{})
            proof=comparison.get('deep',{})
            embed.add_field(name='Paired human-reference review',value=
                f"Deep contributor games: {proof.get('contributors',0)} · comparable decisions: {proof.get('positions',0)}\n"
                + f"Game-capped evidence: {proof.get('effective_positions',0):g} decisions · contributor equivalents: {proof.get('contributor_weight',0):g}\n"
                + ('Distributed gameplay route established; manual review required.' if comparison.get('passed') else
                   'Not established: '+ '; '.join(comparison.get('blockers',[])[:3])),inline=False)
            examples=result.diagnostics.get('manual_maia_examples')
            if examples is None:examples=capture_human_examples(result)
            lines=[]
            for item in examples[:4]:
                source=f"[{item['class'].title()} · move {item['move_number']}]({item['url']})"
                lines.append(f"{source} — {item['move']} · human-model rank #{item['rank']} · "
                             f"Stockfish loss {item['cpl']:.0f} cp · "
                             + ('deep checked' if item['deep'] else 'fast screen'))
            if lines:
                embed.add_field(name='Decisions for manual inspection',value='\n'.join(lines)[:1024],inline=False)
            embed.add_field(name='Limits',value=
                'Maia was trained on human chess, not calibrated as a SharkBot misconduct detector. '
                'Platform ratings and time controls differ. Samples include misses; this reference can allocate '
                'extra full-game deep review. Distributed model discrepancies can raise review priority only after '
                'paired Stockfish confirmation; unsearched alternatives and model uncertainty reduce evidence.',inline=False)
        embed.set_footer(text=DISCLAIMER)
        return embed
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
            explanation=('\n'.join('• '+v for v in row['blockers']) or 'Required evidence established.')
            if 'candidates_evaluated' in row:
                explanation=(f"Examined: {row['candidates_evaluated']} candidate periods · "
                             f"displayed: {row.get('representative_time_class','unknown')} "
                             f"({row.get('candidate_games',0)} games)\n"+explanation)
            embed.add_field(name=name+' — '+status,value=explanation[:1024],inline=False)
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
              f'Opportunity-bearing games: {s.get("opportunity_games",0)} · hit-bearing games: {s.get("hit_games",0)} · single-hit games: {s.get("single_hit_games",0)}\n'
              f'Bounded information: {number(s.get("information"))}\n'
              f'Expected / observed quality: {number(s.get("quality_reference"))} / {number(s.get("observed_quality"))}\n'
              f'Raw quality excess / headroom anomaly: {number(s.get("quality_excess"))} / {number(s.get("anomaly_strength"))}\n'
              f'Descriptive move accuracy: {number(s.get("accuracy_index"))} · selective-assistance games: {s.get("selective_games",0)}\n'
              f'Difficulty inversion strength: {number(s.get("inversion_strength"))} · deep-stable high-information decisions: {deep.get("stable_hits",0)}\n'
              f'Gameplay class: {best.get("class","unavailable")} · controls: {", ".join(best.get("controls",[])) or "unavailable"}\n'
              'Related rank, loss and difficulty measurements form one gameplay family, not independent probabilities.')
    embed.add_field(name='Gameplay HIGH route gates',inline=False,
        value='\n'.join(f'{key.replace("_"," ").title()}: {"PASS" if value else "FAIL"}' for key,value in d['gates'].items())+
              '\nTiming/results are optional for this route. Legacy gates are separate.')
    moderate=d.get('distributed_moderate',{})
    if moderate:
        status='PASS' if moderate.get('passed') else 'FAIL';fast=moderate.get('fast',{});mdeep=moderate.get('deep',{})
        if moderate.get('passed'):
            detail=(f'Candidate games: {moderate.get("candidate_games",0)} · deep-reviewed: {moderate.get("deep_games",0)}\n'
                    f'Fast hits / opportunities: {fast.get("hits",0)} / {fast.get("opportunities",0)} · '
                    f'hit games / contributors / single-hit: {fast.get("hit_games",0)} / {fast.get("contributors",0)} / {fast.get("single_hit_games",0)}\n'
                    f'Deep hits / opportunities: {mdeep.get("hits",0)} / {mdeep.get("opportunities",0)} · '
                    f'hit games / contributors / stable hits: {mdeep.get("hit_games",0)} / {mdeep.get("contributors",0)} / {mdeep.get("stable_hits",0)}')
        else:
            detail='\n'.join('• '+v for v in moderate.get('blockers',[]))
        embed.add_field(name='Distributed gameplay MODERATE — '+status,inline=False,
            value=(detail or 'Required distributed evidence established.')[:1024])
    f=d['funnel'];flow=f.get('_flow',{})
    overlap={key:value for key,value in f.items() if key!='_flow'}
    embed.add_field(name='Evidence coverage — overlapping categories',inline=False,
        value=' · '.join(f'{key.replace("_"," ").title()}: {value}' for key,value in overlap.items())[:1024])
    audit=result.diagnostics.get('evidence_audit',{})
    scoped=audit.get('whole_engine_sample',{})
    if scoped:
        lines=[f"Scope: entire engine sample · {scoped['scope_games']} games / {scoped['decisions']} decisions"]
        for stage in scoped['stages']:
            lines.append(f"{stage['gate']}: {stage['before']} → {stage['pass']} PASS / "
                         f"{stage['fail']} FAIL / {stage['unknown']} UNKNOWN")
        embed.add_field(name='Actual sequential opportunity eligibility (PASS / FAIL / UNKNOWN)',
            inline=False,value='\n'.join(lines)[:1024])
    elif flow:
        embed.add_field(name='Engine opportunity funnel',inline=False,
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
                                   ('Performance','📈','performance'),('Clusters & History','🔬','clusters'),('Engine Analysis','♟️','engine'),('Human Moves','🧠','human'),('Review Gates','🛡️','gates'),('Re-scan','🔄','rescan')]:
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
    timing: LiveTiming = field(default_factory=LiveTiming)
    last_progress_edit: float = 0.0

    def timing_elapsed(self):
        return max(0,time.monotonic()-self.timing.started)
    message: object = None
    stop: threading.Event = field(default_factory=threading.Event)
    cancel_requested: bool = False  # explicit /stopfairplay; not a runner handoff
    report_published: bool = False  # do not retroactively cancel a delivered review
    message_deleted: bool = False
    delivery_unknown: bool = False
    token: str = field(default_factory=lambda:uuid.uuid4().hex[:12])


class FairPlayService:
    def __init__(self,client,channel,analyzer=review,checkpoints=None):
        self.client,self.channel,self.analyzer = client,channel,analyzer
        self.checkpoints = checkpoints
        self.executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SCANS,thread_name_prefix='fairplay')
        self.queue = asyncio.Queue(maxsize=MAX_JOBS)
        self.jobs = {}
        self.active_job = None  # The one scan currently using the engine executor.
        self.results = OrderedDict()
        self.cache = OrderedDict()
        self.failures = OrderedDict()  # sanitized audit only; persistent copy is encrypted
        self.last_human = time.time()
        self.restore_grace = time.time()+60
        self.panel_id = None
        self.panel_lock = asyncio.Lock()
        self.worker = None  # compatibility alias for the first queue worker
        self.workers = []
        self.idle_task = None
        self.pool_warm_task = None
        self.closed = False

    def note_human(self):self.last_human = time.time()

    async def stop_current(self):
        """Request cancellation only for the running scan; leave queued jobs alone.

        Do not cancel the asyncio queue worker or Stockfish pool: the engine
        thread must drain before the next scan starts. Unlike normal runner
        shutdown, an intentional stop must not be auto-resumed next boot.
        """
        if self.closed:return 'unavailable'
        job=self.active_job
        if job is None or job.report_published:return 'idle'
        if job.cancel_requested:return 'already'
        job.cancel_requested=True
        job.stop.set()
        if self.checkpoints is not None and self.checkpoints.enabled:
            try:
                persisted=await asyncio.to_thread(self.checkpoints.cancel,job.target)
            except Exception:
                persisted=False
            if not persisted:return 'checkpoint_unconfirmed'
        return 'stopping'

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
        if len(self.jobs)>=MAX_JOBS:
            await ctx.followup.send(
                f'The Fair Play queue is full ({MAX_CONCURRENT_SCANS} active + {MAX_WAITING_SCANS} waiting). Please try again later.',
                ephemeral=True);return
        job = Job(target,ctx)
        self.jobs[target] = job
        self.queue.put_nowait(job)
        saved = (await asyncio.to_thread(self.checkpoints.note,target,token=job.token)
                 if getattr(self,'checkpoints',None) is not None else False)
        import feature_usage
        feature_usage.note('use:fairplay-scan',ctx.user.id)
        await ctx.followup.send(
            f'Review accepted. Up to {MAX_CONCURRENT_SCANS} heavy scans run in parallel; additional reviews wait in queue. '
            'Public progress appears after the account is validated.'
            + ('' if saved or self.checkpoints is None else
               ' Durable checkpoint storage is unavailable; recovery after a worker crash is not guaranteed.'),
            ephemeral=True)

    async def safe_progress(self,job,stage,*,view=None,embed=None):
        if job.message_deleted:return False
        try:
            if job.delivery_unknown and job.message is None:
                async for candidate in self.channel.history(limit=100):
                    if candidate.author.id==self.client.user.id and candidate.embeds and f'Review ID: {job.token}' in str(candidate.embeds[0].footer.text):
                        job.message = candidate;job.delivery_unknown = False;break
                if job.message is None:return False  # an uncertain ACK is not permission to repost
            job.timing.observe(stage)
            job.progress_percent=max(job.progress_percent,estimate(stage))
            card = (embed.copy() if embed is not None else progress_embed(job.target,stage,job.progress_percent,job.timing))
            if embed is not None and stage=='Complete':
                card.add_field(name='Total scan time',value=duration(job.timing_elapsed()),inline=False)
            card.set_footer(text=(card.footer.text or '')+f' · Review ID: {job.token}')
            if job.message is None:
                job.delivery_unknown = True
                job.message = await self.channel.send(embed=card,view=view,nonce=job.token,
                                                      allowed_mentions=discord.AllowedMentions.none())
                job.delivery_unknown = False
            else:await job.message.edit(embed=card,view=view)
            job.last_progress_edit=time.monotonic()
            if getattr(self,'checkpoints',None) is not None:
                # Persist Discord message identity, so startup resumes by
                # editing the existing card rather than posting a fresh panel.
                metadata=({"timing":job.timing.snapshot()}
                          if isinstance(job.timing,ReliableTiming) else {})
                await asyncio.to_thread(
                    self.checkpoints.note,job.target,message_id=job.message.id,
                    token=job.token,stage=stage,force=False,**metadata)
            return True
        except discord.NotFound:job.message_deleted = True
        except discord.HTTPException:pass  # retry later; never create a second progress card
        return False

    async def save_failure(self, job, error):
        """Best-effort owner-only audit; a reporting error must never mask failure."""
        try:
            from fairplay_failure import failure_record, owner_failure_notice
            from fairplay_analysis import _shared_engine_pool
            report=failure_record(job,error,pool=_shared_engine_pool,
                                  checkpoints=self.checkpoints)
            self.failures[job.token]=report
            self.failures.move_to_end(job.token)
            while len(self.failures)>32:self.failures.popitem(last=False)
            durable=False
            if self.checkpoints is not None:
                try:
                    durable=await asyncio.to_thread(
                        self.checkpoints.record_failure,job.token,report)
                except Exception:
                    durable=False
            await owner_failure_notice(self.client,report,durable=durable)
        except Exception:
            # Diagnostic delivery is supplementary; never log raw exceptions.
            print('Fair Play private failure diagnostic unavailable.',flush=True)

    async def process(self,job):
        loop = asyncio.get_running_loop()
        def progress(stage):
            if not self.closed and not job.cancel_requested:
                loop.call_soon_threadsafe(setattr,job,'stage',stage)
        future = None
        last = None
        try:
            if job.cancel_requested:return
            def run_review():
                kwargs={'cancel':job.stop}
                if self.analyzer is review and self.checkpoints is not None:
                    kwargs['checkpoint']=self.checkpoints
                result=self.analyzer(job.target,progress,**kwargs)
                if job.cancel_requested:return result
                # Policy scoring can inspect thousands of engine-reviewed
                # positions. Do this in the scan executor, never Discord's
                # event loop, before sensitive decisions are discarded.
                try:result.diagnostics['manual_maia_examples']=capture_human_examples(result)
                except Exception:
                    # A supplementary public-link excerpt must never prevent
                    # delivery of a fully completed screening result.
                    result.diagnostics['manual_maia_examples']=[]
                return result
            if os.getenv("FAIRPLAY_V21")=="1" and os.getenv("FAIRPLAY_FULL_DEPTH18")=="1":
                if not isinstance(job.timing,ReliableTiming):
                    job.timing=ReliableTiming()
            else:
                job.timing=LiveTiming()
            future = loop.run_in_executor(self.executor,run_review)
            while not future.done():
                if (not job.cancel_requested and job.stage!='Fetching profile…' and
                        (job.stage!=last or (job.message is not None and time.monotonic()-job.last_progress_edit>=15))):
                    if await self.safe_progress(job,job.stage):last = job.stage
                await asyncio.wait({future},timeout=3)
            result = await future
            if job.cancel_requested:return  # A late completed scan is NOT a report.
            # All detailed position caches remain bounded in the engine worker.
            # Discord detail pages need game summaries, not thousands of FENs.
            for game in result.games:game.decisions.clear()
            if await self.safe_progress(job,'Complete',view=ReportView(result.username),embed=result_embed(result)):
                if job.cancel_requested:return
                self.results[job.message.id] = (time.time(),result)
                self.cache[job.target] = (time.time(),result,job.message)
                job.report_published=True
                self.expire_cache()
                if getattr(self,'checkpoints',None) is not None:
                    await asyncio.to_thread(self.checkpoints.finish,job.target)
        except AccountNotFound as error:
            if job.cancel_requested:return
            await self.save_failure(job,error)
            if getattr(self,'checkpoints',None) is not None:
                await asyncio.to_thread(self.checkpoints.suspend,job.target)
            try:
                if job.ctx is not None:
                    await job.ctx.followup.send('❌ **Chess.com account not found**\nCheck the username and try again.',ephemeral=True)
            except discord.HTTPException:pass
        except ReviewError as error:
            if job.cancel_requested:return
            await self.save_failure(job,error)
            if getattr(self,'checkpoints',None) is not None:
                await asyncio.to_thread(self.checkpoints.suspend,job.target)
            if job.message is not None:await self.safe_progress(job,'❌ '+str(error))
            else:
                try:
                    if job.ctx is not None:
                        await job.ctx.followup.send('❌ '+str(error),ephemeral=True)
                except discord.HTTPException:pass
        except asyncio.CancelledError:
            job.stop.set()
            # Await bounded cleanup so a replacement task cannot launch a second
            # engine while the previous executor is still finishing.
            try:
                if future is not None:await asyncio.wait_for(asyncio.shield(future),timeout=20)
            except Exception:pass
            raise
        except Exception as error:
            if job.cancel_requested:return
            await self.save_failure(job,error)
            if getattr(self,'checkpoints',None) is not None:
                await asyncio.to_thread(self.checkpoints.suspend,job.target)
            # Deliberately do not log exception values/targets/reports.
            if job.message is not None:await self.safe_progress(job,'❌ Analysis could not finish safely. Please try again later.')
            else:
                try:
                    if job.ctx is not None:
                        await job.ctx.followup.send('Analysis could not finish safely. Please try again later.',ephemeral=True)
                except discord.HTTPException:pass
        finally:
            if job.cancel_requested:
                try:
                    if job.message is not None:
                        await self.safe_progress(job,'Stopped by moderator')
                    elif job.ctx is not None:
                        await job.ctx.followup.send(
                            'Fair Play review stopped by a moderator.',ephemeral=True)
                except discord.HTTPException:
                    pass
                finally:
                    # Remove the encrypted resume state only after the engine
                    # future has settled; queued scans remain intact.
                    if self.checkpoints is not None:
                        await asyncio.to_thread(self.checkpoints.finish,job.target)

    async def run_queue(self):
        while not self.closed and not self.client.is_closed():
            job = await self.queue.get()
            self.active_job=job
            try:
                await self.process(job)
            finally:
                if self.active_job is job:self.active_job=None
                self.jobs.pop(job.target,None)
                self.queue.task_done()

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
        tasks=[]
        for task in [self.idle_task,self.pool_warm_task,*self.workers,self.worker]:
            if task and task not in tasks:tasks.append(task)
        for task in tasks:
            if not task.done():task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.executor.shutdown(wait=False,cancel_futures=True)
        if getattr(self,'checkpoints',None) is not None:
            await asyncio.to_thread(self.checkpoints.flush)
        await asyncio.to_thread(close_shared_engine_pool)


async def stop_current_review(ctx):
    """Moderator-only slash entrypoint for the existing active review."""
    if not await channel_check(ctx):return
    from shark_admin import ADMIN_ID
    permissions=getattr(ctx.user,'guild_permissions',None)
    authorized=(ctx.user.id==ADMIN_ID or
                bool(getattr(permissions,'administrator',False)) or
                bool(getattr(permissions,'manage_guild',False)))
    if not authorized:
        await ctx.response.send_message(
            'Only Sharkmeister or a server moderator with Manage Server permission can stop a Fair Play review.',
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        return
    await ctx.response.defer(ephemeral=True,thinking=True)
    if _service is None or _service.closed:
        text='Fair Play is reconnecting; no active review can be stopped.'
    else:
        _service.note_human()
        status=await _service.stop_current()
        text={
            'idle':'No Fair Play review is currently running. Queued reviews were left unchanged.',
            'already':'A stop was already requested for the active Fair Play review.',
            'unavailable':'Fair Play is shutting down; no stop request was applied.',
            'stopping':'Stop requested for the active Fair Play review. The engine will finish its in-flight search; queued reviews remain untouched. The stopped scan will not resume automatically.',
            'checkpoint_unconfirmed':'Stop requested for the active Fair Play review, but durable cancellation could not be confirmed. The bot will retry checkpoint cleanup when the engine exits; queued reviews remain untouched.',
        }[status]
    await ctx.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


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
    # Persistent DM button works after a planned GitHub Actions handoff.
    from fairplay_failure import FailureView
    client.add_view(FailureView())
    try:channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
    except discord.HTTPException:
        print('Fair Play channel unavailable; normal SharkBot startup continues.',flush=True);return
    checkpoints = await asyncio.to_thread(CheckpointStore)
    _service = FairPlayService(client,channel,checkpoints=checkpoints)
    if checkpoints.enabled:
        for item in checkpoints.pending()[:MAX_JOBS]:
            target = item['target']
            try:target=username(target)
            except ReviewError:continue
            if target in _service.jobs:continue
            timing=(ReliableTiming.from_checkpoint(item.get('timing'))
                    if os.getenv('FAIRPLAY_V21')=='1' and os.getenv('FAIRPLAY_FULL_DEPTH18')=='1'
                    else LiveTiming())
            job = Job(target,None,stage='Resuming interrupted scan…',timing=timing,
                      token=item.get('token') or uuid.uuid4().hex[:12])
            if item.get('message_id'):
                job.message = channel.get_partial_message(int(item['message_id']))
            _service.jobs[target]=job
            _service.queue.put_nowait(job)
        # No account names or case details in the deployment logs.
        if _service.jobs:
            print(f'Fair Play: restoring {len(_service.jobs)} encrypted scan checkpoint(s).',flush=True)
    async def warm_engine_pool():
        try:
            pool=await asyncio.to_thread(get_shared_engine_pool)
            memory=available_engine_memory_mb()
            memory_text='unbounded/unknown' if memory is None else f'{memory:.0f} MB'
            print(f'Fair Play engine pool ready: {pool.size} workers; '
                  f'effective CPU={available_engine_cpus()}; memory limit={memory_text}',flush=True)
        except Exception:pass  # scans still fail safely with the normal unavailable-engine message
        try:
            ready=await asyncio.to_thread(__import__('fairplay_maia').warm_worker)
            print('Fair Play local human reference: '+('ready' if ready else 'not installed'),flush=True)
        except Exception:
            print('Fair Play local human reference unavailable; Stockfish remains available.',flush=True)
    _service.pool_warm_task=asyncio.create_task(warm_engine_pool(),name='fairplay-engine-warmup')
    _service.workers = [
        asyncio.create_task(_service.run_queue(),name=f'fairplay-queue-{index+1}')
        for index in range(MAX_CONCURRENT_SCANS)
    ]
    _service.worker = _service.workers[0]
    async def restore_and_idle():
        await _service.restore_history()
        await _service.idle_loop()
    _service.idle_task = asyncio.create_task(restore_and_idle(),name='fairplay-idle')
