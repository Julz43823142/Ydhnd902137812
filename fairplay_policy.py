"""Counterfactual human-policy quality. No calibrated misconduct probabilities."""
import math
import statistics
import time
from concurrent.futures import wait, FIRST_COMPLETED
from dataclasses import dataclass
import chess
import chess.engine
from fairplay_config import CONFIG
from fairplay_data import check_deadline

@dataclass(frozen=True)
class PolicyConfig:
    alternatives: int = 3
    target_mass: float = .90
    minimum_move_mass: float = .025
    difficulty: float = .45
    known_mass: float = .65
    quality_margin: float = .10  # domain/model uncertainty, not a calibrated CI
    move_excess: float = .20
    game_decisions: int = 4  # one full game-equivalent of evidence
    minimum_game_decisions: int = 2
    game_hits: int = 2
    game_signed_excess: float = .18
    game_information: float = .12
    contributor_games: int = 6
    contributor_fraction: float = .60
    decisions: int = 24
    deep_games: int = 4
    deep_decisions: int = 16
    deep_stability: float = .75
    retention: float = .75
    windows: tuple = (6, 10, 20, 50)
POLICY = PolicyConfig()

def alternatives(d, config=POLICY):
    """Choose roots by human probability mass, never by the played result."""
    policy, m = d.human_policy, d.metrics
    if not policy or not m.get('competitive') or not m.get('useful'):return []
    known=set(m.get('candidates', []))
    if 'actual_cp' in m:known.add(d.move)
    mass=sum(policy.get(move,0) for move in known)
    selected=[]
    for move,probability in sorted(policy.items(),key=lambda item:(-item[1],item[0])):
        if mass>=config.target_mass or len(selected)>=config.alternatives:break
        if move in known:continue
        if probability<config.minimum_move_mass:break
        selected.append(move);mass+=probability
    return selected

def search_alternatives(scanner,d,nodes):
    from fairplay_analysis import score_cp, scan_engine_timeout
    values={};board=chess.Board(d.fen)
    depth_search=isinstance(nodes,chess.engine.Limit) and nodes.depth is not None
    limit=nodes if depth_search else chess.engine.Limit(nodes=nodes)
    for uci in alternatives(d):
        check_deadline(scanner.deadline)
        move=chess.Move.from_uci(uci)
        if move not in board.legal_moves:raise ValueError('Invalid local policy root')
        if 'Clear Hash' in scanner.engine.options:scanner.engine.configure({'Clear Hash':None})
        # Root searches are independent tasks and must not inherit a fast
        # position's stale timeout when the budget is depth-18 or deep nodes.
        scanner.engine.timeout=scan_engine_timeout(
            limit,scanner.config,retry=getattr(scanner,'retry_after_timeout',False))
        scanner.last_search={'phase':'maia-counterfactual','budget':nodes.depth if depth_search else nodes,
                             'multipv':1,'started':time.monotonic(),
                             'timeout_seconds':scanner.engine.timeout}
        started=time.monotonic()
        line=scanner.engine.analyse(board,limit,root_moves=[move])
        if (depth_search and (not isinstance(line,dict) or
                int(line.get('depth',0) or 0)<nodes.depth)):
            raise chess.engine.EngineError('Counterfactual did not reach the requested depth')
        if (line.get('lowerbound') or line.get('upperbound') or not line.get('pv')):
            raise chess.engine.EngineError('Counterfactual score was not exact')
        spent=time.monotonic()-started
        scanner.profile['root_seconds']+=spent;scanner.profile['root_searches']+=1
        key='deep_root_seconds' if depth_search or nodes==scanner.config.deep_nodes else 'fast_root_seconds'
        scanner.profile[key]+=spent
        scanner.profile['policy_root_seconds']=scanner.profile.get('policy_root_seconds',0)+spent
        scanner.profile['policy_root_searches']=scanner.profile.get('policy_root_searches',0)+1
        values[uci]=score_cp(line,board.turn)
    check_deadline(scanner.deadline)
    contract={'mode':'depth' if depth_search else 'nodes',
              'requested':nodes.depth if depth_search else nodes,
              'engine':scanner.name,'completed':True,'exact':True}
    return ({'depth':nodes.depth,'scores':values,'search_contract':contract} if depth_search else
            {'nodes':nodes,'scores':values,'search_contract':contract})

