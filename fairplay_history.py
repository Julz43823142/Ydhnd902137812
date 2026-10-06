"""Bounded extended-history discovery, separate from evidentiary engine scoring.

Four fixed-node probes are discovery clues only: never scored as a complete
reviewed game. Exact-control windows nominate periods and neighboring controls
for the normal fast/deep pipeline. Stable histories need no extra full searches.
"""
import math
import statistics as stats
from fairplay_clusters import buckets, med
from fairplay_baseline import timing_profile
from fairplay_config import CONFIG


def probe_decisions(game, config=CONFIG):
    moves = [d for d in game.decisions if d.useful]
    count = min(len(moves),config.historical_probe_moves)
    if not count:return []
    # Evenly spaced decisions, independent of results/accuracy/closure labels.
    return [moves[round(i*(len(moves)-1)/max(1,count-1))] for i in range(count)]


def historical_candidates(history, probes, config=CONFIG):
    ranked = []
    for _, group in buckets(history).items():
        width = 8
        for cut in range(width,len(group)-width+1):
            before, after = group[cut-width:cut], group[cut:cut+width]
            a = [probes[g.identity] for g in before if g.identity in probes]
            b = [probes[g.identity] for g in after if g.identity in probes]
            quality = False
            gain = 0
            if min(len(a),len(b))>=6:
                old,new = med([m.get('robust_cpl') for m in a]),med([m.get('robust_cpl') for m in b])
                top_gain = med([m.get('top1') for m in b],0)-med([m.get('top1') for m in a],0)
                gain = max(0,(old or 0)-(new or 0))
                quality = gain>=25 and top_gain>=.20 and sum((m.get('robust_cpl') or 0)<=20 for m in b)>=6
            # Raw clocks include openings/trivial moves, exclude time trouble.
            bp,hp = timing_profile(before,config),timing_profile(after,config)
            bc,hc = bp['comparison'],hp['comparison']
            timing = (bc.get('delayed_mad') is not None and hc.get('delayed_mad') is not None
                      and bc['delayed_mad']>=2 and hc['delayed_mad']<=bc['delayed_mad']*.5
                      and (hc.get('cluster_fraction') or 0)>=.8
                      and (hc.get('modal_seconds') or 0)>=2)
            if quality or timing:
                # 8-game candidate plus adjoining comparison games. This is a
                # search allocation, never a score based on four sampled moves.
                targets = group[max(0,cut-6):min(len(group),cut+width+6)]
                ranked.append((gain+30*int(timing),targets))
    selected,used = [],set()
    for _, group in sorted(ranked,key=lambda r:r[0],reverse=True):
        for game in group:
            if game.identity not in used:
                selected.append(game);used.add(game.identity)
                if len(selected)>=config.historical_target_games:return selected
    return selected


def session_history(games, config=CONFIG):
    """Approximate end-time sessions, metadata only, never a cheating label."""
    sessions = []
    for (kind,control), group in buckets(games).items():
        current = []
        def save():
            if current:
                sessions.append({'class':kind,'control':control,'start':current[0].ended,
                                 'end':current[-1].ended,'games':len(current),
                                 'rating_start':current[0].rating,'rating_end':current[-1].rating,
                                 'score':sum(g.score for g in current),
                                 'timing':timing_profile(current,config)['comparison']})
        for game in group:
            if current and game.ended-current[-1].ended>config.session_gap_seconds:
                save();current=[]
            current.append(game)
        save()
    return sorted(sessions,key=lambda r:r['start'])
