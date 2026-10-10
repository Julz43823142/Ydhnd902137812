"""Optional local Maia-3 policy reference, independent of case labels.

A move-policy probability is NOT a misconduct probability. Objective quality
and policy rarity belong to the SAME gameplay family. This first integration
adds bounded human-alternative comparison and paired deep confirmation.
Unknown candidate quality is conservatively treated as perfect when
computing an upper bound on expected quality.
"""
import hashlib
import json
import math
import os
import select
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

import chess

MODEL_REVISION = 'b6559de2398d7140b985f28fd2c19fb5e47ddabe'
MODEL_SHA256 = 'ba14208b2992d85502f5fb501934abf6aaaeb355e9f3fdf90e326911f562524f'
SOURCE_REVISION = '1e13597c42d4858b7cfd7cfdae01e297263364b2'
MODEL_NAME = 'Maia-3 5M local policy'
MAX_POSITIONS = 800
_cache = OrderedDict()
_worker = None
_lock = threading.Lock()


def quality(loss_cp, scaled_loss):
    return max(0.0, min(1.0, 1-max(0,loss_cp)/150, 1-max(0,scaled_loss)/.20))


def valid_policy(decision, probabilities):
    if not isinstance(probabilities,dict):return False
    legal={m.uci() for m in chess.Board(decision.fen).legal_moves}
    return (set(probabilities)==legal
            and all(isinstance(v,(int,float)) and math.isfinite(v) and v>=0 for v in probabilities.values())
            and .999<=sum(probabilities.values())<=1.001)


def policy_evidence(decision, probabilities):
    if not valid_policy(decision,probabilities):return None
    legal=set(probabilities);total=sum(probabilities.values())
    probs={move:value/total for move,value in probabilities.items()}
    m=decision.metrics
    candidates=m.get('candidates',[])
    values=m.get('candidate_cp',[])
    if not values or len(candidates)!=len(values) or m.get('search_inconsistent'):return None
    best=values[0]
    from fairplay_policy import POLICY
    searched=dict(zip(candidates,values))
    if 'actual_cp' in m:searched.setdefault(decision.move,m['actual_cp'])
    extra=m.get('policy_search',{})
    complete=(extra.get('depth')==m.get('search_depth') if m.get('search_depth') else
              extra.get('nodes')==m.get('nodes') and 'nodes' in m)
    objective=m.get('search_contract')
    alternative=extra.get('search_contract')
    if complete and (objective or alternative):
        # Same-position budget and exact-score comparisons are necessary;
        # counterfactual searches can still have root-search calibration drift.
        from fairplay_evidence_audit import SearchObservation, State, check_counterfactual_contract
        if not (objective and alternative):
            complete=False
        else:
            def observation(contract, achieved_depth):
                return SearchObservation(
                    position_key=(0,int(decision.ply)),engine_key=str(contract.get('engine') or ''),
                    budget_mode=str(contract.get('mode') or ''),
                    budget_value=int(contract.get('requested') or 0),
                    completed=bool(contract.get('completed')),achieved_depth=achieved_depth,
                    exact_scores=bool(contract.get('exact')))
            complete=(check_counterfactual_contract(
                observation(objective,m.get('search_depth')),
                observation(alternative,extra.get('depth'))
            ).state is State.PASS)
    if complete:searched.update(extra.get('scores',{}))
    if any(move not in legal or not isinstance(value,(int,float)) or not math.isfinite(value)
           for move,value in searched.items()):return None
    # An alternative overturning the original search invalidates this comparison.
    if max(searched.values())>best+20:return None
    best=max(best,max(searched.values()))
    def scale(cp):return 1/(1+math.exp(-.00368208*max(-10000,min(10000,cp))))
    # No assumption that an unsearched move is bad: residual probability mass
    # receives quality 1.0. This bound can only reduce anomaly evidence.
    expected_upper=1.0
    for move,value in searched.items():
        expected_upper-=probs.get(move,0)*(1-quality(best-value,scale(best)-scale(value)))
    expected_upper=min(1.0,max(0.0,expected_upper)+POLICY.quality_margin)
    known_mass=sum(probs.get(move,0) for move in searched)
    observed=(quality(best-m['actual_cp'],scale(best)-scale(m['actual_cp'])) if 'actual_cp' in m
              else quality(m.get('cpl',1000),m.get('scaled_loss',1)))
    excess=max(0.0,observed-expected_upper)
    near_mass=sum(probs.get(move,0) for move,value in searched.items() if best-value<=15)
    played=probs.get(decision.move,0)
    useful=bool(m.get('competitive') and m.get('useful') and not decision.forced
                 and not decision.trivial_kind and decision.phase!='opening'
                 and not m.get('post_opponent_error') and not m.get('easy_conversion')
                 and not m.get('simple_threat_response') and not m.get('automatic_material_gain'))
    info=excess*m.get('difficulty',0) if useful else 0.0
    return {'model':MODEL_NAME,'played_move_probability':played,
        'played_move_rank':1+sum(v>played for v in probs.values()),
        'near_best_policy_mass':near_mass,'expected_quality_upper_bound':max(0,expected_upper),
        'observed_quality':observed,'quality_excess_lower_bound':excess,
        'signed_quality_excess':observed-expected_upper,'known_policy_mass':known_mass,
        'difficulty':m.get('difficulty',0),'counterfactual_complete':complete,
        'unsearched_mass':max(0.0,1-known_mass),'quality_uncertainty_margin':POLICY.quality_margin,
        'information':info,'eligible':useful,
        'surprisal_nats':min(8.0,-math.log(max(played,math.exp(-8)))),
        'note':'Model move likelihood, not a cheating probability; cross-platform calibration is incomplete.'}


