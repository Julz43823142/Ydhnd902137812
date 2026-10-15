"""Replicated selective human-policy discrepancies, capped at MODERATE.

Original implementation inspired by Irwin's ordered features and ChessFraud's
paired move comparisons. No imported model weights or independent probability
claims. Six-decision blocks are fixed BEFORE looking at hits. All misses in a
block remain in its signed mean; incomplete blocks cannot supply evidence.
"""
import math
import statistics
from fairplay_policy import POLICY

BLOCK = 6
MIN_HITS = 3


def _rows(game, fast):
    from fairplay_maia import policy_evidence
    result=[]
    for decision in game.decisions:
        row=(decision.fast_policy if fast else
             policy_evidence(decision,decision.human_policy) if decision.human_policy else None)
        if not isinstance(row,dict) or row.get('eligible') is not True or row.get('counterfactual_complete') is not True:
            result.append(None);continue
        keys=('observed_quality','expected_quality_upper_bound','signed_quality_excess','information',
              'played_move_probability','known_policy_mass','reference_weight')
        if not all(isinstance(row.get(k),(int,float)) and not isinstance(row[k],bool)
                   and math.isfinite(row[k]) for k in keys):
            result.append(None);continue
        if not (0<=row['observed_quality']<=1 and 0<=row['expected_quality_upper_bound']<=1
                and 0<=row['played_move_probability']<=1 and -1<=row['signed_quality_excess']<=1
                and 0<=row['information']<=1 and POLICY.known_mass<=row['known_policy_mass']<=1.000001
                and 0<row['reference_weight']<=1):
            result.append(None);continue
        result.append(row)
    return result


def _hit(row):
    return (row['played_move_probability']<=.10 and row['observed_quality']>=.85
            and row['signed_quality_excess']>=POLICY.move_excess
            and row['information']>=POLICY.game_information)


def _block(rows):
    if len(rows)!=BLOCK or any(row is None for row in rows):return None
    hits=sum(_hit(row) for row in rows)
    signed=statistics.mean(row['signed_quality_excess'] for row in rows)
    information=statistics.mean(row['information'] for row in rows)
    return {'hits':hits,'signed_excess':signed,'information':information,
            'passed':hits>=MIN_HITS and signed>=POLICY.game_signed_excess
                     and information>=POLICY.game_information}


def _structural_exclusion(decision):
    # Only verified structural context may leave the opportunity sequence.
    # Missing model/search data remains a gap. Deep results never choose the
    # sequence: engine-based exclusions use the immutable FAST snapshot only.
    fast=getattr(decision,'fast_engine',{}) or {}
    return bool(decision.forced or decision.trivial_kind or decision.phase=='opening'
                or fast.get('easy_conversion') or fast.get('automatic_material_gain')
                or fast.get('simple_threat_response'))


