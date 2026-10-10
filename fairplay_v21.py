"""Deterministic Fair Play v21 selection and public-data performance context.

No punishment labels, account identity, or raw Accuracy-derived accusations.
The extra discovery set combines contiguous changes and representative controls;
it never selects only games with unusually high Chess.com Accuracy.
"""
from __future__ import annotations
import math
import statistics
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewPlan:
    primary: tuple
    core: tuple
    reserve: tuple
    metadata_games: int
    primary_limit: int
    # Last-20 non-peer games must not silently disappear because the opponent
    # is >500 Elo weaker. They consume EXISTING extra-discovery slots.
    recent_tail: tuple = ()

    @property
    def deep(self):
        return self.core+self.reserve+self.recent_tail


def is_peer_game(game, *, max_lower_gap=500):
    """Only use rated games with a known, not >500-Elo-weaker opponent."""
    return (getattr(game,"rated",None) is True
            and isinstance(getattr(game,"rating",None),int)
            and isinstance(getattr(game,"opponent_rating",None),int)
            and game.opponent_rating >= game.rating-max_lower_gap
            and not getattr(game,"probe_only",False))


def broad_and_core(history, *, broad_count=500, deep_count=100):
    """Fill 100 with older rated peers, without losing broad 500-game coverage.

    A synthetic fixture may have fewer eligible games. Never invent matches,
    include casual/unknown-rated games, or duplicate a single game.
    """
    chronological=sorted((g for g in history if g.rated is True
                          and not getattr(g,"probe_only",False)),
                         key=lambda g:(g.ended,g.identity))
    peers=[g for g in reversed(chronological) if is_peer_game(g)]
    core=tuple(reversed(peers[:max(0,deep_count)]))
    ids={g.identity for g in core}
    newest=[g for g in reversed(chronological) if g.identity not in ids]
    rest=list(reversed(newest[:max(0,broad_count-len(core))]))
    primary=tuple(sorted((*rest,*core),key=lambda g:(g.ended,g.identity)))
    return ReviewPlan(primary,core,(),len(chronological),broad_count)


def discovery_extras(plan, *, max_extra=25):
    """Stratified extension: recent tail, suspicious periods and controls.

    Fast PV1 gives CPL and top1 but no candidate spread, so it must not be
    described as critical/human anomaly proof. Selection does NOT use Chess.com
    Accuracy or the existing outcome's cheating label.
    """
    core={g.identity for g in plan.core}
    # Selection is chronological and outcome/Accuracy-blind. The last twenty
    # rated games are ALWAYS eligible for full-depth work even if the opponent
    # was >500 Elo weaker or rating metadata is missing. The 100/200 peer core
    # remains unchanged, while these missing recent games use up-to-20 slots
    # of the pre-existing 25/50 stratified EXTRA budget.
    recent=sorted((g for g in plan.primary if g.rated is True
                   and not getattr(g, "probe_only", False)),
                  key=lambda g:(g.ended,g.identity))[-20:]
    missing=[g for g in recent if g.identity not in core]
    tail=tuple(missing[-min(len(missing),max(0,max_extra)):])
    tail_ids={g.identity for g in tail}
    older=[g for g in plan.primary
           if g.identity not in core and g.identity not in tail_ids
           and is_peer_game(g)]
    extra_budget=max(0,max_extra-len(tail))
    if not older or extra_budget<=0:
        return ReviewPlan(plan.primary,plan.core,(),
                          plan.metadata_games,plan.primary_limit,tail)
    # Walk contiguous same-control windows, emphasizing persistent *within*
    # window relative changes, not highest raw wins/Accuracy. Signals are
    # discovery-only and cannot earn HIGH without full-depth confirmation.
    groups={}
    for game in older:
        groups.setdefault((game.time_class,game.time_control),[]).append(game)
    candidates=[]
    for key,rows in sorted(groups.items()):
        rows=sorted(rows,key=lambda g:(g.ended,g.identity))
        for i in range(0,len(rows),5):
            window=rows[i:i+5]
            if len(window)<3:continue
            measured=[(g,(g.fast_metrics or g.metrics)) for g in window]
            quality=[1-float(m["robust_cpl"])/150 for g,m in measured
                     if isinstance(m.get("robust_cpl"),(int,float))
                     and math.isfinite(m["robust_cpl"])]
            if len(quality)<3:continue
            # Rank *whole windows*, not individual cherry-picked wins.
            score=sum(quality)/len(quality)
            candidates.append((score,key,window))
    # Half discovery, half evenly-spaced controls; overlapping findings count
    # once. Controls deliberately preserve both errors and strong games.
    allocation=min(extra_budget,len(older))
    discovery_limit=allocation//2
    controls=allocation-discovery_limit
    ordered=[]
    seen=set()
    for _,_,window in sorted(candidates,key=lambda item:(-item[0],item[1])):
        for game in window:
            if len(ordered)>=discovery_limit:break
            if game.identity in seen:continue
            ordered.append(game);seen.add(game.identity)
        if len(ordered)>=discovery_limit:break
    # Choose deterministic chronological quantiles from *unselected* history.
    remaining=[g for g in older if g.identity not in seen]
    if controls and remaining:
        positions=[min(len(remaining)-1, int((i+.5)*len(remaining)/controls))
                   for i in range(controls)]
        for index in positions:
            game=remaining[index]
            if game.identity not in seen:
                ordered.append(game);seen.add(game.identity)
    # Backfill if the windows were too small or quantiles collided.
    for game in reversed(older):
        if len(ordered)>=allocation:break
        if game.identity not in seen:ordered.append(game);seen.add(game.identity)
    # Deep scope deliberately includes all selected misses and weak games.
    return ReviewPlan(plan.primary,plan.core,tuple(sorted(ordered,
                      key=lambda g:(g.ended,g.identity))),plan.metadata_games,
                      plan.primary_limit,tail)