def refresh_game(game):
    rows=[]
    for decision in game.decisions:
        if decision.human_policy:
            row=policy_evidence(decision,decision.human_policy)
            if row:rows.append(row)
    useful=[r for r in rows if r['eligible']]
    game.human_reference={'model':MODEL_NAME,'positions':len(rows),'eligible':len(useful),
        'information':sum(r['information'] for r in useful)/len(useful) if useful else 0.0,
        'quality_excess_lower_bound':sum(r['quality_excess_lower_bound'] for r in useful)/len(useful) if useful else 0.0,
        'low_policy_strong_moves':sum(r['played_move_probability']<=.05 and r['information']>=.10 for r in useful),
        'model_unexpected_moves':sum(r['played_move_probability']<=.05 for r in useful)}
    return game.human_reference



def carry_policy_after_deep(source, confirmed):
    """Re-evaluate this scan's policies with deep metrics, including cached games.

    Cached decisions can predate model availability or use a different sampled
    subset. They must never restore stale policy observations into a new scan.
    """
    # A complete 500-game review may hold tens of thousands of decisions.
    # Index both fast and human policies once instead of scanning the source
    # game's moves for each confirmed move (formerly quadratic per game).
    keyed={(d.ply,d.fen,d.move):d for d in source.decisions}
    policies={key:d.human_policy for key,d in keyed.items() if d.human_policy}
    for decision in confirmed.decisions:
        key=(decision.ply,decision.fen,decision.move)
        matched=keyed.get(key)
        decision.human_policy=dict(policies.get(key,{}))
        decision.fast_policy=dict(getattr(matched,'fast_policy',{}) or {}) if matched is not None else {}
        decision.metrics.pop('policy_search',None)
    if policies:refresh_game(confirmed)
    else:confirmed.human_reference={}