def game_episode(game):
    # One fast-selected fixed block per game. A long game receives no greater
    # account-level weight; its additional searched blocks are disclosed.
    raw_fast=_rows(game,True)
    indices=[i for i,d in enumerate(game.decisions) if not _structural_exclusion(d)]
    fast=[raw_fast[i] for i in indices];candidates=[]
    for start in range(0,len(fast)-BLOCK+1,BLOCK):
        summary=_block(fast[start:start+BLOCK])
        if summary and summary['passed']:candidates.append((start,summary))
    base={'blocks_searched':len(fast)//BLOCK,'contributor':False,'deep_confirmed':False}
    if not candidates:return base
    start,summary=max(candidates,key=lambda item:(item[1]['signed_excess'],item[1]['hits'],-item[0]))
    chosen=indices[start:start+BLOCK]
    decisions=[game.decisions[i] for i in chosen]
    keys={(' '.join(d.fen.split()[:4]),d.move)
          for d,row in zip(decisions,fast[start:start+BLOCK]) if _hit(row)}
    if len(keys)<MIN_HITS:return base
    base.update(contributor=True,indices=chosen,fast=summary,position_keys=keys)
    if not game.deep:return base
    raw_deep=_rows(game,False)
    deep=[raw_deep[i] for i in chosen];confirmed=_block(deep)
    if confirmed is None:return base
    paired=[isinstance(d.metrics.get('search_stability'),dict)
               and d.metrics['search_stability'].get('objective_quality_preserved') is True
               and abs(new['observed_quality']-old['observed_quality'])<=.10
               and abs(new['expected_quality_upper_bound']-old['expected_quality_upper_bound'])<=.15
               for d,new,old in zip(decisions,deep,fast[start:start+BLOCK])]
    stable=sum(paired)
    retained={(' '.join(d.fen.split()[:4]),d.move)
              for d,new,old,ok in zip(decisions,deep,fast[start:start+BLOCK],paired)
              if ok and _hit(new) and _hit(old)}
    base.update(deep=confirmed,stable=stable,position_keys=retained,
                deep_confirmed=bool(confirmed['passed'] and len(retained)>=MIN_HITS and stable>=math.ceil(BLOCK*POLICY.deep_stability)
                    and confirmed['information']>=POLICY.retention*summary['information']))
    return base


def integrate(result,games,config):
    from fairplay_sequence import consecutive_class_games
    eligible=sorted((g for g in games if g.rated is True and g.time_class in ('rapid','blitz')),
                    key=lambda g:(g.ended,g.identity))
    # Duplicate receipts or reloaded aliases cannot manufacture replication.
    unique={g.identity:g for g in eligible};eligible=list(unique.values())
    episodes={g.identity:game_episode(g) for g in eligible}
    best=None;examined=0
    for kind in ('rapid','blitz'):
        group=[g for g in eligible if g.time_class==kind]
        seen=set()
        for width in POLICY.windows:
            if width>len(group):continue
            for offset in sorted({*range(0,len(group)-width+1,width),len(group)-width}):
                members=group[offset:offset+width];ids=tuple(g.identity for g in members)
                if ids in seen:continue
                seen.add(ids);examined+=1
                rows=[episodes[i] for i in ids]
                required=max(POLICY.contributor_games,math.ceil(len(rows)*POLICY.contributor_fraction))
                fast=sum(r['contributor'] for r in rows);deep=sum(r['deep_confirmed'] for r in rows)
                half=len(rows)//2
                distinct=set().union(*(r.get('position_keys',set()) for r in rows if r['deep_confirmed']))
                tests={
                    'twelve distinct confirmed decisions':len(distinct)>=12,
                    'chronology covered':all(consecutive_class_games(a,b) for a,b in zip(members,members[1:])),
                    'six replicated fast contributors':fast>=required,
                    'six replicated deep contributors':deep>=required,
                    'deep evidence in both period halves':all(sum(r['deep_confirmed'] for r in part)>=2
                                                             for part in (rows[:half],rows[half:])),
                    'required engine coverage complete':not result.partial and result.coverage.get('primary_engine_complete') is True
                        and result.coverage.get('required_deep_complete') is True,
                    'learned reference enabled':not {'human','neural'}&set(config.disabled_features)}
                candidate={'games':len(rows),'class':kind,'contributors':fast,'deep_contributors':deep,
                           'passed':all(tests.values()),'gates':tests}
                if best is None or (candidate['passed'],deep,fast)>(best['passed'],best['deep_contributors'],best['contributors']):best=candidate
    result.diagnostics['selective_policy_episodes']={
        'schema':'sharkbot-selective-policy-episodes-v1','best':best,'periods_examined':examined,
        'blocks_examined':sum(r['blocks_searched'] for r in episodes.values()),
        'contributor_games':sum(r['contributor'] for r in episodes.values()),
        'deep_contributor_games':sum(r['deep_confirmed'] for r in episodes.values()),
        'priority_cap':'MODERATE','calibrated_probability':False,
        'note':'Fixed six-decision blocks include misses; fast selects the same block for deep checking. Related gameplay evidence; no independent vote. No claim of held-out detector accuracy.'}
    if best and best['passed'] and result.priority in ('LOW','INSUFFICIENT DATA'):
        result.priority='MODERATE'
        result.diagnostics['moderate_path']='Replicated selective Maia-policy episodes'
        result.reasons=[
            'Human-model-unusual strong choices recurred in fixed short sequences across at least six games and survived paired deep review.',
            'This localized gameplay discrepancy is capped at MODERATE. Maia rarity is not proof; human review is mandatory.']
    return result