WINDOWS=(7,30,90,365)


def public_history_stats(history, *, now=None, public_stats=None):
    """Time windows are based only on verified rated games actually collected.

    Missing official Accuracy is unknown, not zero; sample size and date
    completeness remain visible. Best rated win is derived from games, while
    API rating 'best' refers to the player's own historical rating.
    """
    now=time.time() if now is None else now
    result={"schema":"sharkbot-public-history-v1",
            "source":"official Chess.com game archives; bounded historical sample",
            "games_available":len(history),"periods":{},"all_time":{}}
    for days in WINDOWS:
        rows=[g for g in history if g.rated is True and now-days*86400<g.ended<=now]
        classes={}
        for kind in ("blitz","rapid","bullet"):
            games=sorted((g for g in rows if g.time_class==kind),
                         key=lambda g:(g.ended,g.identity))
            wins=sum(g.result=="Win" for g in games)
            draws=sum(g.result=="Draw" for g in games)
            losses=sum(g.result=="Loss" for g in games)
            valid=[g for g in games if isinstance(g.rating,int)
                   and isinstance(g.opponent_rating,int)]
            expected=sum(1/(1+10**((g.opponent_rating-g.rating)/400))
                         for g in valid)
            actual=sum(g.score for g in valid)
            accuracies=[g.accuracy for g in games if isinstance(g.accuracy,(int,float))
                        and math.isfinite(g.accuracy) and 0<=g.accuracy<=100]
            streak=record=0
            for g in games:
                streak=streak+1 if g.result=="Win" else 0
                record=max(record,streak)
            ratings=[g.rating for g in games if isinstance(g.rating,int)]
            stronger_wins=[g for g in valid if g.result=="Win"]
            best_win=max(stronger_wins,key=lambda g:g.opponent_rating,
                         default=None)
            classes[kind]={
                "rated_games":len(games),"wins":wins,"draws":draws,"losses":losses,
                "points_vs_elo_expected":round(actual-expected,3) if valid else None,
                "elo_expected_games":len(valid),
                "rating_change":ratings[-1]-ratings[0] if len(ratings)>=2 else None,
                "longest_observed_win_streak":record,
                "best_win_opponent_rating":(best_win.opponent_rating
                                            if best_win else None),
                "official_accuracy_coverage":len(accuracies),
                "official_accuracy_mean":round(statistics.mean(accuracies),2)
                    if accuracies else None,
                "official_accuracy_90_plus":sum(v>=90 for v in accuracies),
                "warning":"Incomplete archives, partial historical sample, and selected Game Reviews can bias trends."
            }
        result["periods"][str(days)+"d"]=classes
    if isinstance(public_stats,dict):
        # No username or hidden status. Only published rating best/current.
        for kind in ("chess_blitz","chess_rapid","chess_bullet"):
            row=public_stats.get(kind)
            if not isinstance(row,dict):continue
            result["all_time"][kind]={
                name:obj.get("rating") for name,obj in
                ((key,row.get(key)) for key in ("last","best"))
                if isinstance(obj,dict) and isinstance(obj.get("rating"),int)}
    return result
