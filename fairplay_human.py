"""Bounded, uncalibrated human expectedness alongside legacy engine metrics.

This fallback is NOT a learned probability. Rating bands describe reference
expectations, not innocence. Related rank/CPL/difficulty information is a single
gameplay family. All missed opportunities stay in its denominators.
"""
import statistics
from typing import Protocol
from fairplay_config import CONFIG
from fairplay_calibration import lower_bound
from fairplay_difficulty import annotate, clamp, stability, loss_summary


class HumanMoveModel(Protocol):
    name: str
    def expectedness(self, decision, rating: int | None) -> float: ...


class HeuristicHumanModel:
    name='rating-conditioned heuristic (not probability)'

    def expectedness(self, decision, rating):
        # Elite precision is expected. Missing ratings cannot supply an absolute
        # anomaly. These conservative reference values need held-out calibration.
        strength=clamp(((rating if rating is not None else 2800)-400)/2400)
        difficulty=decision.metrics.get('difficulty',0)
        return clamp(.45+.40*strength+.12*(1-difficulty))


FALLBACK=HeuristicHumanModel()


def annotate_game(game, config=CONFIG, model=None):
    model=model or FALLBACK;failures=0
    for d in game.decisions:
        annotate(d,config);m=d.metrics
        if not m.get('useful'):
            continue
        try:
            expected=float(model.expectedness(d,game.rating))
            if not 0<=expected<=1:raise ValueError('Invalid bounded expectedness')
        except Exception:
            failures+=1;expected=FALLBACK.expectedness(d,game.rating)
        potential=clamp(2*(1-expected)*m['difficulty'])
        quality=clamp(1-m.get('cpl',1000)/100)
        # Opportunity geometry must not depend on a rating-dependent score
        # ceiling. The former potential>=.55 filter made absolute evidence
        # mathematically unavailable above roughly 2050 even with perfect play.
        # Strength is assessed at period level, not used to erase positions.
        opportunity=(m.get('competitive',False) and m['difficulty']>=config.human_difficulty_floor
                     and not m.get('search_inconsistent'))
        m.update(human_expectedness=expected,human_information=potential*quality,
            human_opportunity=bool(opportunity),high_information=bool(opportunity and quality>=.85),
            human_model=FALLBACK.name if failures else model.name)
        stability(d,config)
    rows=[d.metrics for d in game.decisions if d.metrics.get('useful')]
    ranks={str(k):sum(m.get('rank')==k for m in rows) for k in range(1,6)}
    ranks['outside_top5']=sum(m.get('rank') is None and m.get('candidate_count',0)>=5 for m in rows)
    ranks['outside_observed']=sum(m.get('rank') is None and m.get('candidate_count',0)<5 for m in rows)
    opportunities=[m for m in rows if m.get('human_opportunity')]
    # Uniform positions, never the highest successful hits; one marathon game
    # cannot dominate the account evidence.
    capped=opportunities
    if len(capped)>config.human_game_cap:
        capped=[capped[round(i*(len(capped)-1)/(config.human_game_cap-1))] for i in range(config.human_game_cap)]
    buckets={name:[m for m in rows if lo<=m.get('difficulty',0)<hi]
             for name,lo,hi in [('easy',0,.3),('medium',.3,.55),('hard',.55,1.01)]}
    curve={name:{'count':len(v),'near_best':sum(m.get('near_best',False) for m in v)/len(v) if v else None}
           for name,v in buckets.items()}
    easy,hard=buckets['easy'],buckets['hard']
    inversion=(len(easy)>=6 and len(hard)>=6 and
               curve['hard']['near_best']-curve['easy']['near_best']>=.25)
    stable=[m for m in capped if m.get('search_stability',{}).get('stable')]
    hard=[m for m in rows if m.get('difficulty',0)>=config.human_difficulty_floor and m.get('competitive')]
    if len(hard)>config.human_game_cap:
        hard=[hard[round(i*(len(hard)-1)/(config.human_game_cap-1))] for i in range(config.human_game_cap)]
    result={'model':model.name,'model_failures':failures,'rank':ranks,
        'opportunities':len(capped),'raw_opportunities':len(opportunities),
        'hits':sum(m.get('high_information',False) for m in capped),
        'stable_opportunities':len(stable),'stable_hits':sum(m.get('high_information',False) for m in stable),
        'hard_opportunities':len(hard),'hard_hits':sum(m.get('cpl',1000)<=15 for m in hard),
        'hard_stable':sum(m.get('search_stability',{}).get('stable',False) for m in hard),
        'information':statistics.mean(m.get('human_information',0) for m in hard) if hard else 0,
        'quality_reference':statistics.mean(m['human_expectedness'] for m in hard) if hard else None,
        'difficulty_curve':curve,'difficulty_inversion':bool(inversion),
        'competitive_scaled_loss':loss_summary([m for m in rows if m.get('competitive')]),
        'scaled_loss':loss_summary(rows),'critical_scaled_loss':loss_summary([m for m in rows if m.get('critical')])}
    game.metrics['human']=result
    return result


