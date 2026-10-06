"""Conservative same-period result support, never a cheating probability.

Hoeffding's bound applies to bounded scores (including draws) under independent
games and reliable Elo expectations. Both assumptions can fail for underrated
players, correlated sessions and rapid legitimate improvement. This is weak
support, capped at .65; no bans, titles or API accuracy enter the calculation.
"""
import math
from fairplay_config import CONFIG


def result_support(games, config=CONFIG):
    rows=[g for g in games if g.rated is True and g.rating is not None
          and g.opponent_rating is not None and 0<=g.score<=1]
    n=len(rows)
    if n<config.result_support_min_games:return {'games':n,'score':0,'bound':None}
    # Give the subject a sizeable underrated-player allowance. Clean expected
    # wins against weaker opposition should not become corroborating evidence.
    expected=sum(1/(1+10**(max(-4000,min(4000,g.opponent_rating-g.rating-config.result_rating_margin))/400)) for g in rows)
    actual=sum(g.score for g in rows);excess=max(0,actual-expected)
    log_bound=-2*excess*excess/n
    strength=max(0,min(1,(-log_bound-math.log(200))/(math.log(100000)-math.log(200))))*.65
    return {'games':n,'score':strength,'bound':math.exp(log_bound),
            'expected_with_margin':expected,'actual':actual,'rating_margin':config.result_rating_margin}