def complete(games,nodes,deadline,*,executor=None,pool=None,scanner=None,fast=False,
             checkpoint=None,target=None):
    """Reuse the bounded pool. Drain all tasks; incomplete optional data cannot score."""
    from fairplay_maia import refresh_game,policy_evidence
    work=[(g,d) for g in games for d in g.decisions if d.human_policy]
    started=time.monotonic();completed=0;interrupted=False
    pending=[]
    for game,d in work:
        d.metrics.pop('policy_search',None)
        saved=(checkpoint.restore_counterfactual(target,game,d,
               'policy-fast' if fast else 'policy-deep',nodes)
               if checkpoint is not None else None)
        if saved is None:pending.append((game,d))
        else:
            d.metrics['policy_search']=saved
            completed+=1
    # Recording every counterfactual used to re-encrypt the entire growing
    # checkpoint. Buffer small batches, but always flush on exit/failure.
    buffered=0;last_flush=time.monotonic()
    def persist(game,d,result):
        nonlocal buffered,last_flush
        if checkpoint is None:return
        checkpoint.record_counterfactual(target,game,d,
            'policy-fast' if fast else 'policy-deep',result,persist=False)
        buffered+=1
        if buffered>=64 or time.monotonic()-last_flush>=15:
            checkpoint.flush(force=False)
            buffered=0;last_flush=time.monotonic()
    try:
        if executor is not None and pool is not None:
            max_workers=max(1,int(getattr(executor,'_max_workers',getattr(pool,'size',1))))
            capacity=min(64,max_workers*3)
            work_iter=iter(pending)
            futures={}
            def submit_available():
                while len(futures)<capacity:
                    try:game,d=next(work_iter)
                    except StopIteration:break
                    futures[executor.submit(pool._run_with_scanner,deadline,
                        lambda worker,d=d:search_alternatives(worker,d,nodes))]=(game,d)
            submit_available()
            while futures:
                finished,_=wait(tuple(futures),return_when=FIRST_COMPLETED)
                for future in finished:
                    game,d=futures.pop(future)
                    try:
                        result=future.result()
                        d.metrics['policy_search']=result;completed+=1
                        persist(game,d,result)
                    except Exception:
                        interrupted=True
                if interrupted:
                    for running in futures:running.cancel()
                else:submit_available()
        else:
            for game,d in pending:
                check_deadline(deadline)
                result=(pool._run_with_scanner(deadline,lambda worker:search_alternatives(worker,d,nodes))
                        if pool is not None else search_alternatives(scanner,d,nodes))
                d.metrics['policy_search']=result;completed+=1
                persist(game,d,result)
    except Exception:
        interrupted=True
        # Keep draining already-running searches before clearing incomplete data.
        if executor is not None and pool is not None:
            for running in futures:running.cancel()
            for running in futures:
                try:running.result()
                except Exception:pass
    finally:
        if checkpoint is not None and buffered:
            checkpoint.flush()
    if interrupted:
        for _,d in work:
            d.metrics.pop('policy_search',None);d.fast_policy={}
    for game in games:
        refresh_game(game)
        if fast:
            for d in game.decisions:
                d.fast_policy=policy_evidence(d,d.human_policy) if d.human_policy and not interrupted else {}
    return {'complete':not interrupted,'positions':completed,'seconds':time.monotonic()-started}

