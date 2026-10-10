"""Read-only Fair Play depth-pair quality diagnostics.

This audit is computed after the one authoritative verdict. It does not feed
ranking, create a new evidence family, or turn descriptive search noise into
proof of misconduct. It distinguishes unmeasured depth pairs from measured
but unstable ones by time control and the selected chronological candidate.
"""
from collections import Counter

SCHEMA = "sharkbot-paired-stability-audit-v2"
ALLOWED = frozenset({
    "unverified_depth_or_fast_contract",
    "candidate_quality_changed",
    "evidential_geometry_changed",
    "rank_or_best_changed",
    "played_move_no_longer_equivalent",
})


def summarise_stability(games, period_ids=()):
    ids=set(period_ids or ())
    buckets={"blitz":Counter(),"rapid":Counter(),"bullet":Counter(),
             "selected_period":Counter()}
    reasons={key:Counter() for key in buckets}
    for game in games:
        if not getattr(game,"deep",False):
            continue
        destinations=[game.time_class]
        if game.identity in ids:
            destinations.append("selected_period")
        for decision in game.decisions:
            metrics=getattr(decision,"metrics",{}) or {}
            if not metrics.get("useful"):
                continue
            record=metrics.get("search_stability") or {}
            if not isinstance(record,dict):
                record={}
            for kind in destinations:
                if kind not in buckets:
                    continue
                row=buckets[kind]
                row["eligible"]+=1
                if record.get("compared") is True:
                    row["compared"]+=1
                    # These are separate *descriptive* predicates: the played
                    # move can retain quality while the position becomes easy.
                    if record.get("objective_quality_preserved") is True:
                        row["objective_quality_preserved"]+=1
                    if record.get("evidential_geometry_preserved") is True:
                        row["evidential_geometry_preserved"]+=1
                    if (record.get("objective_quality_preserved") is True
                            and record.get("evidential_geometry_preserved") is True):
                        row["both_quality_and_geometry_preserved"]+=1
                    if record.get("stable") is True:
                        row["stable"]+=1
                    else:
                        row["unstable"]+=1
                else:
                    row["unmeasured"]+=1
                for reason in record.get("blockers",[]) or []:
                    if reason in ALLOWED:
                        reasons[kind][reason]+=1
    summary={}
    for kind,row in buckets.items():
        compared=row["compared"]
        summary[kind]={
            "eligible":row["eligible"],"compared":compared,
            "stable":row["stable"],"unstable":row["unstable"],
            "objective_quality_preserved":row["objective_quality_preserved"],
            "evidential_geometry_preserved":row["evidential_geometry_preserved"],
            "both_quality_and_geometry_preserved":row["both_quality_and_geometry_preserved"],
            "unmeasured":row["unmeasured"],
            "comparable_fraction":round(compared/max(1,row["eligible"]),4),
            "stable_among_compared":(round(row["stable"]/compared,4)
                                     if compared else None),
            "quality_preserved_among_compared":(round(row["objective_quality_preserved"]/compared,4)
                                                 if compared else None),
            "geometry_preserved_among_compared":(round(row["evidential_geometry_preserved"]/compared,4)
                                                  if compared else None),
            "reasons":dict(sorted(reasons[kind].items())),
        }
    return {"schema":SCHEMA,"scoring_influence":False,
            "scope":"complete selected deep sample and selected chronological period",
            "classes":{k:summary[k] for k in ("blitz","rapid","bullet")},
            "selected_period":summary["selected_period"],
            "note":"Quality preservation and evidence-grade geometry are different. "
                   "Missing comparisons are unknown, not evidence of fair play. "
                   "This audit never changes a player's priority."}
