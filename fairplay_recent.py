"""Read-only last-20 rated game audit, independent of calendar date.

Result streaks against grossly mismatched opponents must not be mistaken for
engine-assisted moves. No personal identifiers or private PGNs are emitted.
"""
from __future__ import annotations

import math

SCHEMA = "sharkbot-recent-tail-audit-v1"


def _stat(games):
    wins = sum(g.result == "Win" for g in games)
    draws = sum(g.result == "Draw" for g in games)
    losses = sum(g.result == "Loss" for g in games)
    known = [(g.rating, g.opponent_rating) for g in games
             if isinstance(g.rating, int) and not isinstance(g.rating, bool)
             and isinstance(g.opponent_rating, int)
             and not isinstance(g.opponent_rating, bool)
             and 100 <= g.rating <= 4000 and 100 <= g.opponent_rating <= 4000]
    expected = sum(1/(1+10**((opponent-own)/400)) for own,opponent in known)
    return {
        "games": len(games), "wins": wins, "draws": draws, "losses": losses,
        "deep_reviewed": sum(bool(g.deep) for g in games),
        "peer_opponent_games": sum(opponent >= own-500 for own,opponent in known),
        "opponent_more_than_500_weaker": sum(opponent < own-500 for own,opponent in known),
        "known_ratings": len(known),
        "elo_expected_points": round(expected, 2),
        "actual_points_rating_known": sum(
            g.score for g in games if
            isinstance(g.rating,int) and isinstance(g.opponent_rating,int)
            and not isinstance(g.rating,bool) and not isinstance(g.opponent_rating,bool)
            and 100 <= g.rating <= 4000 and 100 <= g.opponent_rating <= 4000),
        "engine_useful_decisions": sum(
            int((getattr(g, "fast_metrics", None) or getattr(g,"metrics",{}) or {})
                .get("decisions",0) or 0) for g in games),
        "deep_critical_positions": sum(
            int((getattr(g,"metrics",{}) or {}).get("critical",0) or 0)
            for g in games if g.deep),
    }


def summarize_recent(games):
    unique = {}
    for g in games:
        if (g.rated is True and not getattr(g, "probe_only", False)
                and isinstance(g.ended, (int,float))
                and math.isfinite(g.ended)):
            unique[g.identity]=g
    recent = sorted(unique.values(), key=lambda g:(g.ended,g.identity))[-20:]
    return {
        "schema":SCHEMA,
        "scoring_influence":False,
        "last_20":_stat(recent),
        "last_10":_stat(recent[-10:]),
        "note":(
            "Elo expectations are descriptive under a simple model, not "
            "a cheating probability. Wins, very low opponent ratings and "
            "short games alone cannot establish engine assistance. "
            "A deep-reviewed game may still contain zero competitive moves."),
    }