def game_summary(game,*,fast=False):
    from fairplay_maia import policy_evidence
    rows=[];stable=0
    for d in game.decisions:
        r=(getattr(d,'fast_policy',{}) if fast else
           policy_evidence(d,d.human_policy) if d.human_policy else None)
        if not r or not r.get('eligible') or r.get('difficulty',0)<POLICY.difficulty:continue
        if r.get('known_policy_mass',0)<POLICY.known_mass or not r.get('counterfactual_complete'):continue
        rows.append(r)
        old=getattr(d,'fast_policy',{}) or {}
        if (not fast and game.deep and old.get('eligible')
            and abs(r['observed_quality']-old.get('observed_quality',-1))<=.10
            and abs(r['expected_quality_upper_bound']-old.get('expected_quality_upper_bound',-1))<=.15
            # Policy search already checks observed quality and alternative
            # probabilities; paired Stockfish reliability must not require an
            # additional best-move hit or fast/deep difficulty agreement.
            and d.metrics.get('search_stability',{}).get('objective_quality_preserved')):stable+=1
    hits=sum(r['observed_quality']>=.85 and r['quality_excess_lower_bound']>=POLICY.move_excess for r in rows)
    signed=statistics.mean(r['signed_quality_excess'] for r in rows) if rows else 0
    info=statistics.mean(r['information'] for r in rows) if rows else 0
    contributor=(len(rows)>=POLICY.minimum_game_decisions and hits>=POLICY.game_hits
                 and signed>=POLICY.game_signed_excess and info>=POLICY.game_information)
    return {'positions':len(rows),'hits':hits,'signed_excess':signed,
            'information':info,'contributor':contributor,'stable':stable}

def summarize(games,*,fast=False):
    return combine([(g,game_summary(g,fast=fast)) for g in games])

def combine(rows):
    # A game contributes at most one equivalent. Sparse games contribute only
    # their measured fraction; dividing a sample into more games creates no
    # extra evidence. Signed misses remain in both weighted averages.
    usable=[(r,min(r['positions'],POLICY.game_decisions)/POLICY.game_decisions)
            for _,r in rows if r['positions']]
    weight=sum(w for _,w in usable)
    return {'games':len(rows),'opportunity_games':len(usable),
        'positions':sum(r['positions'] for _,r in rows),
        'effective_positions':weight*POLICY.game_decisions,
        'opportunity_weight':weight,
        'contributor_weight':sum(w for r,w in usable if r['contributor']),
        'contributors':sum(r['contributor'] for _,r in rows),
        'contributor_ids':[g.identity for g,r in rows if r['contributor']],
        'signed_excess':sum(r['signed_excess']*w for r,w in usable)/weight if weight else 0,
        'information':sum(r['information']*w for r,w in usable)/weight if weight else 0,
        'stable':sum(r['stable'] for _,r in rows)}


def blockers(s):
    required=max(POLICY.contributor_games,math.ceil(s['games']*POLICY.contributor_fraction))
    tests={
        'six replicated contributor games and 60% of the period':s['contributors']>=required,
        'six full contributor equivalents':s['contributor_weight']>=POLICY.contributor_games,
        'at least 24 game-capped comparable decisions':s['effective_positions']>=POLICY.decisions,
        'signed quality excess includes mistakes':s['signed_excess']>=POLICY.game_signed_excess,
        'bounded game-level information':s['information']>=POLICY.game_information}
    return [name for name,passed in tests.items() if not passed]


def periods(games, *, strict_original_sequence=False):
    """Fixed chronological blocks plus latest window; never ranked game sets."""
    output=[]
    summaries={id(g):game_summary(g,fast=True) for g in games}
    for kind in ('rapid','blitz'):  # Bullet model/domain uncertainty is larger.
        group=sorted([g for g in games if g.time_class==kind and g.rated is True],
                     key=lambda g:(g.ended,g.identity))
        seen=set()
        for width in (*POLICY.windows,len(group)):
            if width<POLICY.contributor_games or width>len(group):continue
            for offset in sorted(set([*range(0,len(group)-width+1,width),len(group)-width])):
                members=group[offset:offset+width]
                if strict_original_sequence and any(
                        a.time_control!=b.time_control or
                        getattr(a,'control_index',None) is None or
                        getattr(b,'control_index',None)!=a.control_index+1
                        for a,b in zip(members,members[1:])):
                    continue
                ids=tuple(g.identity for g in members)
                if ids in seen:continue
                seen.add(ids);s=combine([(g,summaries[id(g)]) for g in members])
                output.append({'ids':ids,'class':kind,'summary':s,'blockers':blockers(s)})
    return sorted(output,key=lambda row:(bool(row['blockers']),-row['summary']['signed_excess'],
                                         -row['summary']['contributors'],row['ids']))

