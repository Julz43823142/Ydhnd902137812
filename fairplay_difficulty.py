"""Continuous position value; legal-count alone never establishes difficulty."""
import math
import statistics
import chess
from fairplay_config import CONFIG


def clamp(value):return max(0.0,min(1.0,value))


def simple_threat_response(decision):
    """Straightforward flight of an attacked piece is not a quiet discovery."""
    if decision.capture or decision.check or decision.gives_check:return False
    try:
        board=chess.Board(decision.fen);move=chess.Move.from_uci(decision.move)
        piece=board.piece_at(move.from_square)
        values={chess.PAWN:1,chess.KNIGHT:3,chess.BISHOP:3,chess.ROOK:5,chess.QUEEN:9,chess.KING:0}
        if not piece or piece.color!=board.turn or values[piece.piece_type]<3 or not board.is_legal(move):return False
        enemy=not board.turn;opponent=board.copy(stack=False);opponent.turn=enemy
        threatened=any(values[board.piece_at(square).piece_type]<=values[piece.piece_type]
                       and opponent.is_legal(chess.Move(square,move.from_square))
                       for square in board.attackers(enemy,move.from_square))
        if not threatened:return False
        safe_destinations = set()
        for alternative in list(board.legal_moves):
            if alternative.from_square != move.from_square or board.is_capture(alternative):continue
            trial = board.copy(stack=False);trial.push(alternative)
            if not trial.is_check() and not trial.attackers(enemy,alternative.to_square):
                safe_destinations.add(alternative.to_square)
        board.push(move)
        # A unique defensive resource is not an automatic piece flight.
        return len(safe_destinations)>=3 and not bool(board.attackers(enemy,move.to_square))
    except (ValueError,TypeError):return False


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
    # Normalize against the established critical-position frontier. Requiring
    # twice that gap/spread here silently discarded quiet unique decisions
    # already considered meaningful by the legacy evidence model.
    separation=clamp(boundary/max(1,config.critical_gap))
    spread=clamp((m.get('spread') or 0)/max(1,config.critical_spread))
    quiet=not (decision.capture or decision.check or decision.gives_check)
    difficulty=choices*(.55*separation+.45*spread)*(1 if quiet else .75)
    # Many equivalent candidates make an engine match uninformative.
    difficulty*=1/max(1,good)**config.human_equivalence_power
    difficulty*=1 if m.get('competitive') else .5
    obvious_response=simple_threat_response(decision)
    if obvious_response:difficulty*=config.human_threat_response_factor
    m['simple_threat_response']=obvious_response
    m.update(difficulty=clamp(difficulty),plausible_good_moves=good,played_boundary_cp=boundary)


