"""Read-only accounting for missing versus resolved human-reference evidence."""
import statistics
from fairplay_policy import _needs_counterfactual, POLICY
from fairplay_maia import policy_evidence


def summarize(games):
    total=comparable=low_mass=incomplete=invalid=0
    masses=[]
    for game in games:
        if game.rated is not True or game.time_class=='daily':continue
        for decision in game.decisions:
            if not _needs_counterfactual(decision):continue
            total+=1
            row=policy_evidence(decision,decision.human_policy)
            if row is None:invalid+=1;continue
            mass=row['known_policy_mass'];masses.append(mass)
            if not row['counterfactual_complete']:incomplete+=1
            elif mass<POLICY.known_mass:low_mass+=1
            else:comparable+=1
    return {'eligible':total,'comparable':comparable,'low_mass':low_mass,
            'incomplete':incomplete,'invalid':invalid,
            'median_mass':statistics.median(masses) if masses else None,
            'scoring_influence':False}