def period_summary(games, config=CONFIG, *, fast=False):
    rows=[(g,(g.fast_metrics or g.metrics if fast else g.metrics).get('human',{})) for g in games]
    n=sum(m.get('opportunities',0) for g,m in rows);hits=sum(m.get('hits',0) for g,m in rows)
    contributors=[g for g,m in rows if m.get('hits',0)>=1
                  and m.get('information',0)>=config.human_absolute_information_floor]
    hard_n=sum(m.get('hard_opportunities',0) for g,m in rows)
    hard_hits=sum(m.get('hard_hits',0) for g,m in rows)
    rated=[g for g in games if g.rating is not None]
    # Aggregate at game level. Information is bounded to [0,1] for every game.
    return {'games':len(games),'opportunities':n,'hits':hits,
        'hit_lower':lower_bound(hits/n if n else None,n,config.rate_lower_bound_z),
        'contributors':len(contributors),'contributor_ids':[g.identity for g in contributors],
        'information':statistics.mean(m.get('information',0) for g,m in rows if m.get('hard_opportunities',0))
            if any(m.get('hard_opportunities',0) for g,m in rows) else 0,
        'opportunity_games':sum(m.get('opportunities',0)>0 for g,m in rows),
        'stable_opportunities':sum(m.get('stable_opportunities',0) for g,m in rows),
        'stable_hits':sum(m.get('stable_hits',0) for g,m in rows),
        'hard_opportunities':hard_n,'hard_hits':hard_hits,
        'hard_lower':lower_bound(hard_hits/hard_n if hard_n else None,hard_n,config.rate_lower_bound_z),
        'hard_contributors':sum(m.get('hard_hits',0)>=1 for g,m in rows),
        'hard_stable':sum(m.get('hard_stable',0) for g,m in rows),
        'rating_coverage':len(rated)/len(games) if games else 0,
        'rating_reference':statistics.median(g.rating for g in rated) if rated else None,
        'quality_reference':statistics.mean(m['quality_reference'] for g,m in rows if m.get('quality_reference') is not None)
            if any(m.get('quality_reference') is not None for g,m in rows) else None,
        'inversion_games':sum(m.get('difficulty_inversion',False) for g,m in rows)}


def absolute_qualified(summary, config=CONFIG):
    # Ten-game reports can qualify from distributed opportunities, rather than
    # an impossible fixed 300-move minimum. One perfect game cannot qualify.
    return bool(summary['games']>=config.min_games and summary['rating_coverage']>=.8
        and summary['opportunities']>=config.human_min_opportunities
        and summary['contributors']>=max(config.human_min_contributors,summary['opportunity_games']*.5)
        and summary['information']>=config.human_absolute_information_floor
        and summary['quality_reference'] is not None
        and summary['hit_lower']>=max(config.human_hit_lower,summary['quality_reference']+config.human_expectation_margin))


def evidence_funnel(games, config=CONFIG):
    counts={key:0 for key in ['parsed','book','forced','trivial','easy_conversion','search_unstable',
                            'engine_useful','competitive','high_difficulty','critical','unique','high_information']}
    for game in games:
        for d in game.decisions:
            m=d.metrics;counts['parsed']+=1
            counts['book']+=d.phase=='opening';counts['forced']+=d.forced
            counts['trivial']+=bool(d.trivial_kind);counts['easy_conversion']+=bool(m.get('easy_conversion') or m.get('automatic_material_gain'))
            counts['search_unstable']+=bool(m.get('search_inconsistent') or
                (m.get('search_stability',{}).get('compared') and not m['search_stability']['stable']))
            for key,flag in [('engine_useful',m.get('useful')),('competitive',m.get('competitive')),
                ('high_difficulty',m.get('difficulty',0)>=config.human_difficulty_floor),('critical',m.get('critical')),
                ('unique',m.get('unique')),('high_information',m.get('high_information'))]:counts[key]+=bool(flag)
    return counts