def stability(decision, config=CONFIG):
    fast = decision.fast_engine
    m = decision.metrics
    # A completed, exact depth-12 bullet search is just as valid a
    # paired-depth comparison as a completed depth-18 blitz/rapid search.
    # v22 implicitly required depth >=18 (or 320k nodes), leaving almost
    # every bullet position unconfirmed even when it reached depth 12.
    contract=m.get('search_contract') or {}
    request=contract.get('requested')
    search_depth=m.get('search_depth',0)
    contracted=(contract.get('mode')=='depth'
                and contract.get('completed') is True
                and contract.get('exact') is True
                and isinstance(request,int) and not isinstance(request,bool)
                and request in (config.bullet_deep_depth,18)
                and isinstance(search_depth,int) and search_depth>=request)
    # Compatibility for independently tested legacy fixed-node policies.
    legacy=(not contract and m.get('nodes',0)>=config.deep_nodes)
    compared=bool((contracted or legacy)
                  and fast.get('nodes',0)==config.fast_nodes
                  and not m.get('search_inconsistent')
                  and not fast.get('search_inconsistent')
                  and (not fast.get('search_contract')
                       or (fast['search_contract'].get('completed') is True
                           and fast['search_contract'].get('exact') is True)))
    rank, old = m.get('rank'), fast.get('rank')
    best = compared and m.get('best')==fast.get('best')
    rank_ok = compared and rank is not None and old is not None and abs(rank-old)<=1
    cpl_ok = compared and abs(m.get('cpl', 1000)-fast.get('cpl', 1000))<=30
    # Exact candidate identity is diagnostic. Equivalent #1/#2/#3 permutations
    # preserve semantic quality, including an actual move evaluated at the root.
    near = (compared and m.get('cpl', 1000)<=config.equivalent_cp
            and fast.get('cpl', 1000)<=config.equivalent_cp
            and not m.get('search_inconsistent') and not fast.get('search_inconsistent'))
    scaled_ok = (compared and m.get('scaled_loss') is not None and fast.get('scaled_loss') is not None
                 and abs(m['scaled_loss']-fast['scaled_loss'])<=.035)
    gap_ok = (compared and abs((m.get('played_boundary_cp') or 0)-(fast.get('played_boundary_cp') or 0))
              <=max(50, (fast.get('played_boundary_cp') or 0)*.5))
    # Both passes must retain evidential geometry; equivalent best choices do
    # not rescue a position that becomes easy at deeper search.
    geometry = (compared and m.get('difficulty', 0)>=config.human_difficulty_floor
                and fast.get('difficulty', 0)>=config.human_difficulty_floor
                and m.get('competitive') and fast.get('competitive'))
    semantic = bool(near and cpl_ok and scaled_ok and geometry)
    exact = bool(compared and rank_ok and cpl_ok and gap_ok and (best or near) and geometry)
    stable = semantic or exact
    # Separate move-quality agreement from the stricter requirement that the
    # position remains evidentially difficult at *both* search budgets. Neither
    # diagnostic changes the established HIGH/LOW stability decision.
    objective_quality_preserved = bool(compared and cpl_ok and scaled_ok)
    evidential_geometry_preserved = bool(compared and geometry)
    # Independent semantic quality check; not an extra scoring family.
    from fairplay_evidence_audit import SearchObservation, compare_search_quality
    def observation(snapshot):
        contract=snapshot.get('search_contract') or {}
        return SearchObservation(
            position_key=(0,decision.ply),engine_key=str(contract.get('engine') or ''),
            budget_mode=contract.get('mode') or 'nodes',
            budget_value=int(contract.get('requested') or 0),
            completed=bool(contract.get('completed')),
            achieved_depth=snapshot.get('search_depth'),
            exact_scores=bool(contract.get('exact')),
            cpl=snapshot.get('cpl'),scaled_loss=snapshot.get('scaled_loss'),
            played_rank=snapshot.get('rank'),best_move=snapshot.get('best'))
    comparison=compare_search_quality(observation(fast),observation(m),
        near_best_cp=config.equivalent_cp,cp_tolerance=30,scaled_tolerance=.035)
    m['semantic_search_check']={
        'state':comparison.quality.state.value,'reason':comparison.quality.reason.value,
        'same_rank':comparison.same_rank,'same_best_move':comparison.same_best_move,
        'near_best_preserved':comparison.near_best_preserved}
    # Separate missing contract coverage from genuinely unstable geometry:
    # neither should silently become evidence of fair play.
    blocked=([] if stable else
             (['unverified_depth_or_fast_contract'] if not compared else
              [name for name,ok in (
                  ('candidate_quality_changed',bool(cpl_ok and scaled_ok)),
                  ('evidential_geometry_changed',bool(geometry)),
                  ('rank_or_best_changed',bool(rank_ok or near)),
                  ('played_move_no_longer_equivalent',bool(near or exact))
              ) if not ok]))
    m['search_stability'] = {'compared':bool(compared), 'best':bool(best), 'rank':bool(rank_ok),
        'cpl':bool(cpl_ok), 'gap':bool(gap_ok), 'semantic_quality':semantic, 'stable':stable,
        'objective_quality_preserved':objective_quality_preserved,
        'evidential_geometry_preserved':evidential_geometry_preserved,
        'paired_depth':request if contracted else None,
        'blockers':blocked}
    return stable


def loss_summary(rows):
    values=sorted(m['scaled_loss'] for m in rows if m.get('scaled_loss') is not None)
    return {'median':statistics.median(values) if values else None,
        'trimmed_mean':statistics.mean(values[:max(1,math.ceil(len(values)*.9))]) if values else None,
        'p90':values[min(len(values)-1,int((len(values)-1)*.9))] if values else None}
