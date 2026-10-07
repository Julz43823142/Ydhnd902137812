"""Continuous position value; legal-count alone never establishes difficulty."""
import math
import statistics
from fairplay_config import CONFIG


def clamp(value):return max(0.0,min(1.0,value))


def annotate(decision, config=CONFIG):
    m=decision.metrics
    if not m.get('useful') or m.get('easy_conversion') or m.get('post_opponent_error'):
        m.update(difficulty=0.0,human_information=0.0,human_expectedness=None,high_information=False)
        return
    values=m.get('candidate_cp',[])
    # Separation of a small good-move set from worse plausible choices also
    # matters when the actual move is #2/#3. Equivalent #1 choices are not rare.
    good=sum(values[0]-v<=config.equivalent_cp for v in values) if values else m.get('equivalent_candidates',1)
    boundary=max((values[i]-values[i+1] for i in range(len(values)-1)
                  if values[0]-values[i]<=50),default=m.get('gap') or 0)
    choices=clamp(math.log(max(2,decision.legal))/math.log(35))
    separation=clamp(boundary/200)
    spread=clamp((m.get('spread') or 0)/400)
    quiet=not (decision.capture or decision.check or decision.gives_check)
    difficulty=choices*(.55*separation+.45*spread)*(1 if quiet else .75)
    # Many equivalent candidates make an engine match uninformative.
    difficulty*=1/max(1,good)**config.human_equivalence_power
    difficulty*=1 if m.get('competitive') else .5
    m.update(difficulty=clamp(difficulty),plausible_good_moves=good,played_boundary_cp=boundary)


def stability(decision, config=CONFIG):
    fast=decision.fast_engine;m=decision.metrics
    compared=m.get('nodes',0)>=config.deep_nodes and fast.get('nodes',0)==config.fast_nodes
    rank=m.get('rank');old=fast.get('rank')
    best=compared and m.get('best')==fast.get('best')
    rank_ok=compared and rank is not None and old is not None and abs(rank-old)<=1
    cpl_ok=compared and abs(m.get('cpl',1000)-fast.get('cpl',1000))<=30
    gap_ok=compared and abs((m.get('played_boundary_cp') or 0)-(fast.get('played_boundary_cp') or 0))<=max(50,(fast.get('played_boundary_cp') or 0)*.5)
    stable=bool(compared and rank_ok and cpl_ok and gap_ok and
                (best or (m.get('near_best') and fast.get('near_best'))))
    m.update(search_stability={'compared':bool(compared),'best':bool(best),'rank':bool(rank_ok),
        'cpl':bool(cpl_ok),'gap':bool(gap_ok),'stable':stable})
    return stable


def loss_summary(rows):
    values=sorted(m['scaled_loss'] for m in rows if m.get('scaled_loss') is not None)
    return {'median':statistics.median(values) if values else None,
        'trimmed_mean':statistics.mean(values[:max(1,math.ceil(len(values)*.9))]) if values else None,
        'p90':values[min(len(values)-1,int((len(values)-1)*.9))] if values else None}