def selection(games,*,full_coverage=False,recent_full_ids=None):
    # In latest-fifty exhaustive mode, Maia policy is evaluated for EVERY
    # legally replayable player decision in that scope, including early and
    # weak moves, not just geometry-ranked or engine-matching decisions.
    # Those positions are NOT subject to the historical 1600-sample cap.
    # Older comparison games KEEP their independent former bounded sample;
    # the latest fifty may not crowd out the baseline evidence used to
    # discover a recent regime change. The combined count can exceed 1600.
    if recent_full_ids and not full_coverage:
        wanted=set(recent_full_ids)
        recent=[g for g in games if g.identity in wanted]
        older=[g for g in games if g.identity not in wanted]
        complete=selection(recent,full_coverage=True)
        sampled=selection(older,full_coverage=False)
        return complete+sampled
    # Compare successes and misses: selection uses position geometry, never
    # CPL, top-1, high-information hits, account names or validation labels.
    eligible=[g for g in games if g.moves and g.rating is not None and g.opponent_rating is not None]
    # The distributed depth workers run concurrently with local Maia inference.
    # A slightly wider per-game geometry sample reduces blind spots in short
    # intermittent periods. It never selects positions by played-move quality.
    budget=(1600 if os.getenv('FAIRPLAY_DISTRIBUTED')=='1' else MAX_POSITIONS)
    quota=(None if full_coverage else max(1,min(16,budget//max(1,len(eligible)))))
    selected=[]
    for game in eligible:
        choices=(list(game.decisions) if full_coverage else
                 [d for d in game.decisions if d.metrics.get('useful') and d.metrics.get('competitive')
                  and not d.metrics.get('post_opponent_error') and not d.metrics.get('easy_conversion') and d.phase!='opening'])
        # Position geometry only: no played-move rank, CPL or success selection.
        ordered=sorted(choices,key=lambda d:(d.metrics.get('spread') or 0,d.ply))
        if quota is not None and len(ordered)>quota:
            ordered=[ordered[round(i*(len(ordered)-1)/(quota-1))] for i in range(quota)] if quota>1 else ordered[-1:]
        wanted={d.ply:d for d in ordered}
        board=chess.Board();history=deque([board.fen()],maxlen=8)
        for ply,uci in enumerate(game.moves,1):
            if ply in wanted:
                d=wanted[ply]
                if d.fen==board.fen():
                    selected.append((game,d,{'history':list(history),'rating':game.rating,'opponent_rating':game.opponent_rating}))
            move=chess.Move.from_uci(uci)
            if move not in board.legal_moves:break
            board.push(move);history.append(board.fen())
    return selected if full_coverage else selected[:budget]


class LocalPolicyWorker:
    def __init__(self,checkpoint):
        self.buffer=b''
        env={key:value for key,value in os.environ.items() if key in {
            'PATH','HOME','LANG','LC_ALL','LD_LIBRARY_PATH','PYTHONPATH','VIRTUAL_ENV','SYSTEMROOT','TMPDIR'}}
        env.update(OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',
                   HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).parent/'scripts/fairplay_maia_worker.py'),str(checkpoint)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,env=env)
        os.set_blocking(self.process.stdin.fileno(),False)
        try:
            if self.read(time.monotonic()+30).get('ready')!=MODEL_SHA256:raise ValueError('Model handshake failed')
        except Exception:
            self.close();raise

    def read(self,deadline):
        while b'\n' not in self.buffer:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Local policy deadline exceeded')
            ready,_,_=select.select([self.process.stdout],[],[],remaining)
            if not ready:raise TimeoutError('Local policy deadline exceeded')
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:raise RuntimeError('Local policy worker ended')
            self.buffer+=chunk
            if len(self.buffer)>4*1024*1024:raise ValueError('Local policy output too large')
        line,self.buffer=self.buffer.split(b'\n',1)
        return json.loads(line)

    def predict(self,items):
        deadline=time.monotonic()+20
        data=memoryview((json.dumps({'positions':items},separators=(',',':'))+'\n').encode())
        while data:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Local policy write deadline exceeded')
            _,ready,_=select.select([],[self.process.stdin],[],remaining)
            if not ready:raise TimeoutError('Local policy write deadline exceeded')
            try:written=os.write(self.process.stdin.fileno(),data)
            except BlockingIOError:continue
            data=data[written:]
        reply=self.read(deadline)
        rows=reply.get('policies')
        if not isinstance(rows,list) or len(rows)!=len(items):raise ValueError('Incomplete local policy batch')
        return rows

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        for stream in (self.process.stdin,self.process.stdout):
            if stream:stream.close()


def close_worker():
    global _worker
    with _lock:
        if _worker is not None:_worker.close()
        _worker=None


def warm_worker():
    """Load once during bot startup, never download or block the Discord loop."""
    global _worker
    checkpoint=os.environ.get('FAIRPLAY_MAIA_CHECKPOINT')
    if not checkpoint or not Path(checkpoint).is_file():return False
    with _lock:
        if _worker is None:_worker=LocalPolicyWorker(checkpoint)
    return True


def annotate_history(games,predictor=None,*,full_coverage=False,recent_full_ids=None,checkpoint=None,target=None):
    global _worker
    start=time.monotonic()
    for game in games:
        game.human_reference={}
        for decision in game.decisions:
            decision.human_policy={};decision.fast_policy={}
            decision.metrics.pop('policy_search',None)
    model_path=os.environ.get('FAIRPLAY_MAIA_CHECKPOINT')
    owns_worker=predictor is None
    if predictor is None and (not model_path or not Path(model_path).is_file()):
        return {'available':False,'positions':0,'reason':'Local Maia checkpoint is not installed; Stockfish and the explicit heuristic remain active.'}
    recent_set=set(recent_full_ids or ())
    recent_games=[g for g in games if recent_set
                  and getattr(g,'identity',None) in recent_set]
    # Maia is rating-conditioned; a missing rating cannot be silently
    # imputed. Even the all-position requirement is explicitly limited to
    # causally replayable games with both public ratings.
    rating_complete=[g for g in recent_games
                     if g.moves and g.rating is not None
                     and g.opponent_rating is not None]
    expected_recent=sum(len(g.decisions) for g in rating_complete)
    chosen=(selection(games,full_coverage=full_coverage,
                      recent_full_ids=recent_full_ids)
            if recent_set else selection(games,full_coverage=full_coverage))
    actual_recent=sum(getattr(g,'identity',None) in recent_set
                      for g,d,ctx in chosen)
    coverage={
        'recent_full_games_requested':len(recent_games),
        'recent_full_games_rating_eligible':len(rating_complete),
        'recent_full_positions_expected':expected_recent,
        'recent_full_positions_selected':actual_recent,
        'recent_full_positions_completed':0,
        'recent_full_missing_rating_games':len(recent_games)-len(rating_complete),
        'recent_full_selection_complete':len(rating_complete)==len(recent_games)
            and actual_recent==expected_recent,
        # Selection is NOT completed inference. Set true only once *all*
        # validated policies are populated; a failed later batch stays false.
        'recent_full_complete':False,
        'recent_full_all_decisions_including_openings':bool(recent_set),
        'older_model_positions_sampled':len(chosen)-actual_recent,
    }
    if recent_set and not coverage['recent_full_selection_complete']:
        return {
            'available':False,'positions':0,**coverage,
            'reason':'Recent 50 Maia full-position coverage is incomplete: missing public ratings or legal move history. No partial Maia reference may influence HIGH.'}
    if not chosen:return {'available':False,'positions':0,**coverage,
        'reason':'No positions with verified causal history and both ratings.'}
    try:
        with _lock:
            if predictor is None:
                if _worker is None:_worker=LocalPolicyWorker(model_path)
                predictor=_worker.predict
            keys=[hashlib.sha256((MODEL_SHA256+json.dumps(item,sort_keys=True)).encode()).hexdigest() for _,_,item in chosen]
            if checkpoint is not None:
                for (game,decision,_),key in zip(chosen,keys):
                    saved=checkpoint.policy(target,game,decision)
                    if saved is not None and valid_policy(decision,saved):
                        _cache[key]=saved
            missing=[i for i,key in enumerate(keys) if key not in _cache]
            if missing:
                # Bound each CPU-inference request and IPC payload. A full
                # 100-game review may contain thousands of useful positions.
                for offset in range(0,len(missing),64):
                    group=missing[offset:offset+64]
                    policies=predictor([chosen[i][2] for i in group])
                    if len(policies)!=len(group):raise ValueError('Incomplete local policies')
                    for i,policy in zip(group,policies):
                        if not valid_policy(chosen[i][1],policy):
                            raise ValueError('Invalid local policy distribution')
                        _cache[keys[i]]=policy
                    if checkpoint is not None:
                        for i in group:chosen[i][1].human_policy=dict(_cache[keys[i]])
                        checkpoint.record_policies(target,[(chosen[i][0],chosen[i][1]) for i in group])
            for (game,decision,_),key in zip(chosen,keys):
                decision.human_policy=dict(_cache[key]);_cache.move_to_end(key)
            if checkpoint is not None:
                checkpoint.record_policies(target,[(game,decision) for game,decision,_ in chosen])
            while len(_cache)>2400:_cache.popitem(last=False)
        for game in games:refresh_game(game)
        return {'available':True,'model':MODEL_NAME,'positions':len(chosen),
            **coverage,
            'recent_full_positions_completed':actual_recent,
            'recent_full_complete':bool(recent_set and coverage['recent_full_selection_complete']),
            'games':sum(bool(g.human_reference.get('positions')) for g in games),
            'cache_hits':len(chosen)-len(missing),'seconds':time.monotonic()-start,
            'role':'Learned human-policy comparison with Stockfish counterfactuals and paired deep confirmation; not a calibrated misconduct probability.'}
    except Exception as error:
        # A failed *later* 64-position batch can leave earlier policies on
        # decisions. Do not allow partial model evidence to influence scoring
        # when the declared Maia reference is unavailable.
        for game in games:
            game.human_reference={}
            for decision in game.decisions:
                decision.human_policy={}
                decision.fast_policy={}
                decision.metrics.pop('policy_search',None)
        if owns_worker and _worker is not None:close_worker()
        return {'available':False,'positions':0,**coverage,
                'seconds':time.monotonic()-start,'failure_kind':type(error).__name__,
                'reason':'Local human reference unavailable; Stockfish review completed without invented model results.'}


def confirmation_pair(games):
    """A contiguous replicated policy anomaly may earn extra deep scrutiny.

    This only allocates full-game confirmation, including all mistakes. It does
    not change priority thresholds or treat neural and engine evidence as
    independent families.
    """
    options=[]
    for kind in ('rapid','blitz'):
        group=sorted([g for g in games if g.time_class==kind],key=lambda g:(g.ended,g.identity))
        for a,b in zip(group,group[1:]):
            # A neighboring row in a broad time class is not necessarily
            # the next rated game at the *same* exact clock control.
            if (getattr(a,'time_control',None)!=getattr(b,'time_control',None)
                    or getattr(a,'rated',True) is not True or getattr(b,'rated',True) is not True
                    or getattr(a,'probe_only',False) or getattr(b,'probe_only',False)
                    or (getattr(a,'control_index',None) is not None
                        and getattr(b,'control_index',None) is not None
                        and b.control_index!=a.control_index+1)):
                continue
            refs=[a.human_reference,b.human_reference]
            if all(r.get('eligible',0)>=3 and r.get('low_policy_strong_moves',0)>=2 for r in refs):
                options.append((sum(r.get('information',0) for r in refs),a.ended,[a,b]))
    return max(options,key=lambda row:(row[0],row[1]))[2] if options else []