def allocate(games,plan,config=CONFIG):
    """Preserve the original plan and controls; add only spare deep slots."""
    if config.deep_games==0 or {'human','neural'}&set(config.disabled_features):return plan
    candidate=next((row for row in periods(games) if not row['blockers']),None)
    if candidate is None:return plan
    selected=list(plan);members=[g for g in games if g.identity in candidate['ids']]
    from fairplay_sequence import coverage_members
    # Allocate by opportunity coverage, not successful moves. Six ordinary
    # games or twelve two-opportunity games request the same evidence budget.
    exposure=lambda g:min(game_summary(g,fast=True)['positions'],POLICY.game_decisions)/POLICY.game_decisions
    covered=sum(exposure(g) for g in selected if g in members)
    mean_exposure=sum(exposure(g) for g in members)/len(members)
    slots=min(len(members),math.ceil(POLICY.contributor_games/max(mean_exposure,.01)))
    anchors=coverage_members(members,slots)
    remaining=[g for g in coverage_members(members,len(members)) if g not in anchors]
    for game in anchors+remaining:
        if covered>=POLICY.contributor_games or len(selected)>=config.deep_max_games:break
        if game not in selected:
            selected.append(game);covered+=exposure(game)
    return selected

def integrate(result,games,config=CONFIG,*,strict_original_sequence=False):
    best=None
    best_ids=()
    deep_rows={id(g):game_summary(g) for g in games if g.deep}
    fast_rows={id(g):game_summary(g,fast=True) for g in games if g.deep}
    for candidate in periods(games,strict_original_sequence=strict_original_sequence):
        members=[g for g in games if g.identity in candidate['ids'] and g.deep]
        deep=combine([(g,deep_rows[id(g)]) for g in members]);paired=combine([(g,fast_rows[id(g)]) for g in members])
        reasons=list(candidate['blockers'])
        tests={
            'four full deep-confirmed contributor equivalents':deep['contributors']>=POLICY.deep_games and deep['contributor_weight']>=POLICY.deep_games,
            'sixteen game-capped deep opportunities':deep['effective_positions']>=POLICY.deep_decisions,
            'deep information retained':deep['information']>=max(POLICY.game_information,POLICY.retention*paired['information']),
            'deep signed excess retained':deep['signed_excess']>=POLICY.game_signed_excess,
            'stable paired quality and human alternatives':deep['stable']>=POLICY.deep_stability*deep['positions'],
            'primary engine coverage complete':result.coverage.get('primary_engine_complete',not result.partial),
            'human-reference feature enabled':not {'human','neural'}&set(config.disabled_features)}
        reasons += [name for name,passed in tests.items() if not passed]
        row={'passed':not reasons,'blockers':reasons,'fast':candidate['summary'],'deep':deep,
             'class':candidate['class'],'candidate_games':len(candidate['ids'])}
        if best is None or row['passed']:
            best=row
            best_ids=tuple(candidate['ids'])
        if row['passed']:break
    best=best or {'passed':False,'blockers':['No adequately covered chronological human-policy period.'],'candidate_games':0}
    from fairplay_evidence_audit import audit_maia_funnel
    result.diagnostics['maia_evidence_audit']=audit_maia_funnel(games,best_ids)
    result.diagnostics['learned_gameplay']=best
    result.diagnostics.setdefault('high_paths',{})['Learned human-policy HIGH']={
        k:best[k] for k in ('passed','blockers','candidate_games')}
    result.families['Learned Human Reference']='Elevated' if best['passed'] else 'Limited / not established'
    if best['passed'] and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
        result.priority='HIGH';result.deep_confirmed=True
        result.diagnostics.update(high_path='Distributed learned human-policy discrepancy',high_blocked=[])
        result.reasons=[
            'Move quality repeatedly exceeded a conservative local human-policy reference in competitive decisions across several games.',
            'The same decisions and human alternatives retained this discrepancy under deeper Stockfish review. '
            'This is one gameplay family, not an independent probability or proof of misconduct.']
    return result
